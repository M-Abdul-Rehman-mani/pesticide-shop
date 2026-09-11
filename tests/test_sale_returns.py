"""Returning goods against an invoice, and amending an unpaid one."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from pytestqt.qtbot import QtBot
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.customer import Customer
from app.models.dealer import Dealer
from app.models.enums import PaymentDirection, PaymentMethod, PaymentStatus, SaleStatus
from app.models.inventory import StockBatch, StockMovement
from app.models.payment import Payment
from app.models.product import Product
from app.models.sale import Sale, SaleItem
from app.models.supplier import Supplier
from app.security.authentication import AuthenticatedUser
from app.services.catalog_service import DealerService
from app.services.dto import (
    CreatePesticideSaleCommand,
    CreateStockPurchaseCommand,
    PaymentInput,
    PesticideSaleLineInput,
    PurchasedBatchInput,
)
from app.services.pesticide_sale_service import PesticideSaleService
from app.services.sale_return_service import (
    ReturnableLine,
    ReturnLineInput,
    SaleReturnService,
)
from app.services.stock_purchase_service import StockPurchaseService
from app.utils.exceptions import ConflictError, NotFoundError, ValidationError


def _batch(
    session: Session,
    actor: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    *,
    batch_number: str,
    quantity: int = 50,
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


def _sale(
    session: Session,
    owner: AuthenticatedUser,
    batch: StockBatch,
    *,
    quantity: int,
    dealer: Dealer | None = None,
    customer: Customer | None = None,
    paid: Decimal | None = None,
) -> Sale:
    sale = PesticideSaleService(session).create(
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(batch.id, quantity),),
            payments=(PaymentInput(PaymentMethod.CASH, paid),) if paid else (),
            dealer_id=dealer.id if dealer else None,
            customer_id=customer.id if customer else None,
        ),
        owner,
    )
    session.flush()
    return sale


def test_a_partial_return_restocks_and_credits_the_balance(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    dealer = _dealer(db_session, owner)
    batch = _batch(db_session, owner, supplier, product, batch_number="RET-1")
    sale = _sale(db_session, owner, batch, quantity=10, dealer=dealer)
    assert batch.quantity_available == 40
    assert dealer.balance == Decimal("1500.00")
    item = db_session.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id)).one()

    service = SaleReturnService(db_session)
    document = service.record(
        sale.id, (ReturnLineInput(item.id, 4),), owner, reason="Customer over-ordered"
    )
    db_session.flush()

    assert document.total == Decimal("600.00")
    assert document.refunded is False
    assert batch.quantity_available == 44
    assert dealer.balance == Decimal("900.00")
    assert sale.remaining_amount == Decimal("900.00")
    assert sale.payment_status is PaymentStatus.PARTIALLY_PAID
    # The invoice itself stands exactly as issued.
    assert sale.total == Decimal("1500.00")
    assert sale.status is SaleStatus.COMPLETED


def test_returning_everything_clears_the_invoice(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    dealer = _dealer(db_session, owner)
    batch = _batch(db_session, owner, supplier, product, batch_number="RET-2")
    sale = _sale(db_session, owner, batch, quantity=6, dealer=dealer)
    item = db_session.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id)).one()

    SaleReturnService(db_session).record(
        sale.id, (ReturnLineInput(item.id, 6),), owner, reason="Wrong product delivered"
    )
    db_session.flush()
    assert batch.quantity_available == 50
    assert dealer.balance == Decimal("0.00")
    assert sale.remaining_amount == Decimal("0.00")
    assert sale.payment_status is PaymentStatus.PAID


def test_a_return_on_a_paid_invoice_refunds_the_money(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    """Cash already taken can only go back."""

    batch = _batch(db_session, owner, supplier, product, batch_number="RET-3")
    sale = _sale(db_session, owner, batch, quantity=4, customer=customer, paid=Decimal("600.00"))
    item = db_session.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id)).one()

    document = SaleReturnService(db_session).record(
        sale.id, (ReturnLineInput(item.id, 2),), owner, reason="Damaged on delivery"
    )
    db_session.flush()

    assert document.refunded is True
    refund = db_session.scalars(
        select(Payment).where(
            Payment.sale_id == sale.id, Payment.direction == PaymentDirection.OUTGOING
        )
    ).one()
    assert refund.amount == Decimal("300.00")
    assert refund.reference == document.return_number
    assert batch.quantity_available == 48


def test_damaged_goods_are_recorded_but_not_restocked(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    batch = _batch(db_session, owner, supplier, product, batch_number="RET-4")
    sale = _sale(db_session, owner, batch, quantity=5, customer=customer)
    item = db_session.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id)).one()

    document = SaleReturnService(db_session).record(
        sale.id,
        (ReturnLineInput(item.id, 2, restock=False),),
        owner,
        reason="Bottles leaked",
    )
    db_session.flush()

    assert document.items[0].restocked is False
    assert batch.quantity_available == 45, "damaged stock must not go back on the shelf"
    assert document.total == Decimal("300.00")
    movements = db_session.scalars(
        select(StockMovement).where(
            StockMovement.batch_id == batch.id, StockMovement.reference_type == "Sale Return"
        )
    ).all()
    assert list(movements) == []


def test_a_line_cannot_be_returned_more_than_it_was_sold(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    batch = _batch(db_session, owner, supplier, product, batch_number="RET-5")
    sale = _sale(db_session, owner, batch, quantity=3, customer=customer)
    item = db_session.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id)).one()
    service = SaleReturnService(db_session)

    with pytest.raises(ValidationError, match="can still be returned"):
        service.record(sale.id, (ReturnLineInput(item.id, 4),), owner, reason="Too many")

    service.record(sale.id, (ReturnLineInput(item.id, 2),), owner, reason="Partial")
    db_session.flush()
    assert [line.returnable for line in service.returnable_lines(sale.id)] == [1]

    with pytest.raises(ValidationError, match="can still be returned"):
        service.record(sale.id, (ReturnLineInput(item.id, 2),), owner, reason="Too many again")


def test_returns_need_a_reason_and_at_least_one_line(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    batch = _batch(db_session, owner, supplier, product, batch_number="RET-6")
    sale = _sale(db_session, owner, batch, quantity=2, customer=customer)
    item = db_session.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id)).one()
    service = SaleReturnService(db_session)
    with pytest.raises(ValidationError, match="reason"):
        service.record(sale.id, (ReturnLineInput(item.id, 1),), owner, reason="  ")
    with pytest.raises(ValidationError, match="at least one line"):
        service.record(sale.id, (), owner, reason="Nothing selected")
    with pytest.raises(ValidationError, match="greater than zero"):
        service.record(sale.id, (ReturnLineInput(item.id, 0),), owner, reason="Zero")


def test_a_voided_sale_cannot_accept_a_return(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    batch = _batch(db_session, owner, supplier, product, batch_number="RET-7")
    sale = _sale(db_session, owner, batch, quantity=2, customer=customer)
    item = db_session.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id)).one()
    PesticideSaleService(db_session).void(sale.id, owner, reason="Cancelled")
    db_session.flush()
    with pytest.raises(ConflictError, match="completed sale"):
        SaleReturnService(db_session).record(
            sale.id, (ReturnLineInput(item.id, 1),), owner, reason="Too late"
        )


def test_amending_an_invoice_moves_only_the_stock_that_changed(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    dealer = _dealer(db_session, owner)
    first = _batch(db_session, owner, supplier, product, batch_number="AMEND-A")
    second = _batch(db_session, owner, supplier, product, batch_number="AMEND-B")
    sale = _sale(db_session, owner, first, quantity=10, dealer=dealer)
    assert first.quantity_available == 40
    assert dealer.balance == Decimal("1500.00")
    invoice_number = sale.invoice_number

    PesticideSaleService(db_session).amend(
        sale.id,
        CreatePesticideSaleCommand(
            lines=(
                PesticideSaleLineInput(first.id, 4),
                PesticideSaleLineInput(second.id, 2),
            ),
            payments=(),
            dealer_id=dealer.id,
        ),
        owner,
    )
    db_session.flush()

    assert sale.invoice_number == invoice_number, "the customer's reference is preserved"
    assert first.quantity_available == 46, "six units went back"
    assert second.quantity_available == 48, "two units left the new batch"
    assert sale.total == Decimal("900.00")
    assert sale.remaining_amount == Decimal("900.00")
    assert dealer.balance == Decimal("900.00")
    items = db_session.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id)).all()
    assert sorted(item.quantity for item in items) == [2, 4]


def test_amending_can_drop_a_line_entirely(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    first = _batch(db_session, owner, supplier, product, batch_number="DROP-A")
    second = _batch(db_session, owner, supplier, product, batch_number="DROP-B")
    sale = PesticideSaleService(db_session).create(
        CreatePesticideSaleCommand(
            lines=(
                PesticideSaleLineInput(first.id, 3),
                PesticideSaleLineInput(second.id, 5),
            ),
            payments=(),
            customer_id=customer.id,
        ),
        owner,
    )
    db_session.flush()

    PesticideSaleService(db_session).amend(
        sale.id,
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(first.id, 3),),
            payments=(),
            customer_id=customer.id,
        ),
        owner,
    )
    db_session.flush()
    assert second.quantity_available == 50, "the dropped line's stock came back"
    items = db_session.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id)).all()
    assert len(items) == 1


def test_a_paid_or_returned_invoice_cannot_be_edited(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    batch = _batch(db_session, owner, supplier, product, batch_number="LOCK-1")
    paid = _sale(db_session, owner, batch, quantity=2, customer=customer, paid=Decimal("300.00"))
    service = PesticideSaleService(db_session)
    command = CreatePesticideSaleCommand(
        lines=(PesticideSaleLineInput(batch.id, 1),), payments=(), customer_id=customer.id
    )
    with pytest.raises(ConflictError, match="Reverse them first"):
        service.amend(paid.id, command, owner)

    other = _sale(db_session, owner, batch, quantity=4, customer=customer)
    item = db_session.scalars(select(SaleItem).where(SaleItem.sale_id == other.id)).one()
    SaleReturnService(db_session).record(
        other.id, (ReturnLineInput(item.id, 1),), owner, reason="One back"
    )
    db_session.flush()
    with pytest.raises(ConflictError, match="returned against this invoice"):
        service.amend(other.id, command, owner)


def test_amending_refuses_more_than_the_batch_holds(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    batch = _batch(db_session, owner, supplier, product, batch_number="LIMIT-1", quantity=10)
    sale = _sale(db_session, owner, batch, quantity=4, customer=customer)
    with pytest.raises(ConflictError, match="available"):
        PesticideSaleService(db_session).amend(
            sale.id,
            CreatePesticideSaleCommand(
                lines=(PesticideSaleLineInput(batch.id, 25),),
                payments=(),
                customer_id=customer.id,
            ),
            owner,
        )


def test_amending_a_missing_sale_is_reported(db_session: Session, owner: AuthenticatedUser) -> None:
    with pytest.raises(NotFoundError):
        PesticideSaleService(db_session).amend(
            uuid.uuid4(),
            CreatePesticideSaleCommand(
                lines=(PesticideSaleLineInput(uuid.uuid4(), 1),), payments=()
            ),
            owner,
        )


@pytest.mark.ui
def test_return_dialog_totals_the_credit_and_collects_lines(qtbot: QtBot) -> None:
    """The operator sees what the return is worth before committing to it."""

    from app.ui.sales.screen import SaleReturnDialog

    lines = [
        ReturnableLine(
            sale_item_id=uuid.uuid4(),
            product="Test Herbicide",
            batch_number="B-001",
            sold=10,
            already_returned=2,
            unit_price=Decimal("150.00"),
        ),
        ReturnableLine(
            sale_item_id=uuid.uuid4(),
            product="Other Product",
            batch_number="B-002",
            sold=4,
            already_returned=0,
            unit_price=Decimal("90.00"),
        ),
    ]
    dialog = SaleReturnDialog("INV-2026-000001", lines, "PKR")
    qtbot.addWidget(dialog)
    assert dialog.values() == ()
    assert dialog.credit_label.text().endswith("0")

    # A line already partly returned can only give back what is left.
    assert dialog._quantities[0].maximum() == 8
    dialog._quantities[0].setValue(3)
    dialog._quantities[1].setValue(2)
    assert "630" in dialog.credit_label.text()

    dialog._restock[1].setChecked(False)
    selected = dialog.values()
    assert [(entry.quantity, entry.restock) for entry in selected] == [(3, True), (2, False)]
    assert selected[0].sale_item_id == lines[0].sale_item_id


@pytest.mark.ui
def test_edit_mode_switches_the_sales_screen_and_back(
    qtbot: QtBot, database_engine: Engine, owner: AuthenticatedUser
) -> None:
    """Amending must be obvious on screen and must not offer to take payment."""

    from app.config.settings import get_settings
    from app.ui.sales.screen import SalesScreen

    factory = sessionmaker[Session](bind=database_engine, expire_on_commit=False, autoflush=False)
    screen = SalesScreen(factory, owner, get_settings())
    qtbot.addWidget(screen)
    assert screen._editing is None
    assert screen.cancel_edit.isVisible() is False
    assert screen.payments.isEnabled() is True

    sale_id = uuid.uuid4()
    screen._enter_edit_mode(sale_id, "INV-2026-000042")
    assert screen._editing == sale_id
    assert "INV-2026-000042" in screen.complete.text()
    assert screen.payments.isEnabled() is False, "payments are reversed, not edited"
    assert screen.tabs.currentIndex() == 0

    screen._leave_edit_mode()
    assert screen._editing is None
    assert screen.complete.text() == "Complete Sale && Queue Emails"
    assert screen.payments.isEnabled() is True


@pytest.mark.ui
def test_sale_returns_tab_lists_recorded_credit_notes(
    qtbot: QtBot,
    db_session: Session,
    database_engine: Engine,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    """A recorded return has to be visible somewhere; this is where."""

    from app.config.settings import get_settings
    from app.ui.sales.screen import RETURNS_TAB, SalesScreen

    dealer = _dealer(db_session, owner)
    batch = _batch(db_session, owner, supplier, product, batch_number="RET-TAB")
    sale = _sale(db_session, owner, batch, quantity=10, dealer=dealer)
    item = db_session.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id)).one()
    document = SaleReturnService(db_session).record(
        sale.id, (ReturnLineInput(item.id, 4, restock=False),), owner, reason="Leaking pack"
    )
    db_session.flush()

    factory = sessionmaker[Session](bind=database_engine, expire_on_commit=False, autoflush=False)
    screen = SalesScreen(factory, owner, get_settings())
    qtbot.addWidget(screen)
    assert screen.tabs.tabText(RETURNS_TAB) == "Sale Returns"

    screen._display_returns([document])
    row = [screen.returns_model.data(screen.returns_model.index(0, column)) for column in range(8)]
    assert row[0] == document.return_number
    assert row[2] == sale.invoice_number
    assert row[3] == dealer.display_name
    assert "not restocked" in str(row[4])
    assert row[5].endswith("600"), "credit prints as whole units"
    assert row[6] == "Against balance"
    assert "1 return" in screen.returns_count.text()

    # Opening the invoice from a credit note has to land on the sales list.
    screen._open_returned_invoice(0)
    assert screen.sales_search.text() == sale.invoice_number


def test_moving_an_invoice_between_dealers_moves_the_balance_with_it(
    db_session: Session, owner: AuthenticatedUser, supplier: Supplier, product: Product
) -> None:
    """Re-addressing an invoice left the old dealer charged and the new one at zero."""

    first = DealerService(db_session).create(
        actor=owner, name="Dealer A", phone="03001110000", credit_limit=Decimal("100000.00")
    )
    second = DealerService(db_session).create(
        actor=owner, name="Dealer B", phone="03002220000", credit_limit=Decimal("100000.00")
    )
    db_session.flush()
    batch = _batch(db_session, owner, supplier, product, batch_number="MOVE-1")
    service = PesticideSaleService(db_session)
    sale = _sale(db_session, owner, batch, quantity=10, dealer=first)
    assert first.balance == Decimal("1500.00")

    service.amend(
        sale.id,
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(batch.id, 10),), payments=(), dealer_id=second.id
        ),
        owner,
    )
    db_session.flush()
    assert (first.balance, second.balance) == (Decimal("0.00"), Decimal("1500.00"))

    # Moving it on to a retail customer clears the dealer account entirely.
    customer = Customer(name="Retail Buyer", phone="03004440000")
    db_session.add(customer)
    db_session.flush()
    service.amend(
        sale.id,
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(batch.id, 10),), payments=(), customer_id=customer.id
        ),
        owner,
    )
    db_session.flush()
    assert (first.balance, second.balance) == (Decimal("0.00"), Decimal("0.00"))

    # And bringing it back to a dealer charges the whole invoice, not the difference.
    service.amend(
        sale.id,
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(batch.id, 10),), payments=(), dealer_id=first.id
        ),
        owner,
    )
    db_session.flush()
    assert (first.balance, second.balance) == (Decimal("1500.00"), Decimal("0.00"))


def test_amending_onto_a_new_dealer_checks_their_whole_credit_limit(
    db_session: Session, owner: AuthenticatedUser, supplier: Supplier, product: Product
) -> None:
    """The receiving dealer takes the full invoice against their limit."""

    first = DealerService(db_session).create(
        actor=owner, name="Roomy Dealer", phone="03001110001", credit_limit=Decimal("100000.00")
    )
    tight = DealerService(db_session).create(
        actor=owner, name="Tight Dealer", phone="03002220001", credit_limit=Decimal("1000.00")
    )
    db_session.flush()
    batch = _batch(db_session, owner, supplier, product, batch_number="LIMIT-1")
    sale = _sale(db_session, owner, batch, quantity=10, dealer=first)

    with pytest.raises(ConflictError, match="credit limit"):
        PesticideSaleService(db_session).amend(
            sale.id,
            CreatePesticideSaleCommand(
                lines=(PesticideSaleLineInput(batch.id, 10),), payments=(), dealer_id=tight.id
            ),
            owner,
        )
    assert (first.balance, tight.balance) == (Decimal("1500.00"), Decimal("0.00"))


def test_a_return_credit_cannot_be_reversed_as_if_it_were_a_payment(
    db_session: Session, owner: AuthenticatedUser, supplier: Supplier, product: Product
) -> None:
    """Reversing it would re-charge the dealer for goods that are back on the shelf."""

    from app.services.dealer_account_service import DealerAccountService

    dealer = _dealer(db_session, owner)
    batch = _batch(db_session, owner, supplier, product, batch_number="NOREV-1")
    sale = _sale(db_session, owner, batch, quantity=10, dealer=dealer)
    item = db_session.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id)).one()
    document = SaleReturnService(db_session).record(
        sale.id, (ReturnLineInput(item.id, 10),), owner, reason="All returned"
    )
    db_session.flush()
    assert dealer.balance == Decimal("0.00")

    credit = db_session.scalars(
        select(Payment).where(
            Payment.sale_id == sale.id, Payment.direction == PaymentDirection.INCOMING
        )
    ).one()
    assert credit.sale_return_id == document.id
    assert credit.is_return_credit is True

    with pytest.raises(ConflictError, match="returned goods"):
        DealerAccountService(db_session).reverse_payment(credit.id, actor=owner, reason="mistake")
    assert dealer.balance == Decimal("0.00"), "returned goods are never re-charged"
    assert batch.quantity_available == 50


def test_a_refund_paid_out_with_a_return_is_not_read_as_a_reversal(
    db_session: Session, owner: AuthenticatedUser, supplier: Supplier, product: Product
) -> None:
    """A paid invoice refunds cash; that refund must not hide a real payment."""

    from app.services.dealer_account_service import DealerAccountService

    dealer = _dealer(db_session, owner)
    batch = _batch(db_session, owner, supplier, product, batch_number="REFUND-1")
    sale = _sale(db_session, owner, batch, quantity=10, dealer=dealer, paid=Decimal("1500.00"))
    item = db_session.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id)).one()
    SaleReturnService(db_session).record(
        sale.id, (ReturnLineInput(item.id, 10),), owner, reason="All returned"
    )
    db_session.flush()

    payment = db_session.scalars(
        select(Payment).where(
            Payment.sale_id == sale.id,
            Payment.direction == PaymentDirection.INCOMING,
            Payment.sale_return_id.is_(None),
        )
    ).one()
    # The original payment is still reversible: the refund beside it is not a reversal.
    reversal = DealerAccountService(db_session).reverse_payment(
        payment.id, actor=owner, reason="Cheque bounced"
    )
    db_session.flush()
    assert reversal.direction is PaymentDirection.OUTGOING
    assert reversal.amount == Decimal("1500.00")


def test_a_salesperson_cannot_record_a_return(
    db_session: Session,
    owner: AuthenticatedUser,
    owner_model: object,
    supplier: Supplier,
    product: Product,
) -> None:
    """A return hands money back, so it needs the same authority as a void."""

    from dataclasses import replace as dataclass_replace

    from app.models.enums import UserRole
    from app.utils.exceptions import PermissionDeniedError

    batch = _batch(db_session, owner, supplier, product, batch_number="PERM-1")
    sale = _sale(db_session, owner, batch, quantity=4)
    item = db_session.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id)).one()
    salesperson = dataclass_replace(owner, role=UserRole.SALESPERSON)

    with pytest.raises(PermissionDeniedError):
        SaleReturnService(db_session).record(
            sale.id, (ReturnLineInput(item.id, 1),), salesperson, reason="Wrong pack"
        )
    assert batch.quantity_available == 46, "nothing goes back on the shelf"


def test_a_dealer_statement_names_a_return_credit_for_what_it_is(
    db_session: Session, owner: AuthenticatedUser, supplier: Supplier, product: Product
) -> None:
    """On the ledger a return must read as goods coming back, not as cash received."""

    from app.services.dealer_account_service import DealerAccountService

    dealer = _dealer(db_session, owner)
    batch = _batch(db_session, owner, supplier, product, batch_number="STMT-1")
    sale = _sale(db_session, owner, batch, quantity=10, dealer=dealer)
    item = db_session.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id)).one()
    SaleReturnService(db_session).record(
        sale.id, (ReturnLineInput(item.id, 4),), owner, reason="Leaking pack"
    )
    db_session.flush()

    statement = DealerAccountService(db_session).statement(dealer.id)
    credit = next(entry for entry in statement.entries if entry.reference.startswith("RET-"))
    assert credit.detail == f"Goods returned against {sale.invoice_number}"
    assert credit.credit == Decimal("600.00")
    # The ledger and the account agree on what is left owed.
    assert statement.outstanding == dealer.balance == Decimal("900.00")
