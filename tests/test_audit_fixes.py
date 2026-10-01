"""Regression tests for the fixes made after the October 2026 application audit."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models.audit import AuditLog
from app.models.customer import Customer
from app.models.dealer import Dealer
from app.models.enums import PaymentMethod, PaymentStatus, SaleStatus, UserRole
from app.models.inventory import StockBatch
from app.models.payment import Payment
from app.models.product import Product
from app.models.purchase import Purchase
from app.models.sale import Sale
from app.models.sale_return import SaleReturn
from app.models.supplier import Supplier
from app.models.user import User
from app.reports.report_service import DateRange, ReportService
from app.security.authentication import (
    MAX_FAILED_LOGINS,
    AuthenticatedUser,
    AuthenticationService,
    sign_in,
)
from app.security.session_guard import guard_sessions
from app.services.catalog_service import DealerService
from app.services.dealer_account_service import DealerAccountService
from app.services.dto import (
    CreatePesticideSaleCommand,
    CreateStockPurchaseCommand,
    PaymentInput,
    PesticideSaleLineInput,
    PurchasedBatchInput,
)
from app.services.payment_service import PaymentService
from app.services.pesticide_sale_service import PesticideSaleService
from app.services.sale_return_service import ReturnLineInput, SaleReturnService
from app.services.stock_inventory_service import StockInventoryService
from app.services.stock_purchase_service import StockPurchaseService
from app.utils.exceptions import (
    AuthenticationError,
    ConflictError,
    PermissionDeniedError,
)
from app.utils.security import hash_password

TODAY = DateRange(datetime.now(UTC) - timedelta(days=1), datetime.now(UTC) + timedelta(days=1))


def _purchase(
    session: Session,
    actor: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    *,
    batch: str,
    quantity: int = 50,
    cost: str = "100.00",
    price: str = "150.00",
    expiry: date | None = date(2099, 1, 1),
) -> tuple[Purchase, StockBatch]:
    purchase = StockPurchaseService(session).create(
        CreateStockPurchaseCommand(
            supplier_id=supplier.id,
            batches=(
                PurchasedBatchInput(
                    product_id=product.id,
                    batch_number=batch,
                    quantity=quantity,
                    purchase_price=Decimal(cost),
                    selling_price=Decimal(price),
                    expiry_date=expiry,
                ),
            ),
        ),
        actor,
    )
    session.flush()
    stock = session.scalars(
        select(StockBatch).where(
            StockBatch.product_id == product.id, StockBatch.batch_number == batch.upper()
        )
    ).one()
    return purchase, stock


def _sell(
    session: Session,
    actor: AuthenticatedUser,
    batch: StockBatch,
    quantity: int,
    *,
    price: str | None = None,
    line_discount: str = "0",
    order_discount: str = "0",
    tax: str = "0",
    paid: str | None = None,
    dealer: Dealer | None = None,
    customer: Customer | None = None,
) -> Sale:
    sale = PesticideSaleService(session).create(
        CreatePesticideSaleCommand(
            lines=(
                PesticideSaleLineInput(
                    batch.id,
                    quantity,
                    Decimal(price) if price else None,
                    Decimal(line_discount),
                ),
            ),
            payments=(PaymentInput(PaymentMethod.CASH, Decimal(paid)),) if paid else (),
            dealer_id=dealer.id if dealer else None,
            customer_id=customer.id if customer else None,
            order_discount=Decimal(order_discount),
            tax=Decimal(tax),
        ),
        actor,
    )
    session.flush()
    return sale


def _return(
    session: Session,
    actor: AuthenticatedUser,
    sale: Sale,
    quantity: int,
    *,
    restock: bool = True,
) -> SaleReturn:
    document = SaleReturnService(session).record(
        sale.id, (ReturnLineInput(sale.items[0].id, quantity, restock),), actor, reason="Audit"
    )
    session.flush()
    return document


def _dealer(session: Session, actor: AuthenticatedUser, *, limit: str = "0") -> Dealer:
    dealer = DealerService(session).create(
        actor=actor, name="Audit Dealer", phone="03001234567", credit_limit=Decimal(limit)
    )
    session.flush()
    return dealer


def _actor(owner: AuthenticatedUser, role: UserRole) -> AuthenticatedUser:
    return AuthenticatedUser(owner.id, "staff", "Staff", "staff@test.invalid", role, False)


# --------------------------------------------------------------------- BUG-01 returns


def test_a_return_credits_what_a_discounted_line_actually_cost(
    db_session: Session, owner: AuthenticatedUser, supplier: Supplier, product: Product
) -> None:
    _, batch = _purchase(db_session, owner, supplier, product, batch="DISC-1")
    sale = _sell(db_session, owner, batch, 10, price="100", line_discount="200", paid="800")

    document = _return(db_session, owner, sale, 10)

    assert document.total == Decimal("800.00")


def test_a_return_never_credits_more_than_the_invoice_total(
    db_session: Session, owner: AuthenticatedUser, supplier: Supplier, product: Product
) -> None:
    _, batch = _purchase(db_session, owner, supplier, product, batch="DISC-2")
    sale = _sell(db_session, owner, batch, 10, price="100", order_discount="500", paid="500")

    document = _return(db_session, owner, sale, 10)

    assert document.total == sale.total == Decimal("500.00")


def test_partial_returns_add_up_to_exactly_the_invoice_total(
    db_session: Session, owner: AuthenticatedUser, supplier: Supplier, product: Product
) -> None:
    _, batch = _purchase(db_session, owner, supplier, product, batch="DISC-3")
    sale = _sell(db_session, owner, batch, 3, price="100", order_discount="10", tax="5")
    assert sale.total == Decimal("295.00")

    credits = [_return(db_session, owner, sale, 1).total for _ in range(3)]

    assert sum(credits, Decimal("0.00")) == sale.total
    assert credits == [Decimal("98.33"), Decimal("98.34"), Decimal("98.33")]


def test_the_return_preview_matches_the_recorded_credit(
    db_session: Session, owner: AuthenticatedUser, supplier: Supplier, product: Product
) -> None:
    _, batch = _purchase(db_session, owner, supplier, product, batch="DISC-4")
    sale = _sell(db_session, owner, batch, 4, price="100", line_discount="40")
    line = SaleReturnService(db_session).returnable_lines(sale.id)[0]

    assert line.credit_for(2) == Decimal("180.00")
    assert _return(db_session, owner, sale, 2).total == Decimal("180.00")


# --------------------------------------------------------------------- BUG-02/07/08 stock


def test_cancelling_a_purchase_keeps_a_later_restock_of_the_same_batch(
    db_session: Session, owner: AuthenticatedUser, supplier: Supplier, product: Product
) -> None:
    first, batch = _purchase(db_session, owner, supplier, product, batch="CX", quantity=10)
    second, _ = _purchase(
        db_session, owner, supplier, product, batch="CX", quantity=30, cost="200.00"
    )

    StockPurchaseService(db_session).cancel(first.id, owner)
    db_session.flush()
    db_session.refresh(batch)

    assert batch.is_active
    assert (batch.quantity_received, batch.quantity_available) == (30, 30)
    assert batch.purchase_price == Decimal("200.00")
    assert second.status.value == "COMPLETED"


def test_cancelling_the_later_restock_leaves_the_original_units(
    db_session: Session, owner: AuthenticatedUser, supplier: Supplier, product: Product
) -> None:
    _, batch = _purchase(db_session, owner, supplier, product, batch="CY", quantity=10)
    second, _ = _purchase(
        db_session, owner, supplier, product, batch="CY", quantity=30, cost="200.00"
    )

    StockPurchaseService(db_session).cancel(second.id, owner)
    db_session.flush()
    db_session.refresh(batch)

    assert (batch.quantity_received, batch.quantity_available) == (10, 10)
    assert batch.purchase_price == Decimal("100.00")


def test_a_purchase_whose_stock_was_sold_still_cannot_be_cancelled(
    db_session: Session, owner: AuthenticatedUser, supplier: Supplier, product: Product
) -> None:
    first, batch = _purchase(db_session, owner, supplier, product, batch="CZ", quantity=10)
    _sell(db_session, owner, batch, 5, paid="750")

    with pytest.raises(ConflictError):
        StockPurchaseService(db_session).cancel(first.id, owner)


def test_a_restock_uses_the_weighted_average_cost_and_earliest_expiry(
    db_session: Session, owner: AuthenticatedUser, supplier: Supplier, product: Product
) -> None:
    _, batch = _purchase(
        db_session, owner, supplier, product, batch="PX", quantity=10, expiry=date(2099, 6, 1)
    )
    _purchase(
        db_session,
        owner,
        supplier,
        product,
        batch="PX",
        quantity=10,
        cost="300.00",
        price="400.00",
        expiry=date(2098, 1, 1),
    )
    db_session.refresh(batch)
    assert batch.purchase_price == Decimal("200.00")
    assert batch.selling_price == Decimal("400.00")
    assert batch.expiry_date == date(2098, 1, 1)

    sale = _sell(db_session, owner, batch, 20, paid="8000")

    assert sum(item.purchase_cost for item in sale.items) == Decimal("4000.00")


def test_restocking_a_removed_batch_puts_it_back_on_sale(
    db_session: Session, owner: AuthenticatedUser, supplier: Supplier, product: Product
) -> None:
    _, batch = _purchase(db_session, owner, supplier, product, batch="RB", quantity=5)
    StockInventoryService(db_session).delete_batch(batch.id, actor=owner, reason="Damaged")
    db_session.flush()

    _, again = _purchase(db_session, owner, supplier, product, batch="RB", quantity=20)

    assert again.id == batch.id and again.is_active and again.quantity_available == 20
    _sell(db_session, owner, again, 1, paid="150")


def test_returning_goods_to_a_removed_batch_reactivates_it(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    _, batch = _purchase(db_session, owner, supplier, product, batch="RMV", quantity=10)
    sale = _sell(db_session, owner, batch, 4, customer=customer)
    StockInventoryService(db_session).delete_batch(batch.id, actor=owner, reason="Recall")
    db_session.flush()

    _return(db_session, owner, sale, 4)
    db_session.refresh(batch)

    assert batch.is_active and batch.quantity_available == 4


# --------------------------------------------------------------------- BUG-04/05 reports


def test_a_fully_returned_sale_adds_no_profit(
    db_session: Session, owner: AuthenticatedUser, supplier: Supplier, product: Product
) -> None:
    _, batch = _purchase(db_session, owner, supplier, product, batch="PR-1")
    reports = ReportService(db_session)
    before = reports.profit(TODAY)

    sale = _sell(db_session, owner, batch, 10, paid="1500")
    _return(db_session, owner, sale, 10)

    assert reports.profit(TODAY) == before
    assert sum((point.profit for point in reports.financial_by_day(TODAY, "UTC")), Decimal(0)) == (
        before
    )
    product_row = next(row for row in reports.product_catalogue(TODAY) if row.id == product.id)
    assert product_row.profit == Decimal("0.00")
    assert reports.product_dashboard(product.id, TODAY).profit == Decimal("0.00")


def test_written_off_returns_cost_the_whole_credit(
    db_session: Session, owner: AuthenticatedUser, supplier: Supplier, product: Product
) -> None:
    _, batch = _purchase(db_session, owner, supplier, product, batch="PR-2")
    reports = ReportService(db_session)
    before = reports.profit(TODAY)

    sale = _sell(db_session, owner, batch, 10, paid="1500")
    _return(db_session, owner, sale, 10, restock=False)

    # Sold for 1500 at a cost of 1000; all 1500 refunded and nothing back on the shelf.
    assert reports.profit(TODAY) - before == Decimal("-1000.00")


def test_cash_received_ignores_return_credits_and_nets_reversals(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    _, batch = _purchase(db_session, owner, supplier, product, batch="CASH-1")
    reports = ReportService(db_session)
    before = reports.payment_breakdown(TODAY).get("CASH", Decimal("0.00"))

    unpaid = _sell(db_session, owner, batch, 10, customer=customer)
    _return(db_session, owner, unpaid, 10)
    dealer = _dealer(db_session, owner)
    accounts = DealerAccountService(db_session)
    accounts.record_payment(
        dealer.id, actor=owner, method=PaymentMethod.CASH, amount=Decimal("1000")
    )
    db_session.flush()
    accounts.reverse_payment(accounts.payments(dealer.id)[0].id, actor=owner, reason="Typo")
    db_session.flush()
    assert reports.payment_breakdown(TODAY).get("CASH", Decimal("0.00")) == before

    paid = _sell(db_session, owner, batch, 2, paid="300")
    _return(db_session, owner, paid, 1)  # 150 handed back in cash

    assert reports.payment_breakdown(TODAY)["CASH"] - before == Decimal("150.00")


# --------------------------------------------------------------------- BUG-03/06 dealers


def test_a_refunded_return_leaves_the_statement_matching_the_balance(
    db_session: Session, owner: AuthenticatedUser, supplier: Supplier, product: Product
) -> None:
    _, batch = _purchase(db_session, owner, supplier, product, batch="ST-1")
    dealer = _dealer(db_session, owner)
    sale = _sell(db_session, owner, batch, 10, dealer=dealer, paid="1500")
    _return(db_session, owner, sale, 10)
    db_session.refresh(dealer)

    statement = DealerAccountService(db_session).statement(dealer.id)

    assert dealer.balance == statement.outstanding == Decimal("0.00")
    assert statement.entries[-1].balance == Decimal("0.00")
    details = [entry.detail for entry in statement.entries]
    assert any("refund for returned goods" in detail for detail in details)
    assert not any(detail.endswith("reversed") for detail in details)


def test_advance_credit_pays_down_the_next_invoice(
    db_session: Session, owner: AuthenticatedUser, supplier: Supplier, product: Product
) -> None:
    _, batch = _purchase(db_session, owner, supplier, product, batch="ADV-1")
    dealer = _dealer(db_session, owner)
    DealerAccountService(db_session).record_payment(
        dealer.id, actor=owner, method=PaymentMethod.CASH, amount=Decimal("500")
    )
    db_session.flush()
    reports = ReportService(db_session)
    outstanding_before = reports.dashboard(TODAY).outstanding_payments
    cash_before = reports.payment_breakdown(TODAY)["CASH"]

    sale = _sell(db_session, owner, batch, 10, dealer=dealer)
    db_session.refresh(dealer)

    assert sale.remaining_amount == Decimal("1000.00")
    assert sale.payment_status is PaymentStatus.PARTIALLY_PAID
    assert dealer.balance == Decimal("1000.00")
    assert reports.dashboard(TODAY).outstanding_payments - outstanding_before == Decimal("1000.00")
    # Applying credit is not new money.
    assert reports.payment_breakdown(TODAY)["CASH"] == cash_before
    statement = DealerAccountService(db_session).statement(dealer.id)
    assert statement.outstanding == dealer.balance


def test_voiding_an_invoice_paid_by_credit_returns_the_credit(
    db_session: Session, owner: AuthenticatedUser, supplier: Supplier, product: Product
) -> None:
    _, batch = _purchase(db_session, owner, supplier, product, batch="ADV-2")
    dealer = _dealer(db_session, owner)
    accounts = DealerAccountService(db_session)
    accounts.record_payment(
        dealer.id, actor=owner, method=PaymentMethod.CASH, amount=Decimal("2000")
    )
    db_session.flush()
    sale = _sell(db_session, owner, batch, 10, dealer=dealer)
    assert sale.payment_status is PaymentStatus.PAID

    advance = next(payment for payment in accounts.payments(dealer.id) if payment.sale_id is None)
    with pytest.raises(ConflictError, match="already paid invoices"):
        accounts.reverse_payment(advance.id, actor=owner, reason="Wrong dealer")

    PesticideSaleService(db_session).void(sale.id, owner, reason="Entered twice")
    db_session.flush()
    db_session.refresh(dealer)

    assert sale.status is SaleStatus.VOIDED
    assert dealer.balance == Decimal("-2000.00")
    accounts.reverse_payment(advance.id, actor=owner, reason="Wrong dealer")
    db_session.flush()
    db_session.refresh(dealer)
    assert dealer.balance == Decimal("0.00")


def test_an_applied_credit_entry_cannot_be_reversed_as_cash(
    db_session: Session, owner: AuthenticatedUser, supplier: Supplier, product: Product
) -> None:
    _, batch = _purchase(db_session, owner, supplier, product, batch="ADV-3")
    dealer = _dealer(db_session, owner)
    DealerAccountService(db_session).record_payment(
        dealer.id, actor=owner, method=PaymentMethod.CASH, amount=Decimal("200")
    )
    db_session.flush()
    sale = _sell(db_session, owner, batch, 2, dealer=dealer)
    applied = db_session.scalars(
        select(Payment).where(Payment.sale_id == sale.id, Payment.applied_credit.is_(True))
    ).one()

    with pytest.raises(ConflictError, match="advance credit"):
        DealerAccountService(db_session).reverse_payment(applied.id, actor=owner, reason="x")


# --------------------------------------------------------------------- SEC-03/04/05/06


@pytest.fixture
def savepoint_factory(db_session: Session) -> sessionmaker[Session]:
    return sessionmaker[Session](
        bind=db_session.get_bind(),
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
        autoflush=False,
    )


def _user(session: Session, username: str, role: UserRole = UserRole.MANAGER) -> User:
    user = User(
        username=username,
        full_name="Audit User",
        email=f"{username}@test.invalid",
        password_hash=hash_password("Correct-Pass-123"),
        role=role,
    )
    session.add(user)
    session.flush()
    return user


def test_a_failed_sign_in_is_kept_in_the_audit_log(
    db_session: Session, savepoint_factory: sessionmaker[Session]
) -> None:
    user = _user(db_session, "audit-fail")

    with pytest.raises(AuthenticationError):
        sign_in(savepoint_factory, "audit-fail", "wrong")

    rows = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "LOGIN_FAILED", AuditLog.entity_id == user.id)
    ).all()
    assert len(rows) == 1


def test_repeated_wrong_passwords_lock_the_account(
    db_session: Session, savepoint_factory: sessionmaker[Session]
) -> None:
    user = _user(db_session, "audit-lock")
    for _ in range(MAX_FAILED_LOGINS):
        with pytest.raises(AuthenticationError):
            sign_in(savepoint_factory, "audit-lock", "guess")

    with pytest.raises(AuthenticationError, match="locked"):
        sign_in(savepoint_factory, "audit-lock", "Correct-Pass-123")

    db_session.refresh(user)
    user.locked_until = datetime.now(UTC) - timedelta(seconds=1)
    db_session.flush()
    assert sign_in(savepoint_factory, "audit-lock", "Correct-Pass-123").id == user.id
    db_session.refresh(user)
    assert user.failed_login_attempts == 0 and user.locked_until is None


def test_a_successful_sign_in_resets_the_failure_count(
    db_session: Session, savepoint_factory: sessionmaker[Session]
) -> None:
    _user(db_session, "audit-reset")
    for _ in range(MAX_FAILED_LOGINS - 1):
        with pytest.raises(AuthenticationError):
            sign_in(savepoint_factory, "audit-reset", "guess")
    sign_in(savepoint_factory, "audit-reset", "Correct-Pass-123")
    with pytest.raises(AuthenticationError, match="Invalid"):
        sign_in(savepoint_factory, "audit-reset", "guess")


def test_a_disabled_or_demoted_account_is_refused_mid_session(
    db_session: Session, savepoint_factory: sessionmaker[Session]
) -> None:
    user = _user(db_session, "audit-stale")
    actor = AuthenticatedUser.from_model(user)
    remove = guard_sessions(savepoint_factory, actor)
    try:
        with savepoint_factory() as session:
            AuthenticationService(session).validate_session(actor)

        user.role = UserRole.SALESPERSON
        db_session.flush()
        with pytest.raises(AuthenticationError, match="sign in again"), savepoint_factory() as s:
            s.execute(select(1))
        with pytest.raises(AuthenticationError):
            AuthenticationService(db_session).validate_session(actor)
    finally:
        remove()
    with savepoint_factory() as session:
        session.execute(select(1))


def test_only_owners_and_managers_can_edit_an_issued_invoice(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    _, batch = _purchase(db_session, owner, supplier, product, batch="ED-1")
    sale = _sell(db_session, owner, batch, 2, customer=customer)
    command = CreatePesticideSaleCommand(
        lines=(PesticideSaleLineInput(batch.id, 2, Decimal("1")),),
        payments=(),
        customer_id=customer.id,
    )

    with pytest.raises(PermissionDeniedError):
        PesticideSaleService(db_session).amend(
            sale.id, command, _actor(owner, UserRole.SALESPERSON)
        )

    PesticideSaleService(db_session).amend(sale.id, command, _actor(owner, UserRole.MANAGER))
    assert sale.total == Decimal("2.00")


def test_a_salesperson_can_still_collect_payment_on_an_invoice(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    _, batch = _purchase(db_session, owner, supplier, product, batch="ED-2")
    sale = _sell(db_session, owner, batch, 1, customer=customer)
    PaymentService(db_session).collect_sale_payment(
        sale.id,
        actor=_actor(owner, UserRole.SALESPERSON),
        method=PaymentMethod.CASH,
        amount=Decimal("150"),
    )
    assert sale.payment_status is PaymentStatus.PAID


# --------------------------------------------------------------------- SEC-02 / BUG-09 UI


@pytest.mark.ui
def test_the_dashboard_hides_profit_from_roles_without_profit_rights(
    qtbot: QtBot, savepoint_factory: sessionmaker[Session]
) -> None:
    from app.ui.dashboard.screen import DashboardScreen

    hidden = DashboardScreen(savepoint_factory, "UTC", "PKR", show_profit=False)
    shown = DashboardScreen(savepoint_factory, "UTC", "PKR")
    qtbot.addWidget(hidden)
    qtbot.addWidget(shown)
    QThreadPool.globalInstance().waitForDone(5000)
    QApplication.processEvents()

    for tab in (hidden.overview, hidden.customers, hidden.dealers):
        assert "Profit" not in tab.cards.cards
    assert "Profit" not in hidden.products._columns
    assert "Profit" in shown.overview.cards.cards
    assert "Profit" in shown.products._columns


@pytest.mark.ui
def test_the_sale_screen_offers_every_customer(
    qtbot: QtBot,
    db_session: Session,
    owner: AuthenticatedUser,
    savepoint_factory: sessionmaker[Session],
) -> None:
    from app.config.settings import get_settings
    from app.ui.sales.screen import SalesScreen

    db_session.add_all(
        Customer(name=f"Bulk Customer {index:04d}", phone=f"0399{index:07d}")
        for index in range(520)
    )
    db_session.flush()

    screen = SalesScreen(savepoint_factory, owner, get_settings())
    qtbot.addWidget(screen)
    qtbot.waitUntil(lambda: len(screen._customers) >= 520, timeout=10000)
    QThreadPool.globalInstance().waitForDone(5000)

    labels = [screen.recipient.itemText(index) for index in range(screen.recipient.count())]
    assert any(label.startswith("Bulk Customer 0519") for label in labels)
