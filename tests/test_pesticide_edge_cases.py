from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.dealer import Dealer
from app.models.enums import PaymentMethod
from app.models.inventory import StockBatch, StockMovement
from app.models.payment import Payment
from app.models.product import Product
from app.models.supplier import Supplier
from app.reports.report_service import DateRange, ReportService
from app.security.authentication import AuthenticatedUser
from app.services.dto import (
    CreatePesticideSaleCommand,
    CreateStockPurchaseCommand,
    PaymentInput,
    PesticideSaleLineInput,
    PurchasedBatchInput,
)
from app.services.payment_service import PaymentService
from app.services.pesticide_sale_service import PesticideSaleService
from app.services.stock_purchase_service import StockPurchaseService
from app.utils.exceptions import ConflictError, ValidationError


def _purchase(
    session: Session,
    actor: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    *,
    batch_number: str,
    expiry_date: date | None = None,
    payment: Decimal = Decimal("0.00"),
) -> tuple[object, StockBatch]:
    payments = (PaymentInput(PaymentMethod.CASH, payment),) if payment > Decimal("0.00") else ()
    purchase = StockPurchaseService(session).create(
        CreateStockPurchaseCommand(
            supplier_id=supplier.id,
            batches=(
                PurchasedBatchInput(
                    product_id=product.id,
                    batch_number=batch_number,
                    quantity=10,
                    purchase_price=Decimal("100.00"),
                    selling_price=Decimal("150.00"),
                    manufacture_date=date(2025, 1, 1),
                    expiry_date=expiry_date,
                ),
            ),
            payments=payments,
        ),
        actor,
    )
    session.flush()
    batch = session.scalar(select(StockBatch).where(StockBatch.purchase_id == purchase.id))
    assert batch is not None
    return purchase, batch


def test_expired_batch_cannot_be_sold(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    _purchase_document, batch = _purchase(
        db_session,
        owner,
        supplier,
        product,
        batch_number="EXPIRED-01",
        expiry_date=date.today() - timedelta(days=1),
    )

    with pytest.raises(ConflictError, match="expired"):
        PesticideSaleService(db_session).create(
            CreatePesticideSaleCommand(
                lines=(PesticideSaleLineInput(batch.id, 1),),
                payments=(),
            ),
            owner,
        )

    assert batch.quantity_available == 10


def test_dealer_payment_reduces_account_balance(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    _purchase_document, batch = _purchase(
        db_session, owner, supplier, product, batch_number="DEALER-PAY-01"
    )
    dealer = Dealer(
        name="Trade Dealer",
        phone="0300-5555555",
        credit_limit=Decimal("1000.00"),
        balance=Decimal("0.00"),
        is_active=True,
    )
    db_session.add(dealer)
    db_session.flush()
    sale = PesticideSaleService(db_session).create(
        CreatePesticideSaleCommand(
            dealer_id=dealer.id,
            lines=(PesticideSaleLineInput(batch.id, 2),),
            payments=(),
        ),
        owner,
    )
    db_session.flush()
    assert dealer.balance == Decimal("300.00")

    PaymentService(db_session).collect_sale_payment(
        sale.id,
        actor=owner,
        method=PaymentMethod.BANK_TRANSFER,
        amount=Decimal("125.00"),
    )

    assert sale.remaining_amount == Decimal("175.00")
    assert dealer.balance == Decimal("175.00")


def test_profit_subtracts_order_discount_and_excludes_tax(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    _purchase_document, batch = _purchase(
        db_session, owner, supplier, product, batch_number="PROFIT-01"
    )
    PesticideSaleService(db_session).create(
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(batch.id, 2),),
            payments=(),
            order_discount=Decimal("30.00"),
            tax=Decimal("27.00"),
        ),
        owner,
    )
    db_session.flush()

    period = DateRange.today("Asia/Karachi")
    reports = ReportService(db_session)
    assert reports.profit(period) == Decimal("70.00")
    assert reports.financial_by_day(period, "Asia/Karachi")[0].profit == Decimal("70.00")


def test_purchase_rejects_invalid_batches_and_overpayment(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    service = StockPurchaseService(db_session)
    with pytest.raises(ValidationError, match="at least one"):
        service.create(CreateStockPurchaseCommand(supplier_id=supplier.id, batches=()), owner)

    invalid_date = PurchasedBatchInput(
        product_id=product.id,
        batch_number="BAD-DATE",
        quantity=1,
        purchase_price=Decimal("100.00"),
        selling_price=Decimal("150.00"),
        manufacture_date=date(2027, 1, 1),
        expiry_date=date(2026, 1, 1),
    )
    with pytest.raises(ValidationError, match="Expiry"):
        service.create(
            CreateStockPurchaseCommand(supplier_id=supplier.id, batches=(invalid_date,)), owner
        )

    duplicate = PurchasedBatchInput(
        product_id=product.id,
        batch_number="DUPLICATE",
        quantity=1,
        purchase_price=Decimal("100.00"),
        selling_price=Decimal("150.00"),
    )
    with pytest.raises(ConflictError, match="more than once"):
        service.create(
            CreateStockPurchaseCommand(
                supplier_id=supplier.id,
                batches=(duplicate, duplicate),
            ),
            owner,
        )

    with pytest.raises(ValidationError, match="cannot exceed"):
        service.create(
            CreateStockPurchaseCommand(
                supplier_id=supplier.id,
                batches=(duplicate,),
                payments=(PaymentInput(PaymentMethod.CASH, Decimal("101.00")),),
            ),
            owner,
        )


def test_sale_rejects_ambiguous_duplicate_and_overpaid_orders(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    _purchase_document, batch = _purchase(
        db_session, owner, supplier, product, batch_number="SALE-GUARDS-01"
    )
    dealer = Dealer(
        name="Guard Dealer",
        phone="0300-6666666",
        credit_limit=Decimal("1000.00"),
        balance=Decimal("0.00"),
        is_active=True,
    )
    db_session.add(dealer)
    db_session.flush()
    service = PesticideSaleService(db_session)

    with pytest.raises(ValidationError, match="at least one"):
        service.create(CreatePesticideSaleCommand(lines=(), payments=()), owner)
    with pytest.raises(ValidationError, match="either"):
        service.create(
            CreatePesticideSaleCommand(
                customer_id=customer.id,
                dealer_id=dealer.id,
                lines=(PesticideSaleLineInput(batch.id, 1),),
                payments=(),
            ),
            owner,
        )
    with pytest.raises(ConflictError, match="only once"):
        service.create(
            CreatePesticideSaleCommand(
                lines=(
                    PesticideSaleLineInput(batch.id, 1),
                    PesticideSaleLineInput(batch.id, 1),
                ),
                payments=(),
            ),
            owner,
        )
    with pytest.raises(ValidationError, match="cannot exceed"):
        service.create(
            CreatePesticideSaleCommand(
                lines=(PesticideSaleLineInput(batch.id, 1),),
                payments=(PaymentInput(PaymentMethod.CASH, Decimal("151.00")),),
            ),
            owner,
        )


def test_database_rejects_payment_mutation(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    _purchase_document, _batch = _purchase(
        db_session,
        owner,
        supplier,
        product,
        batch_number="IMMUTABLE-PAYMENT",
        payment=Decimal("100.00"),
    )
    payment_id = db_session.scalar(select(Payment.id))
    assert payment_id is not None

    with pytest.raises(DBAPIError):
        db_session.execute(
            update(Payment).where(Payment.id == payment_id).values(amount=Decimal("1.00"))
        )


def test_database_rejects_stock_movement_mutation(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    _purchase_document, _batch = _purchase(
        db_session, owner, supplier, product, batch_number="IMMUTABLE-STOCK"
    )
    movement_id = db_session.scalar(select(StockMovement.id))
    assert movement_id is not None

    with pytest.raises(DBAPIError):
        db_session.execute(
            update(StockMovement).where(StockMovement.id == movement_id).values(notes="tampered")
        )


def test_sale_date_controls_expiry_validation(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    _purchase_document, batch = _purchase(
        db_session,
        owner,
        supplier,
        product,
        batch_number="HISTORICAL-01",
        expiry_date=date(2025, 12, 31),
    )
    historical_date = datetime(2025, 6, 1, 12, tzinfo=UTC)
    sale = PesticideSaleService(db_session).create(
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(batch.id, 1),),
            payments=(),
            sale_date=historical_date,
        ),
        owner,
    )

    assert sale.sale_date == historical_date
