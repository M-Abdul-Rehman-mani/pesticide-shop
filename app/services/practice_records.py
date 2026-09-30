"""Ready-made TEST customer, dealer, product, and supplier for trying the app.

Each record is flagged ``is_test``, so everything done with it -- sales, stock,
purchases, payments, returns -- stays out of dashboards, reports, and the owner's
daily email (see ``app/reports/live_data.py``). Creating them is idempotent: a
record that already exists is reused, never duplicated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.dealer import Dealer
from app.models.inventory import StockBatch
from app.models.product import Product
from app.models.supplier import Supplier
from app.security.authentication import AuthenticatedUser
from app.services.audit_service import AuditService
from app.services.dto import CreateStockPurchaseCommand, PurchasedBatchInput
from app.services.stock_purchase_service import StockPurchaseService
from app.utils.clock import local_today

TEST_CUSTOMER_NAME = "TEST Customer"
TEST_DEALER_NAME = "TEST Dealer"
TEST_SUPPLIER_NAME = "TEST Supplier"
TEST_PRODUCT_NAME = "TEST Product"
TEST_BATCH_NUMBER = "TEST-BATCH-001"
TEST_STOCK_UNITS = 1000

_Flagged = TypeVar("_Flagged", Customer, Dealer, Supplier, Product)


@dataclass(frozen=True, slots=True)
class PracticeRecords:
    customer: Customer
    dealer: Dealer
    supplier: Supplier
    product: Product
    batch: StockBatch
    created: tuple[str, ...]

    def summary(self) -> str:
        if not self.created:
            return "Test records already exist; nothing to add."
        return (
            "Test records added (excluded from dashboards and reports): "
            + ", ".join(self.created)
            + "."
        )


def ensure_practice_records(session: Session, actor: AuthenticatedUser) -> PracticeRecords:
    """Create whichever TEST records are missing and return all of them.

    Existing records are found by their ``is_test`` flag, not their name, so a
    renamed one is still reused. A deactivated one is switched back on, and the
    TEST product is restocked when it has nothing left to sell.
    """

    created: list[str] = []

    customer = _first_flagged(session, Customer)
    if customer is None:
        customer = Customer(
            name=TEST_CUSTOMER_NAME,
            phone="0300-0000000",
            address="Practice record - not a real customer",
            notes="Created for testing. Excluded from all dashboards and reports.",
            is_test=True,
        )
        session.add(customer)
        created.append("customer")

    dealer = _first_flagged(session, Dealer)
    if dealer is None:
        dealer = Dealer(
            name=TEST_DEALER_NAME,
            business_name="TEST Dealer Traders",
            phone="0300-0000001",
            address="Practice record - not a real dealer",
            territory="TEST",
            credit_limit=Decimal("1000000.00"),
            balance=Decimal("0.00"),
            notes="Created for testing. Excluded from all dashboards and reports.",
            is_active=True,
            is_test=True,
        )
        session.add(dealer)
        created.append("dealer")
    elif not dealer.is_active:
        dealer.is_active = True
        created.append("dealer reactivated")

    supplier = _first_flagged(session, Supplier)
    if supplier is None:
        supplier = Supplier(
            name=TEST_SUPPLIER_NAME,
            company_name="TEST Supplier",
            phone="0300-0000002",
            notes="Created for testing. Excluded from all dashboards and reports.",
            is_active=True,
            is_test=True,
            balance=Decimal("0.00"),
        )
        session.add(supplier)
        created.append("supplier")
    elif not supplier.is_active:
        supplier.is_active = True
        created.append("supplier reactivated")

    product = _first_flagged(session, Product)
    if product is None:
        product = Product(
            manufacturer="TEST",
            name=TEST_PRODUCT_NAME,
            active_ingredient="Practice only",
            formulation="10% EC",
            pack_size="1-L",
            unit="PACK",
            category="HERBICIDE",
            default_purchase_price=Decimal("100.00"),
            default_sale_price=Decimal("150.00"),
            minimum_stock=0,
            description="Created for testing. Excluded from all dashboards and reports.",
            is_active=True,
            is_test=True,
        )
        session.add(product)
        created.append("product")
    elif not product.is_active:
        product.is_active = True
        created.append("product reactivated")
    session.flush()

    today = local_today()
    batch = session.scalar(
        select(StockBatch)
        .where(
            StockBatch.product_id == product.id,
            StockBatch.is_active.is_(True),
            StockBatch.quantity_available > 0,
            (StockBatch.expiry_date.is_(None) | (StockBatch.expiry_date >= today)),
        )
        .order_by(StockBatch.created_at)
        .limit(1)
    )
    if batch is None:
        held = session.scalar(
            select(func.count()).select_from(StockBatch).where(StockBatch.product_id == product.id)
        )
        batch_number = TEST_BATCH_NUMBER if not held else f"TEST-BATCH-{int(held) + 1:03d}"
        purchase = StockPurchaseService(session).create(
            CreateStockPurchaseCommand(
                supplier_id=supplier.id,
                batches=(
                    PurchasedBatchInput(
                        product_id=product.id,
                        batch_number=batch_number,
                        quantity=TEST_STOCK_UNITS,
                        manufacture_date=today,
                        expiry_date=today + timedelta(days=3 * 365),
                        purchase_price=Decimal("100.00"),
                        selling_price=Decimal("150.00"),
                    ),
                ),
                payments=(),
                notes="Practice stock for the TEST product.",
            ),
            actor,
        )
        session.flush()
        batch = session.scalar(select(StockBatch).where(StockBatch.purchase_id == purchase.id))
        assert batch is not None
        created.append(f"{TEST_STOCK_UNITS} units of stock")

    if created:
        AuditService(session).record(
            actor_id=actor.id,
            action="PRACTICE_RECORDS_CREATED",
            entity_type="Product",
            entity_id=product.id,
            new_value={"created": created},
        )
    return PracticeRecords(customer, dealer, supplier, product, batch, tuple(created))


def _first_flagged(session: Session, model: type[_Flagged]) -> _Flagged | None:
    return session.scalar(
        select(model).where(model.is_test.is_(True)).order_by(model.created_at).limit(1)
    )
