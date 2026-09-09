"""Staged dealer payments, invoice allocation, and account statements."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from PySide6.QtCore import QThreadPool
from pytestqt.qtbot import QtBot
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.dealer import Dealer
from app.models.enums import PaymentDirection, PaymentMethod, PaymentStatus, UserRole
from app.models.inventory import StockBatch
from app.models.payment import Payment
from app.models.product import Product
from app.models.sale import Sale
from app.models.supplier import Supplier
from app.security.authentication import AuthenticatedUser
from app.services.catalog_service import DealerService
from app.services.dealer_account_service import INVOICE, PAYMENT, DealerAccountService
from app.services.dto import (
    CreatePesticideSaleCommand,
    CreateStockPurchaseCommand,
    PesticideSaleLineInput,
    PurchasedBatchInput,
)
from app.services.pesticide_sale_service import PesticideSaleService
from app.services.stock_purchase_service import StockPurchaseService
from app.utils.exceptions import NotFoundError, PermissionDeniedError, ValidationError


def _batch(
    session: Session,
    actor: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    *,
    batch_number: str,
    quantity: int = 100,
) -> StockBatch:
    purchase = StockPurchaseService(session).create(
        CreateStockPurchaseCommand(
            supplier_id=supplier.id,
            batches=(
                PurchasedBatchInput(
                    product_id=product.id,
                    batch_number=batch_number,
                    quantity=quantity,
                    purchase_price=Decimal("100.00"),
                    selling_price=Decimal("150.00"),
                    manufacture_date=date(2025, 1, 1),
                    expiry_date=date(2099, 1, 1),
                ),
            ),
            payments=(),
        ),
        actor,
    )
    session.flush()
    batch = session.scalar(select(StockBatch).where(StockBatch.purchase_id == purchase.id))
    assert batch is not None
    return batch


def _dealer(session: Session, owner: AuthenticatedUser) -> Dealer:
    dealer = DealerService(session).create(
        actor=owner,
        name="Credit Dealer",
        phone="03001234567",
        credit_limit=Decimal("100000.00"),
    )
    session.flush()
    return dealer


def _credit_sale(
    session: Session,
    owner: AuthenticatedUser,
    dealer: Dealer,
    batch: StockBatch,
    *,
    quantity: int,
    days_ago: int = 0,
) -> Sale:
    """Raise an unpaid dealer invoice for ``quantity`` units at 150.00 each."""

    from datetime import UTC, datetime

    sale = PesticideSaleService(session).create(
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(batch.id, quantity),),
            payments=(),
            dealer_id=dealer.id,
            sale_date=datetime.now(UTC) - timedelta(days=days_ago),
        ),
        owner,
    )
    session.flush()
    return sale


def test_a_payment_settles_the_oldest_invoice_first(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    dealer = _dealer(db_session, owner)
    batch = _batch(db_session, owner, supplier, product, batch_number="STAGED")
    older = _credit_sale(db_session, owner, dealer, batch, quantity=10, days_ago=3)
    newer = _credit_sale(db_session, owner, dealer, batch, quantity=10, days_ago=1)
    assert dealer.balance == Decimal("3000.00")

    result = DealerAccountService(db_session).record_payment(
        dealer.id,
        actor=owner,
        method=PaymentMethod.CASH,
        amount=Decimal("2000.00"),
        reference="CHQ-001",
    )
    db_session.flush()

    assert result.amount == Decimal("2000.00")
    assert result.credited == Decimal("0.00")
    assert result.balance == Decimal("1000.00")
    assert [entry.invoice_number for entry in result.settled] == [
        older.invoice_number,
        newer.invoice_number,
    ]
    assert older.remaining_amount == Decimal("0.00")
    assert older.payment_status is PaymentStatus.PAID
    assert newer.remaining_amount == Decimal("1000.00")
    assert newer.payment_status is PaymentStatus.PARTIALLY_PAID
    assert dealer.balance == Decimal("1000.00")


def test_a_partial_payment_leaves_the_rest_outstanding(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    dealer = _dealer(db_session, owner)
    batch = _batch(db_session, owner, supplier, product, batch_number="PARTIAL")
    sale = _credit_sale(db_session, owner, dealer, batch, quantity=10)

    service = DealerAccountService(db_session)
    for instalment in (Decimal("500.00"), Decimal("400.00")):
        service.record_payment(dealer.id, actor=owner, method=PaymentMethod.CASH, amount=instalment)
    db_session.flush()

    assert sale.paid_amount == Decimal("900.00")
    assert sale.remaining_amount == Decimal("600.00")
    assert sale.payment_status is PaymentStatus.PARTIALLY_PAID
    assert dealer.balance == Decimal("600.00")


def test_an_overpayment_is_held_as_account_credit(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    """Paying more than is owed leaves credit on the account, not on an invoice."""

    dealer = _dealer(db_session, owner)
    batch = _batch(db_session, owner, supplier, product, batch_number="ADVANCE")
    sale = _credit_sale(db_session, owner, dealer, batch, quantity=10)

    result = DealerAccountService(db_session).record_payment(
        dealer.id, actor=owner, method=PaymentMethod.BANK_TRANSFER, amount=Decimal("2000.00")
    )
    db_session.flush()

    assert sale.remaining_amount == Decimal("0.00")
    assert result.credited == Decimal("500.00")
    assert result.balance == Decimal("-500.00")
    credit = db_session.scalars(
        select(Payment).where(Payment.dealer_id == dealer.id, Payment.sale_id.is_(None))
    ).all()
    assert len(credit) == 1
    assert credit[0].amount == Decimal("500.00")
    assert credit[0].is_account_credit is True


def test_a_payment_from_a_dealer_with_no_invoices_is_all_credit(
    db_session: Session, owner: AuthenticatedUser
) -> None:
    dealer = _dealer(db_session, owner)
    result = DealerAccountService(db_session).record_payment(
        dealer.id, actor=owner, method=PaymentMethod.CASH, amount=Decimal("750.00")
    )
    db_session.flush()
    assert result.settled == ()
    assert result.credited == Decimal("750.00")
    assert dealer.balance == Decimal("-750.00")


def test_payment_amount_must_be_positive(db_session: Session, owner: AuthenticatedUser) -> None:
    dealer = _dealer(db_session, owner)
    with pytest.raises(ValidationError, match="greater than zero"):
        DealerAccountService(db_session).record_payment(
            dealer.id, actor=owner, method=PaymentMethod.CASH, amount=Decimal("0.00")
        )


def test_payment_requires_a_known_dealer(db_session: Session, owner: AuthenticatedUser) -> None:
    import uuid

    with pytest.raises(NotFoundError):
        DealerAccountService(db_session).record_payment(
            uuid.uuid4(), actor=owner, method=PaymentMethod.CASH, amount=Decimal("10.00")
        )


def test_recording_a_payment_needs_dealer_permission(
    db_session: Session, owner: AuthenticatedUser
) -> None:
    dealer = _dealer(db_session, owner)
    inventory_manager = AuthenticatedUser(
        id=owner.id,
        username="stock",
        full_name="Stock Keeper",
        email="stock@test.invalid",
        role=UserRole.INVENTORY_MANAGER,
        must_change_password=False,
    )
    with pytest.raises(PermissionDeniedError):
        DealerAccountService(db_session).record_payment(
            dealer.id,
            actor=inventory_manager,
            method=PaymentMethod.CASH,
            amount=Decimal("10.00"),
        )


def test_statement_lists_every_charge_and_payment_with_a_running_balance(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    dealer = _dealer(db_session, owner)
    batch = _batch(db_session, owner, supplier, product, batch_number="LEDGER")
    first = _credit_sale(db_session, owner, dealer, batch, quantity=10, days_ago=5)
    second = _credit_sale(db_session, owner, dealer, batch, quantity=4, days_ago=2)
    DealerAccountService(db_session).record_payment(
        dealer.id, actor=owner, method=PaymentMethod.CASH, amount=Decimal("1800.00")
    )
    db_session.flush()

    statement = DealerAccountService(db_session).statement(dealer.id)
    assert statement.dealer_name == dealer.display_name
    assert statement.invoiced == first.total + second.total
    assert statement.paid == Decimal("1800.00")
    assert statement.outstanding == Decimal("300.00")
    assert statement.credit_limit == Decimal("100000.00")
    assert statement.available_credit == Decimal("99700.00")

    kinds = [entry.kind for entry in statement.entries]
    assert kinds[:2] == [INVOICE, INVOICE]
    assert PAYMENT in kinds
    # The running balance ends at what the dealer still owes.
    assert statement.entries[-1].balance == Decimal("300.00")
    assert dealer.balance == Decimal("300.00")


def test_statement_available_credit_is_absent_without_a_limit(
    db_session: Session, owner: AuthenticatedUser
) -> None:
    dealer = DealerService(db_session).create(actor=owner, name="No Limit", phone="03009999999")
    db_session.flush()
    statement = DealerAccountService(db_session).statement(dealer.id)
    assert statement.credit_limit == Decimal("0.00")
    assert statement.available_credit is None
    assert statement.entries == ()


def test_counter_payments_are_stamped_with_the_dealer(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    """A sale's own payment must appear on the dealer statement too."""

    from app.services.dto import PaymentInput

    dealer = _dealer(db_session, owner)
    batch = _batch(db_session, owner, supplier, product, batch_number="COUNTER")
    PesticideSaleService(db_session).create(
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(batch.id, 4),),
            payments=(PaymentInput(PaymentMethod.CASH, Decimal("300.00")),),
            dealer_id=dealer.id,
        ),
        owner,
    )
    db_session.flush()
    payment = db_session.scalars(
        select(Payment).where(Payment.direction == PaymentDirection.INCOMING)
    ).one()
    assert payment.dealer_id == dealer.id
    statement = DealerAccountService(db_session).statement(dealer.id)
    assert statement.paid == Decimal("300.00")
    assert statement.outstanding == Decimal("300.00")


@pytest.mark.ui
def test_sales_screen_offers_the_right_shortcut_for_the_recipient_type(
    qtbot: QtBot, database_engine: Engine, owner: AuthenticatedUser
) -> None:
    """Choosing Dealer must not still offer to add a walk-in customer."""

    from app.config.settings import get_settings
    from app.ui.sales.screen import SalesScreen

    factory = sessionmaker[Session](bind=database_engine, expire_on_commit=False, autoflush=False)
    screen = SalesScreen(factory, owner, get_settings())
    qtbot.addWidget(screen)
    assert screen.recipient_type.currentText() == "Customer"
    assert screen.add_recipient.text() == "+ Add walk-in customer"
    screen.recipient_type.setCurrentText("Dealer")
    assert screen.add_recipient.text() == "+ Add dealer"
    screen.recipient_type.setCurrentText("Customer")
    assert screen.add_recipient.text() == "+ Add walk-in customer"
    QThreadPool.globalInstance().waitForDone(5000)


@pytest.mark.ui
def test_dealers_screen_shows_advance_credit_as_credit(
    qtbot: QtBot, database_engine: Engine, owner: AuthenticatedUser
) -> None:
    from app.config.settings import get_settings
    from app.ui.dealers.screen import DealersScreen

    factory = sessionmaker[Session](bind=database_engine, expire_on_commit=False, autoflush=False)
    screen = DealersScreen(factory, owner, get_settings())
    qtbot.addWidget(screen)
    assert screen._balance_text(Decimal("1250.00")) == "PKR 1,250.00"
    assert screen._balance_text(Decimal("0.00")) == "PKR 0.00"
    assert screen._balance_text(Decimal("-500.00")) == "PKR 500.00 credit"
    QThreadPool.globalInstance().waitForDone(5000)


@pytest.mark.ui
def test_payment_dialog_returns_a_real_enum_member(qtbot: QtBot) -> None:
    """Qt stores a StrEnum as a plain string, so it must be converted back.

    Recording a dealer payment crashed with
    ``AttributeError: 'str' object has no attribute 'value'`` because the dialog
    handed the service a bare string.
    """

    from app.ui.dealers.screen import DealerPaymentDialog

    dialog = DealerPaymentDialog("Malik Agri Traders", Decimal("11250.00"), "PKR")
    qtbot.addWidget(dialog)
    assert dialog.method.count() == len(list(PaymentMethod))
    for index in range(dialog.method.count()):
        dialog.method.setCurrentIndex(index)
        chosen = dialog.payment_method()
        assert isinstance(chosen, PaymentMethod)
        # The audit trail reads ``.value``, which only a real member has.
        assert chosen.value == dialog.method.currentData()


@pytest.mark.ui
def test_payment_editor_rows_carry_real_enum_members(qtbot: QtBot) -> None:
    """The sale screen's split-payment rows share the same conversion."""

    from app.ui.forms import PaymentEditor

    editor = PaymentEditor()
    qtbot.addWidget(editor)
    editor.add_row(method=PaymentMethod.BANK_TRANSFER, amount="250.00")
    values = editor.values()
    assert values, "a row with an amount should produce a payment"
    for payment in values:
        assert isinstance(payment.method, PaymentMethod)
    assert values[-1].method is PaymentMethod.BANK_TRANSFER


@pytest.mark.ui
def test_a_payment_chosen_in_the_dialog_reaches_the_service(
    qtbot: QtBot,
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    """Exercise the dialog-to-service boundary that the crash came through."""

    from app.ui.dealers.screen import DealerPaymentDialog

    dealer = _dealer(db_session, owner)
    batch = _batch(db_session, owner, supplier, product, batch_number="DIALOG")
    sale = _credit_sale(db_session, owner, dealer, batch, quantity=10)

    dialog = DealerPaymentDialog(dealer.display_name, dealer.balance, "PKR")
    qtbot.addWidget(dialog)
    dialog.method.setCurrentIndex(dialog.method.findData(PaymentMethod.BANK_TRANSFER.value))
    dialog.amount.setText("600.00")

    result = DealerAccountService(db_session).record_payment(
        dealer.id,
        actor=owner,
        method=dialog.payment_method(),
        amount=dialog.amount.decimal_value("Payment"),
        reference=dialog.reference.text(),
        notes=dialog.notes.toPlainText(),
    )
    db_session.flush()
    assert result.amount == Decimal("600.00")
    assert [entry.invoice_number for entry in result.settled] == [sale.invoice_number]
    payment = db_session.scalars(select(Payment).where(Payment.dealer_id == dealer.id)).one()
    assert payment.method is PaymentMethod.BANK_TRANSFER


@pytest.mark.ui
def test_every_enum_combo_returns_a_real_member(qtbot: QtBot) -> None:
    """The same StrEnum trap existed in the supplier-payment and user-role dialogs.

    Qt flattens a ``StrEnum`` to a string in item data, and both the supplier
    payment audit trail and the role policy expect a real member.
    """

    from PySide6.QtWidgets import QComboBox

    from app.models.enums import UserRole
    from app.ui.widgets import populate_enum_combo, selected_enum

    for enum_type in (PaymentMethod, UserRole):
        combo = QComboBox()
        qtbot.addWidget(combo)
        populate_enum_combo(combo, enum_type)
        assert combo.count() == len(list(enum_type))
        for index in range(combo.count()):
            combo.setCurrentIndex(index)
            member = selected_enum(combo, enum_type)
            assert isinstance(member, enum_type)
            assert member.value  # the audit trail reads this


@pytest.mark.ui
def test_populate_enum_combo_preselects_a_member(qtbot: QtBot) -> None:
    from PySide6.QtWidgets import QComboBox

    from app.ui.widgets import populate_enum_combo, selected_enum

    combo = QComboBox()
    qtbot.addWidget(combo)
    populate_enum_combo(combo, PaymentMethod, current=PaymentMethod.MOBILE_WALLET)
    assert selected_enum(combo, PaymentMethod) is PaymentMethod.MOBILE_WALLET
    assert combo.currentText() == "Mobile Wallet"
