from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.enums import (
    DamageType,
    PaymentMethod,
    PaymentStatus,
    PhoneStatus,
    ReturnCondition,
    ReturnReason,
)
from app.models.inventory import InventoryTransaction, PhoneInventory
from app.models.return_record import ReturnItem
from app.security.authentication import AuthenticatedUser
from app.services.damage_service import DamageService
from app.services.dto import (
    CreateDamageCommand,
    CreateReturnCommand,
    CreateSaleCommand,
    PaymentInput,
    SaleLineInput,
)
from app.services.return_service import ReturnService
from app.services.sale_service import SaleService
from app.utils.exceptions import ConflictError, NotFoundError, ValidationError


def _sold_phone(
    db_session: Session,
    owner: AuthenticatedUser,
    customer: Customer,
    purchase_phone: object,
    imei: str,
) -> tuple[PhoneInventory, object]:
    phone: PhoneInventory = purchase_phone(imei, None)  # type: ignore[operator]
    sale = SaleService(db_session).create(
        CreateSaleCommand(
            customer_id=customer.id,
            lines=(SaleLineInput(phone.imei_1),),
            payments=(PaymentInput(PaymentMethod.CASH, Decimal("125000.00")),),
        ),
        owner,
    )
    db_session.flush()
    return phone, sale


def test_valid_good_return_restocks_phone(
    db_session: Session,
    owner: AuthenticatedUser,
    customer: Customer,
    purchase_phone: object,
) -> None:
    phone, sale = _sold_phone(db_session, owner, customer, purchase_phone, "350000000000201")
    result = ReturnService(db_session).create(
        CreateReturnCommand(
            invoice_number=sale.invoice_number,  # type: ignore[attr-defined]
            imei=phone.imei_1,
            reason=ReturnReason.CUSTOMER_CHANGED_MIND,
            condition=ReturnCondition.GOOD,
            refund_amount=Decimal("125000.00"),
            refund_method=PaymentMethod.CASH,
            approved_by=owner.id,
        ),
        owner,
    )
    db_session.flush()
    assert phone.status is PhoneStatus.IN_STOCK
    assert sale.payment_status is PaymentStatus.REFUNDED  # type: ignore[attr-defined]
    assert db_session.scalar(select(ReturnItem.id).where(ReturnItem.return_id == result.id))
    transitions = db_session.scalars(
        select(InventoryTransaction).where(InventoryTransaction.reference_id == result.id)
    ).all()
    assert [entry.new_status for entry in transitions] == [
        PhoneStatus.RETURNED,
        PhoneStatus.IN_STOCK,
    ]


def test_damaged_return_moves_phone_to_damaged(
    db_session: Session,
    owner: AuthenticatedUser,
    customer: Customer,
    purchase_phone: object,
) -> None:
    phone, sale = _sold_phone(db_session, owner, customer, purchase_phone, "350000000000202")
    ReturnService(db_session).create(
        CreateReturnCommand(
            invoice_number=sale.invoice_number,  # type: ignore[attr-defined]
            imei=phone.imei_1,
            reason=ReturnReason.DEFECTIVE,
            condition=ReturnCondition.DAMAGED,
            refund_amount=Decimal("100000.00"),
            refund_method=PaymentMethod.BANK_TRANSFER,
            approved_by=owner.id,
        ),
        owner,
    )
    assert phone.status is PhoneStatus.DAMAGED


def test_duplicate_return_rejected(
    db_session: Session,
    owner: AuthenticatedUser,
    customer: Customer,
    purchase_phone: object,
) -> None:
    phone, sale = _sold_phone(db_session, owner, customer, purchase_phone, "350000000000203")
    command = CreateReturnCommand(
        invoice_number=sale.invoice_number,  # type: ignore[attr-defined]
        imei=phone.imei_1,
        reason=ReturnReason.OTHER,
        condition=ReturnCondition.GOOD,
        refund_amount=Decimal("1000.00"),
        refund_method=PaymentMethod.CASH,
        approved_by=owner.id,
    )
    ReturnService(db_session).create(command, owner)
    db_session.flush()
    with pytest.raises(ConflictError, match="already been returned"):
        ReturnService(db_session).create(command, owner)


def test_invalid_invoice_and_over_refund_rejected(
    db_session: Session,
    owner: AuthenticatedUser,
    customer: Customer,
    purchase_phone: object,
) -> None:
    phone, sale = _sold_phone(db_session, owner, customer, purchase_phone, "350000000000204")
    common = {
        "imei": phone.imei_1,
        "reason": ReturnReason.OTHER,
        "condition": ReturnCondition.GOOD,
        "refund_method": PaymentMethod.CASH,
        "approved_by": owner.id,
    }
    with pytest.raises(NotFoundError, match="invoice"):
        ReturnService(db_session).create(
            CreateReturnCommand(
                invoice_number="INV-DOES-NOT-EXIST",
                refund_amount=Decimal("1.00"),
                **common,
            ),
            owner,
        )
    with pytest.raises(ValidationError, match="exceed"):
        ReturnService(db_session).create(
            CreateReturnCommand(
                invoice_number=sale.invoice_number,  # type: ignore[attr-defined]
                refund_amount=Decimal("125000.01"),
                **common,
            ),
            owner,
        )


def test_damage_changes_status_and_creates_record(
    db_session: Session,
    owner: AuthenticatedUser,
    purchase_phone: object,
) -> None:
    phone: PhoneInventory = purchase_phone("350000000000205", None)  # type: ignore[operator]
    damage = DamageService(db_session).create(
        CreateDamageCommand(
            imei=phone.imei_1,
            damage_type=DamageType.SCREEN,
            description="Cracked during stock handling",
            estimated_loss=Decimal("15000.00"),
            repair_cost=Decimal("7000.00"),
        ),
        owner,
    )
    db_session.flush()
    assert phone.status is PhoneStatus.DAMAGED
    assert damage.imei == phone.imei_1
