"""Deleting catalogue records, and the guards that keep history intact."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.dealer import Dealer
from app.models.enums import InventoryTransactionType, PaymentMethod, UserRole
from app.models.inventory import StockBatch, StockMovement
from app.models.product import Product
from app.models.supplier import Supplier
from app.security.authentication import AuthenticatedUser
from app.services.catalog_service import DealerService, ProductService, SupplierService
from app.services.dto import (
    CreatePesticideSaleCommand,
    CreateStockPurchaseCommand,
    PaymentInput,
    PesticideSaleLineInput,
    PurchasedBatchInput,
)
from app.services.pesticide_sale_service import PesticideSaleService
from app.services.stock_inventory_service import StockInventoryService
from app.services.stock_purchase_service import StockPurchaseService
from app.utils.exceptions import ConflictError, PermissionDeniedError, ValidationError


def _stock(
    session: Session,
    actor: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    *,
    batch_number: str,
    quantity: int = 10,
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
                    cartons=1,
                    packs_per_carton=quantity,
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


def _dealer(session: Session, owner: AuthenticatedUser, name: str = "Test Dealer") -> Dealer:
    dealer = DealerService(session).create(actor=owner, name=name, phone="03001234567")
    session.flush()
    return dealer


def test_unused_dealer_can_be_deleted(db_session: Session, owner: AuthenticatedUser) -> None:
    dealer = _dealer(db_session, owner)
    DealerService(db_session).delete(dealer.id, actor=owner)
    db_session.flush()
    assert db_session.get(Dealer, dealer.id) is None


def test_dealer_with_sales_history_is_kept(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    dealer = _dealer(db_session, owner, "Trading Dealer")
    batch = _stock(db_session, owner, supplier, product, batch_number="DEALER-SALE")
    PesticideSaleService(db_session).create(
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(batch.id, 1),),
            payments=(PaymentInput(PaymentMethod.CASH, Decimal("150.00")),),
            dealer_id=dealer.id,
        ),
        owner,
    )
    db_session.flush()
    with pytest.raises(ConflictError, match="sales history"):
        DealerService(db_session).delete(dealer.id, actor=owner)
    assert db_session.get(Dealer, dealer.id) is not None


def test_dealer_with_outstanding_balance_is_kept(
    db_session: Session, owner: AuthenticatedUser
) -> None:
    dealer = _dealer(db_session, owner, "Indebted Dealer")
    dealer.balance = Decimal("500.00")
    db_session.flush()
    with pytest.raises(ConflictError, match="outstanding balance"):
        DealerService(db_session).delete(dealer.id, actor=owner)


def test_supplier_with_purchase_history_is_kept(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    _stock(db_session, owner, supplier, product, batch_number="SUPPLIER-KEEP")
    with pytest.raises(ConflictError, match="purchase history"):
        SupplierService(db_session).delete(supplier.id, actor=owner)


def test_unused_supplier_can_be_deleted(db_session: Session, owner: AuthenticatedUser) -> None:
    supplier = SupplierService(db_session).create(
        actor=owner, name="Unused Supplier", phone="03007654321"
    )
    db_session.flush()
    SupplierService(db_session).delete(supplier.id, actor=owner)
    db_session.flush()
    assert db_session.get(Supplier, supplier.id) is None


def test_unstocked_product_can_be_deleted(
    db_session: Session, owner: AuthenticatedUser, product: Product
) -> None:
    ProductService(db_session).delete(product.id, actor=owner)
    db_session.flush()
    assert db_session.get(Product, product.id) is None


def test_stocked_product_is_kept(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    _stock(db_session, owner, supplier, product, batch_number="PRODUCT-KEEP")
    with pytest.raises(ConflictError, match="stock batches or purchase history"):
        ProductService(db_session).delete(product.id, actor=owner)
    assert db_session.get(Product, product.id) is not None


def test_deleting_a_customer_with_sales_is_refused(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    from app.services.catalog_service import CustomerService

    batch = _stock(db_session, owner, supplier, product, batch_number="CUSTOMER-KEEP")
    PesticideSaleService(db_session).create(
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(batch.id, 1),),
            payments=(PaymentInput(PaymentMethod.CASH, Decimal("150.00")),),
            customer_id=customer.id,
        ),
        owner,
    )
    db_session.flush()
    with pytest.raises(ConflictError, match="sales history"):
        CustomerService(db_session).delete(customer.id, actor=owner)


def test_a_salesperson_cannot_delete_products(
    db_session: Session, owner: AuthenticatedUser, product: Product
) -> None:
    salesperson = AuthenticatedUser(
        id=owner.id,
        username="sales",
        full_name="Sales Person",
        email="sales@test.invalid",
        role=UserRole.SALESPERSON,
        must_change_password=False,
    )
    with pytest.raises(PermissionDeniedError):
        ProductService(db_session).delete(product.id, actor=salesperson)


def test_batch_details_can_be_corrected(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    batch = _stock(db_session, owner, supplier, product, batch_number="EDIT-ME")
    updated = StockInventoryService(db_session).update_batch(
        batch.id,
        actor=owner,
        batch_number="corrected-01",
        manufacture_date=date(2025, 2, 1),
        expiry_date=date(2030, 2, 1),
        cartons=4,
        packs_per_carton=5,
        purchase_price=Decimal("110.00"),
        selling_price=Decimal("165.00"),
        location="Shelf B2",
        notes="Relabelled",
    )
    db_session.flush()
    assert updated.batch_number == "CORRECTED-01"
    assert updated.expiry_date == date(2030, 2, 1)
    assert updated.selling_price == Decimal("165.00")
    assert updated.location == "Shelf B2"
    # Quantities are only ever changed through an audited adjustment.
    assert updated.quantity_available == 10
    assert updated.quantity_received == 10


def test_batch_edit_rejects_an_expiry_before_manufacture(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    batch = _stock(db_session, owner, supplier, product, batch_number="BAD-DATES")
    with pytest.raises(ValidationError, match="Expiry date"):
        StockInventoryService(db_session).update_batch(
            batch.id,
            actor=owner,
            batch_number="BAD-DATES",
            manufacture_date=date(2027, 1, 1),
            expiry_date=date(2026, 1, 1),
            cartons=0,
            packs_per_carton=0,
            purchase_price=Decimal("100.00"),
            selling_price=Decimal("150.00"),
        )


def test_batch_edit_rejects_a_duplicate_batch_number(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    _stock(db_session, owner, supplier, product, batch_number="FIRST")
    second = _stock(db_session, owner, supplier, product, batch_number="SECOND")
    with pytest.raises(ConflictError, match="batch number"):
        StockInventoryService(db_session).update_batch(
            second.id,
            actor=owner,
            batch_number="FIRST",
            manufacture_date=None,
            expiry_date=None,
            cartons=0,
            packs_per_carton=0,
            purchase_price=Decimal("100.00"),
            selling_price=Decimal("150.00"),
        )


def test_removing_a_batch_writes_off_its_stock_and_keeps_the_ledger(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    """A batch row survives removal because invoices and movements refer to it."""

    batch = _stock(db_session, owner, supplier, product, batch_number="REMOVE-ME")
    removed = StockInventoryService(db_session).delete_batch(
        batch.id, actor=owner, reason="Damaged in the store"
    )
    db_session.flush()
    assert removed.quantity_available == 0
    assert removed.is_active is False
    assert db_session.get(StockBatch, batch.id) is not None
    movements = list(
        db_session.scalars(
            select(StockMovement)
            .where(StockMovement.batch_id == batch.id)
            .order_by(StockMovement.created_at)
        )
    )
    assert [m.transaction_type for m in movements] == [
        InventoryTransactionType.PURCHASE,
        InventoryTransactionType.ADJUSTMENT,
    ]
    assert movements[-1].quantity_change == -10
    assert movements[-1].balance_after == 0
    assert movements[-1].notes == "Damaged in the store"


def test_removing_a_batch_requires_a_reason(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    batch = _stock(db_session, owner, supplier, product, batch_number="NO-REASON")
    with pytest.raises(ValidationError, match="reason"):
        StockInventoryService(db_session).delete_batch(batch.id, actor=owner, reason="   ")


def test_a_batch_cannot_be_removed_twice(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    batch = _stock(db_session, owner, supplier, product, batch_number="TWICE")
    service = StockInventoryService(db_session)
    service.delete_batch(batch.id, actor=owner, reason="First removal")
    db_session.flush()
    with pytest.raises(ConflictError, match="already been removed"):
        service.delete_batch(batch.id, actor=owner, reason="Second removal")


def test_batch_is_sold_reports_invoice_usage(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    batch = _stock(db_session, owner, supplier, product, batch_number="SOLD-CHECK")
    service = StockInventoryService(db_session)
    assert service.batch_is_sold(batch.id) is False
    PesticideSaleService(db_session).create(
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(batch.id, 1),),
            payments=(PaymentInput(PaymentMethod.CASH, Decimal("150.00")),),
            customer_id=customer.id,
        ),
        owner,
    )
    db_session.flush()
    assert service.batch_is_sold(batch.id) is True
