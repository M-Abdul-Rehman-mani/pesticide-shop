"""TEST records are usable everywhere but never reach a dashboard, report, or email."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.dealer import Dealer
from app.models.enums import PaymentMethod
from app.models.payment import Payment
from app.models.product import Product
from app.models.purchase import Purchase
from app.models.sale import Sale
from app.models.supplier import Supplier
from app.reports.live_data import live_payment, live_purchase, live_sale
from app.reports.report_service import DateRange, ReportService
from app.security.authentication import AuthenticatedUser
from app.services.dto import (
    CreatePesticideSaleCommand,
    CreateStockPurchaseCommand,
    PaymentInput,
    PesticideSaleLineInput,
    PurchasedBatchInput,
)
from app.services.pesticide_sale_service import PesticideSaleService
from app.services.practice_records import TEST_STOCK_UNITS, ensure_practice_records
from app.services.stock_purchase_service import StockPurchaseService
from app.utils.exceptions import ValidationError
from tests.test_dashboard import TIMEZONE, _stock


def _count(session: Session, model: type[object]) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def test_practice_records_are_created_once(db_session: Session, owner: AuthenticatedUser) -> None:
    first = ensure_practice_records(db_session, owner)
    assert set(first.created) == {
        "customer",
        "dealer",
        "supplier",
        "product",
        f"{TEST_STOCK_UNITS} units of stock",
    }
    assert all(
        record.is_test for record in (first.customer, first.dealer, first.supplier, first.product)
    )
    assert first.batch.quantity_available == TEST_STOCK_UNITS

    counts = [_count(db_session, model) for model in (Customer, Dealer, Supplier, Product)]
    again = ensure_practice_records(db_session, owner)
    assert again.created == ()
    assert "nothing to add" in again.summary()
    assert again.product.id == first.product.id
    assert [_count(db_session, m) for m in (Customer, Dealer, Supplier, Product)] == counts


def test_test_activity_never_reaches_any_figure(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    practice = ensure_practice_records(db_session, owner)
    real = _stock(db_session, owner, supplier, product, batch_number="REAL", quantity=40)
    sales = PesticideSaleService(db_session)

    def sell(batch_id: object, quantity: int, amount: str, **party: object) -> None:
        sales.create(
            CreatePesticideSaleCommand(
                lines=(PesticideSaleLineInput(batch_id, quantity),),  # type: ignore[arg-type]
                payments=(PaymentInput(PaymentMethod.CASH, Decimal(amount)),),
                **party,  # type: ignore[arg-type]
            ),
            owner,
        )

    sell(real.id, 2, "300.00", customer_id=customer.id)  # the only real sale
    sell(practice.batch.id, 5, "750.00", dealer_id=practice.dealer.id)
    sell(practice.batch.id, 3, "450.00", customer_id=practice.customer.id)
    sell(practice.batch.id, 7, "1050.00")  # walk-in
    db_session.flush()

    reports = ReportService(db_session)
    period = DateRange.today(TIMEZONE)
    metrics = reports.dashboard(period)
    assert metrics.sales == Decimal("300.00")
    assert metrics.units_sold == 2
    assert metrics.profit == Decimal("100.00")
    # 40 received - 2 sold; the TEST product's stock is left out.
    assert metrics.current_inventory == 38
    assert reports.payment_breakdown(period) == {"CASH": Decimal("300.00")}
    assert [p.id for p in reports.products()] == [product.id]
    assert [row.id for row in reports.product_catalogue(period)] == [product.id]
    assert [model.product for model in reports.top_selling_models(period)] == [product.display_name]
    assert sum(reports.inventory_status().values()) == 38

    customers = reports.customer_dashboard(period)
    assert (customers.parties, customers.invoices, customers.sales) == (1, 1, Decimal("300.00"))
    dealers = reports.dealer_dashboard(period)
    assert (dealers.parties, dealers.invoices, dealers.sales) == (0, 0, Decimal("0.00"))
    assert dealers.outstanding == Decimal("0.00")
    assert [row.name for row in reports.top_customers(period)] == [customer.name]
    assert reports.top_dealers(period) == []

    # The records themselves are still there to be used.
    assert _count(db_session, Sale) == 4
    live = db_session.scalar(select(func.count()).select_from(Sale).where(live_sale()))
    assert live == 1
    paid = db_session.scalar(select(func.count()).select_from(Payment).where(live_payment()))
    assert paid == 1, "only the real sale was paid; the three test payments are hidden"
    real_purchases = db_session.scalars(select(Purchase.id).where(live_purchase())).all()
    assert real_purchases == [real.purchase_id]


def test_test_and_real_trading_are_never_mixed(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    """A mixed document would hide real revenue yet keep its effect on real balances."""

    practice = ensure_practice_records(db_session, owner)
    real = _stock(db_session, owner, supplier, product, batch_number="REAL", quantity=40)
    sales = PesticideSaleService(db_session)

    def sale(*batches: object, **party: object) -> CreatePesticideSaleCommand:
        return CreatePesticideSaleCommand(
            lines=tuple(PesticideSaleLineInput(b, 1) for b in batches),  # type: ignore[arg-type]
            payments=(),
            **party,  # type: ignore[arg-type]
        )

    with pytest.raises(ValidationError, match="same invoice"):
        sales.create(sale(real.id, practice.batch.id), owner)
    with pytest.raises(ValidationError, match="only buy TEST products"):
        sales.create(sale(real.id, dealer_id=practice.dealer.id), owner)
    with pytest.raises(ValidationError, match="only buy TEST products"):
        sales.create(sale(real.id, customer_id=practice.customer.id), owner)
    with pytest.raises(ValidationError, match="only be sold to a TEST"):
        sales.create(sale(practice.batch.id, customer_id=customer.id), owner)

    purchases = StockPurchaseService(db_session)

    def bill(supplier_id: object, product_id: object) -> CreateStockPurchaseCommand:
        return CreateStockPurchaseCommand(
            supplier_id=supplier_id,  # type: ignore[arg-type]
            batches=(
                PurchasedBatchInput(
                    product_id=product_id,  # type: ignore[arg-type]
                    batch_number="MIX-1",
                    quantity=1,
                    purchase_price=Decimal("1.00"),
                    selling_price=Decimal("2.00"),
                ),
            ),
        )

    with pytest.raises(ValidationError, match="TEST supplier can only supply"):
        purchases.create(bill(practice.supplier.id, product.id), owner)
    with pytest.raises(ValidationError, match="only be purchased from the TEST supplier"):
        purchases.create(bill(supplier.id, practice.product.id), owner)


def test_practice_records_survive_renames_deactivation_and_selling_out(
    db_session: Session, owner: AuthenticatedUser
) -> None:
    first = ensure_practice_records(db_session, owner)
    first.product.name = "Renamed practice product"
    first.product.is_active = False
    first.dealer.name = "Renamed practice dealer"
    first.dealer.is_active = False
    first.batch.quantity_available = 0
    db_session.flush()

    again = ensure_practice_records(db_session, owner)
    assert again.product.id == first.product.id, "found by flag, not by name"
    assert again.dealer.id == first.dealer.id
    assert again.product.is_active and again.dealer.is_active
    assert again.batch.id != first.batch.id, "sold out, so restocked"
    assert again.batch.batch_number == "TEST-BATCH-002"
    assert again.batch.quantity_available == TEST_STOCK_UNITS
    assert "product reactivated" in again.created
