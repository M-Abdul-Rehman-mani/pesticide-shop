"""The sole application-level path for phone inventory status transitions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.enums import InventoryTransactionType, PhoneStatus
from app.models.inventory import InventoryTransaction, PhoneInventory
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, require_permission
from app.services.audit_service import AuditService
from app.utils.exceptions import ConflictError, NotFoundError, ValidationError
from app.utils.validators import nonnegative_money, validate_imei

ALLOWED_TRANSITIONS: dict[PhoneStatus, frozenset[PhoneStatus]] = {
    PhoneStatus.IN_STOCK: frozenset(
        {
            PhoneStatus.RESERVED,
            PhoneStatus.SOLD,
            PhoneStatus.DAMAGED,
            PhoneStatus.LOST,
            PhoneStatus.CANCELLED,
        }
    ),
    PhoneStatus.RESERVED: frozenset(
        {PhoneStatus.IN_STOCK, PhoneStatus.SOLD, PhoneStatus.CANCELLED}
    ),
    PhoneStatus.SOLD: frozenset({PhoneStatus.RETURNED}),
    PhoneStatus.RETURNED: frozenset({PhoneStatus.IN_STOCK, PhoneStatus.DAMAGED}),
    PhoneStatus.DAMAGED: frozenset({PhoneStatus.SENT_FOR_REPAIR, PhoneStatus.CANCELLED}),
    PhoneStatus.SENT_FOR_REPAIR: frozenset({PhoneStatus.REPAIRED, PhoneStatus.DAMAGED}),
    PhoneStatus.REPAIRED: frozenset({PhoneStatus.IN_STOCK, PhoneStatus.DAMAGED}),
    PhoneStatus.LOST: frozenset({PhoneStatus.IN_STOCK, PhoneStatus.CANCELLED}),
    PhoneStatus.CANCELLED: frozenset(),
}


class InventoryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_by_imei(self, imei: str) -> PhoneInventory:
        normalized = validate_imei(imei)
        phone = self._session.execute(
            select(PhoneInventory)
            .where(or_(PhoneInventory.imei_1 == normalized, PhoneInventory.imei_2 == normalized))
            .with_for_update(of=PhoneInventory)
        ).scalar_one_or_none()
        if phone is None:
            raise NotFoundError("No phone was found with that IMEI.")
        return phone

    def record_purchase(
        self,
        phone: PhoneInventory,
        *,
        reference_id: uuid.UUID,
        actor_id: uuid.UUID,
        notes: str | None = None,
    ) -> InventoryTransaction:
        if phone.status is not PhoneStatus.IN_STOCK:
            raise ConflictError("A newly purchased phone must enter inventory as IN_STOCK.")
        entry = InventoryTransaction(
            phone_id=phone.id,
            transaction_type=InventoryTransactionType.PURCHASE,
            quantity=1,
            reference_id=reference_id,
            reference_type="Purchase",
            previous_status=None,
            new_status=PhoneStatus.IN_STOCK,
            performed_by=actor_id,
            created_at=datetime.now(UTC),
            notes=notes,
        )
        self._session.add(entry)
        return entry

    def transition(
        self,
        phone: PhoneInventory,
        new_status: PhoneStatus,
        *,
        transaction_type: InventoryTransactionType,
        reference_id: uuid.UUID,
        reference_type: str,
        actor_id: uuid.UUID,
        notes: str | None = None,
    ) -> InventoryTransaction:
        previous_status = phone.status
        if new_status not in ALLOWED_TRANSITIONS[previous_status]:
            raise ConflictError(
                "Inventory status cannot change from "
                f"{previous_status.value} to {new_status.value}."
            )
        if transaction_type is InventoryTransactionType.SALE:
            quantity = -1
        elif (
            transaction_type is InventoryTransactionType.RETURN
            and previous_status is PhoneStatus.SOLD
        ):
            quantity = 1
        else:
            quantity = 0
        phone.status = new_status
        entry = InventoryTransaction(
            phone_id=phone.id,
            transaction_type=transaction_type,
            quantity=quantity,
            reference_id=reference_id,
            reference_type=reference_type,
            previous_status=previous_status,
            new_status=new_status,
            performed_by=actor_id,
            created_at=datetime.now(UTC),
            notes=notes,
        )
        self._session.add(entry)
        # Keep compound transitions in deterministic ledger order. A flush remains
        # part of the caller-owned transaction and does not commit partial work.
        self._session.flush()
        return entry


class InventoryAdministrationService:
    """Explicit, audited manual operations available only to inventory roles."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._inventory = InventoryService(session)
        self._audit = AuditService(session)

    def adjust_status(
        self,
        imei: str,
        new_status: PhoneStatus,
        *,
        actor: AuthenticatedUser,
        reason: str,
    ) -> PhoneInventory:
        require_permission(actor.role, Permission.MANAGE_INVENTORY)
        if not reason.strip():
            raise ValidationError("A reason is required for an inventory adjustment.")
        phone = self._inventory.lock_by_imei(imei)
        previous = phone.status
        reference_id = uuid.uuid4()
        self._inventory.transition(
            phone,
            new_status,
            transaction_type=InventoryTransactionType.ADJUSTMENT,
            reference_id=reference_id,
            reference_type="InventoryAdjustment",
            actor_id=actor.id,
            notes=reason.strip(),
        )
        self._audit.record(
            actor_id=actor.id,
            action="INVENTORY_ADJUSTED",
            entity_type="PhoneInventory",
            entity_id=phone.id,
            old_value={"status": previous.value},
            new_value={"status": new_status.value, "reason": reason.strip()},
        )
        return phone

    def change_selling_price(
        self,
        imei: str,
        selling_price: Decimal | str | int,
        *,
        actor: AuthenticatedUser,
        reason: str,
    ) -> PhoneInventory:
        require_permission(actor.role, Permission.MANAGE_INVENTORY)
        if not reason.strip():
            raise ValidationError("A reason is required for a price change.")
        phone = self._inventory.lock_by_imei(imei)
        old_price = phone.selling_price
        phone.selling_price = nonnegative_money(selling_price, field="Selling price")
        self._audit.record(
            actor_id=actor.id,
            action="PRICE_CHANGED",
            entity_type="PhoneInventory",
            entity_id=phone.id,
            old_value={"selling_price": str(old_price)},
            new_value={"selling_price": str(phone.selling_price), "reason": reason.strip()},
        )
        return phone
