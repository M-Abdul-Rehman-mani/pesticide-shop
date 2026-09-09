"""Voiding a sale, reversing a payment, and the safeguards around both."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.dealer import Dealer
from app.models.enums import (
    PaymentDirection,
    PaymentMethod,
    PaymentStatus,
    SaleStatus,
    UserRole,
)
from app.models.inventory import StockBatch, StockMovement
from app.models.payment import Payment
from app.models.product import Product
from app.models.sale import Sale
from app.models.supplier import Supplier
from app.reports.report_service import DateRange, ReportService
from app.security.authentication import AuthenticatedUser
from app.services.catalog_service import DealerService
from app.services.dealer_account_service import DealerAccountService
from app.services.dto import (
    CreatePesticideSaleCommand,
    CreateStockPurchaseCommand,
    PaymentInput,
    PesticideSaleLineInput,
    PurchasedBatchInput,
)
from app.services.pesticide_sale_service import PesticideSaleService
from app.services.stock_purchase_service import StockPurchaseService
from app.utils.exceptions import ConflictError, PermissionDeniedError, ValidationError

TIMEZONE = "Asia/Karachi"


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
    payments = (PaymentInput(PaymentMethod.CASH, paid),) if paid else ()
    sale = PesticideSaleService(session).create(
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(batch.id, quantity),),
            payments=payments,
            dealer_id=dealer.id if dealer else None,
            customer_id=customer.id if customer else None,
        ),
        owner,
    )
    session.flush()
    return sale


def test_voiding_returns_stock_and_clears_the_dealer_debt(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    dealer = _dealer(db_session, owner)
    batch = _batch(db_session, owner, supplier, product, batch_number="VOID-1")
    sale = _sale(db_session, owner, batch, quantity=10, dealer=dealer)
    assert batch.quantity_available == 40
    assert dealer.balance == Decimal("1500.00")

    PesticideSaleService(db_session).void(sale.id, owner, reason="Wrong product picked")
    db_session.flush()

    assert sale.status is SaleStatus.VOIDED
    assert batch.quantity_available == 50
    assert dealer.balance == Decimal("0.00")
    movements = list(
        db_session.scalars(
            select(StockMovement)
            .where(StockMovement.batch_id == batch.id)
            .order_by(StockMovement.created_at)
        )
    )
    assert movements[-1].reference_type == "Sale Void"
    assert movements[-1].quantity_change == 10
    assert movements[-1].balance_after == 50


def test_a_voided_sale_leaves_reports_and_statements(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    dealer = _dealer(db_session, owner)
    batch = _batch(db_session, owner, supplier, product, batch_number="VOID-2")
    kept = _sale(db_session, owner, batch, quantity=4, dealer=dealer)
    scrapped = _sale(db_session, owner, batch, quantity=6, dealer=dealer)
    PesticideSaleService(db_session).void(scrapped.id, owner, reason="Cancelled")
    db_session.flush()

    period = DateRange.today(TIMEZONE)
    assert ReportService(db_session).dashboard(period).units_sold == 4
    statement = DealerAccountService(db_session).statement(dealer.id)
    assert [entry.reference for entry in statement.entries] == [kept.invoice_number]
    assert statement.outstanding == kept.total


def test_a_paid_sale_must_have_its_payments_reversed_first(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    batch = _batch(db_session, owner, supplier, product, batch_number="VOID-3")
    sale = _sale(db_session, owner, batch, quantity=2, customer=customer, paid=Decimal("300.00"))
    with pytest.raises(ConflictError, match="Reverse them first"):
        PesticideSaleService(db_session).void(sale.id, owner, reason="Cancelled")
    assert sale.status is SaleStatus.COMPLETED


def test_voiding_needs_a_reason_and_the_permission(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    batch = _batch(db_session, owner, supplier, product, batch_number="VOID-4")
    sale = _sale(db_session, owner, batch, quantity=1, customer=customer)
    service = PesticideSaleService(db_session)
    with pytest.raises(ValidationError, match="reason"):
        service.void(sale.id, owner, reason="   ")
    salesperson = AuthenticatedUser(
        id=owner.id,
        username="sales",
        full_name="Sales Person",
        email="sales@test.invalid",
        role=UserRole.SALESPERSON,
        must_change_password=False,
    )
    with pytest.raises(PermissionDeniedError):
        service.void(sale.id, salesperson, reason="Cancelled")


def test_a_sale_cannot_be_voided_twice(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    batch = _batch(db_session, owner, supplier, product, batch_number="VOID-5")
    sale = _sale(db_session, owner, batch, quantity=1, customer=customer)
    service = PesticideSaleService(db_session)
    service.void(sale.id, owner, reason="First")
    db_session.flush()
    with pytest.raises(ConflictError, match="completed sale"):
        service.void(sale.id, owner, reason="Second")


def test_reversing_a_payment_restores_the_invoice_and_balance(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    """A mistyped amount must be correctable without editing history."""

    dealer = _dealer(db_session, owner)
    batch = _batch(db_session, owner, supplier, product, batch_number="REV-1")
    sale = _sale(db_session, owner, batch, quantity=10, dealer=dealer)
    service = DealerAccountService(db_session)
    service.record_payment(
        dealer.id, actor=owner, method=PaymentMethod.CASH, amount=Decimal("1500.00")
    )
    db_session.flush()
    assert sale.remaining_amount == Decimal("0.00")
    assert dealer.balance == Decimal("0.00")

    original = db_session.scalars(
        select(Payment).where(
            Payment.dealer_id == dealer.id, Payment.direction == PaymentDirection.INCOMING
        )
    ).one()
    reversal = service.reverse_payment(original.id, actor=owner, reason="Typed 1500 not 150")
    db_session.flush()

    assert reversal.direction is PaymentDirection.OUTGOING
    assert reversal.amount == original.amount
    assert sale.remaining_amount == Decimal("1500.00")
    assert sale.payment_status is PaymentStatus.UNPAID
    assert dealer.balance == Decimal("1500.00")
    # The original entry is untouched.
    assert original.direction is PaymentDirection.INCOMING


def test_account_credit_can_be_reversed(db_session: Session, owner: AuthenticatedUser) -> None:
    dealer = _dealer(db_session, owner)
    service = DealerAccountService(db_session)
    service.record_payment(
        dealer.id, actor=owner, method=PaymentMethod.CASH, amount=Decimal("800.00")
    )
    db_session.flush()
    assert dealer.balance == Decimal("-800.00")
    credit = db_session.scalars(
        select(Payment).where(Payment.dealer_id == dealer.id, Payment.sale_id.is_(None))
    ).one()
    service.reverse_payment(credit.id, actor=owner, reason="Paid by the wrong dealer")
    db_session.flush()
    assert dealer.balance == Decimal("0.00")


def test_a_payment_cannot_be_reversed_twice(db_session: Session, owner: AuthenticatedUser) -> None:
    dealer = _dealer(db_session, owner)
    service = DealerAccountService(db_session)
    service.record_payment(
        dealer.id, actor=owner, method=PaymentMethod.CASH, amount=Decimal("200.00")
    )
    db_session.flush()
    payment = db_session.scalars(
        select(Payment).where(
            Payment.dealer_id == dealer.id, Payment.direction == PaymentDirection.INCOMING
        )
    ).one()
    service.reverse_payment(payment.id, actor=owner, reason="Duplicate entry")
    db_session.flush()
    with pytest.raises(ConflictError, match="already been reversed"):
        service.reverse_payment(payment.id, actor=owner, reason="Again")


def test_a_reversal_itself_cannot_be_reversed(
    db_session: Session, owner: AuthenticatedUser
) -> None:
    dealer = _dealer(db_session, owner)
    service = DealerAccountService(db_session)
    service.record_payment(
        dealer.id, actor=owner, method=PaymentMethod.CASH, amount=Decimal("200.00")
    )
    db_session.flush()
    payment = db_session.scalars(
        select(Payment).where(
            Payment.dealer_id == dealer.id, Payment.direction == PaymentDirection.INCOMING
        )
    ).one()
    reversal = service.reverse_payment(payment.id, actor=owner, reason="Wrong")
    db_session.flush()
    with pytest.raises(ConflictError, match="received payment"):
        service.reverse_payment(reversal.id, actor=owner, reason="No")


def test_reversal_requires_a_reason(db_session: Session, owner: AuthenticatedUser) -> None:
    dealer = _dealer(db_session, owner)
    service = DealerAccountService(db_session)
    service.record_payment(
        dealer.id, actor=owner, method=PaymentMethod.CASH, amount=Decimal("50.00")
    )
    db_session.flush()
    payment = db_session.scalars(select(Payment).where(Payment.dealer_id == dealer.id)).one()
    with pytest.raises(ValidationError, match="reason"):
        service.reverse_payment(payment.id, actor=owner, reason=" ")


def test_a_reversal_shows_on_the_statement_and_restores_the_total(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    dealer = _dealer(db_session, owner)
    batch = _batch(db_session, owner, supplier, product, batch_number="REV-2")
    _sale(db_session, owner, batch, quantity=10, dealer=dealer)
    service = DealerAccountService(db_session)
    service.record_payment(
        dealer.id, actor=owner, method=PaymentMethod.CASH, amount=Decimal("600.00")
    )
    db_session.flush()
    payment = db_session.scalars(
        select(Payment).where(
            Payment.dealer_id == dealer.id, Payment.direction == PaymentDirection.INCOMING
        )
    ).one()
    service.reverse_payment(payment.id, actor=owner, reason="Wrong dealer")
    db_session.flush()

    statement = service.statement(dealer.id)
    assert statement.paid == Decimal("0.00")
    assert statement.outstanding == Decimal("1500.00")
    assert statement.entries[-1].balance == Decimal("1500.00")
    assert any("reversed" in entry.detail for entry in statement.entries)


def test_statement_range_carries_an_opening_balance(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    """A window must not read as though the dealer started from zero."""

    dealer = _dealer(db_session, owner)
    batch = _batch(db_session, owner, supplier, product, batch_number="RANGE-1")
    old_sale = PesticideSaleService(db_session).create(
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(batch.id, 4),),
            payments=(),
            dealer_id=dealer.id,
            sale_date=datetime.now(UTC) - timedelta(days=40),
        ),
        owner,
    )
    recent = _sale(db_session, owner, batch, quantity=2, dealer=dealer)
    db_session.flush()

    window = DateRange.local_days(date.today() - timedelta(days=7), date.today(), TIMEZONE)
    statement = DealerAccountService(db_session).statement(dealer.id, period=window)
    assert [entry.reference for entry in statement.entries] == [recent.invoice_number]
    assert statement.opening_balance == old_sale.total
    assert statement.entries[-1].balance == old_sale.total + recent.total
    # Whole-account totals stay complete.
    assert statement.invoiced == old_sale.total + recent.total


def test_voiding_a_missing_sale_is_reported(db_session: Session, owner: AuthenticatedUser) -> None:
    from app.utils.exceptions import NotFoundError

    with pytest.raises(NotFoundError):
        PesticideSaleService(db_session).void(uuid.uuid4(), owner, reason="Nope")
