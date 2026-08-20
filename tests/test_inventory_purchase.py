from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models.enums import InventoryTransactionType, PhoneStatus
from app.models.inventory import InventoryTransaction, PhoneIMEI
from app.models.product import Product
from app.models.supplier import Supplier
from app.repositories.inventory_repository import InventoryRepository
from app.security.authentication import AuthenticatedUser
from app.services.dto import CreatePurchaseCommand, PurchasedPhoneInput
from app.services.purchase_service import PurchaseService
from app.utils.exceptions import ConflictError


def test_purchase_creates_phone_and_immutable_ledger(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    purchase = PurchaseService(db_session).create(
        CreatePurchaseCommand(
            supplier_id=supplier.id,
            phones=(
                PurchasedPhoneInput(
                    product_id=product.id,
                    imei_1="350000000000010",
                    imei_2="350000000000011",
                    purchase_price=Decimal("100000.00"),
                    selling_price=Decimal("125000.00"),
                ),
            ),
        ),
        owner,
    )
    db_session.flush()
    phone = InventoryRepository(db_session).find_by_imei("350000000000011")
    assert phone is not None
    assert phone.status is PhoneStatus.IN_STOCK
    assert phone.purchase_id == purchase.id
    assert db_session.scalar(select(func.count()).select_from(PhoneIMEI)) == 2
    ledger = db_session.scalars(
        select(InventoryTransaction).where(InventoryTransaction.phone_id == phone.id)
    ).one()
    assert ledger.transaction_type is InventoryTransactionType.PURCHASE
    assert ledger.quantity == 1


def test_duplicate_imei_rejected(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    purchase_phone: object,
) -> None:
    creator = purchase_phone
    creator("350000000000020", "350000000000021")  # type: ignore[operator]
    with pytest.raises(ConflictError, match="IMEI"):
        PurchaseService(db_session).create(
            CreatePurchaseCommand(
                supplier_id=supplier.id,
                phones=(
                    PurchasedPhoneInput(
                        product_id=product.id,
                        imei_1="350000000000021",
                        purchase_price=Decimal("1.00"),
                        selling_price=Decimal("2.00"),
                    ),
                ),
            ),
            owner,
        )


def test_database_rejects_cross_slot_imei_collision(
    db_session: Session,
    purchase_phone: object,
) -> None:
    creator = purchase_phone
    first = creator("350000000000030", "350000000000031")  # type: ignore[operator]
    second = creator("350000000000032", "350000000000033")  # type: ignore[operator]
    second.imei_2 = first.imei_1
    with pytest.raises(DBAPIError):
        db_session.flush()
