from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.damage import DamageRecord
from app.models.enums import (
    DamageStatus,
    DamageType,
    PaymentDirection,
    PaymentMethod,
    PaymentStatus,
    PhoneStatus,
    SaleStatus,
    UserRole,
)
from app.models.inventory import PhoneInventory
from app.models.payment import Payment
from app.models.product import Product
from app.models.supplier import Supplier
from app.security.authentication import AuthenticatedUser
from app.services.damage_service import DamageService
from app.services.dto import (
    CreateDamageCommand,
    CreatePurchaseCommand,
    CreateSaleCommand,
    PaymentInput,
    PurchasedPhoneInput,
    SaleLineInput,
)
from app.services.inventory_service import InventoryAdministrationService
from app.services.payment_service import PaymentService
from app.services.purchase_service import PurchaseService
from app.services.sale_service import SaleService
from app.utils.exceptions import ConflictError, PermissionDeniedError, ValidationError


def test_sale_void_restocks_and_refunds_without_deletion(
    db_session: Session,
    owner: AuthenticatedUser,
    purchase_phone: object,
) -> None:
    phone: PhoneInventory = purchase_phone("350000000000601", None)  # type: ignore[operator]
    service = SaleService(db_session)
    sale = service.create(
        CreateSaleCommand(
            lines=(SaleLineInput(phone.imei_1),),
            payments=(PaymentInput(PaymentMethod.CARD, phone.selling_price),),
        ),
        owner,
    )
    service.void(sale.id, owner, refund_method=PaymentMethod.CARD, reason="Operator correction")
    db_session.flush()
    assert sale.status is SaleStatus.VOIDED
    assert sale.payment_status is PaymentStatus.REFUNDED
    assert phone.status is PhoneStatus.IN_STOCK
    refunds = db_session.scalars(
        select(Payment).where(
            Payment.sale_id == sale.id,
            Payment.direction == PaymentDirection.OUTGOING,
        )
    ).all()
    assert len(refunds) == 1
    assert refunds[0].amount == sale.paid_amount
    with pytest.raises(ConflictError, match="completed"):
        service.void(sale.id, owner, reason="Second attempt")


def test_damage_repair_lifecycle(
    db_session: Session,
    owner: AuthenticatedUser,
    purchase_phone: object,
) -> None:
    phone: PhoneInventory = purchase_phone("350000000000602", None)  # type: ignore[operator]
    service = DamageService(db_session)
    damage = service.create(
        CreateDamageCommand(
            imei=phone.imei_1,
            damage_type=DamageType.CAMERA,
            description="Camera module failed",
            estimated_loss=Decimal("10000.00"),
        ),
        owner,
    )
    service.send_for_repair(damage.id, owner)
    assert phone.status is PhoneStatus.SENT_FOR_REPAIR
    repaired = service.complete_repair(
        damage.id,
        owner,
        repair_cost=Decimal("4500.00"),
        resolution="Camera module replaced",
    )
    assert repaired.status is DamageStatus.REPAIRED
    assert phone.status is PhoneStatus.IN_STOCK
    assert db_session.get(DamageRecord, damage.id) is damage
    with pytest.raises(ConflictError, match="under repair"):
        service.complete_repair(
            damage.id,
            owner,
            repair_cost=Decimal("0.00"),
            resolution="Duplicate completion",
        )


def test_inventory_adjustment_and_price_change_are_permissioned(
    db_session: Session,
    owner: AuthenticatedUser,
    purchase_phone: object,
) -> None:
    phone: PhoneInventory = purchase_phone("350000000000603", None)  # type: ignore[operator]
    service = InventoryAdministrationService(db_session)
    service.change_selling_price(
        phone.imei_1,
        Decimal("130000.00"),
        actor=owner,
        reason="Updated market price",
    )
    service.adjust_status(
        phone.imei_1,
        PhoneStatus.LOST,
        actor=owner,
        reason="Missing during stock count",
    )
    service.adjust_status(
        phone.imei_1,
        PhoneStatus.IN_STOCK,
        actor=owner,
        reason="Recovered",
    )
    assert phone.selling_price == Decimal("130000.00")
    assert phone.status is PhoneStatus.IN_STOCK
    salesperson = replace(owner, role=UserRole.SALESPERSON)
    with pytest.raises(PermissionDeniedError):
        service.adjust_status(
            phone.imei_1,
            PhoneStatus.DAMAGED,
            actor=salesperson,
            reason="No permission",
        )
    with pytest.raises(ValidationError, match="reason"):
        service.change_selling_price(
            phone.imei_1,
            Decimal("1.00"),
            actor=owner,
            reason=" ",
        )


def test_partial_supplier_payment_updates_document_and_balance(
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
                    imei_1="350000000000604",
                    purchase_price=Decimal("100000.00"),
                    selling_price=Decimal("125000.00"),
                ),
            ),
            payments=(),
        ),
        owner,
    )
    assert supplier.balance == Decimal("100000.00")
    payment = PaymentService(db_session).pay_supplier(
        purchase.id,
        actor=owner,
        method=PaymentMethod.BANK_TRANSFER,
        amount=Decimal("40000.00"),
        reference="BANK-OUT-1",
    )
    assert payment.direction is PaymentDirection.OUTGOING
    assert purchase.remaining_amount == Decimal("60000.00")
    assert supplier.balance == Decimal("60000.00")
    with pytest.raises(ValidationError, match="outstanding"):
        PaymentService(db_session).pay_supplier(
            purchase.id,
            actor=owner,
            method=PaymentMethod.CASH,
            amount=Decimal("60000.01"),
        )
