"""Damage reporting and repair lifecycle workflow."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.damage import DamageRecord
from app.models.enums import (
    DamageStatus,
    InventoryTransactionType,
    PhoneStatus,
)
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, require_permission
from app.services.audit_service import AuditService
from app.services.document_service import DocumentNumberService
from app.services.dto import CreateDamageCommand
from app.services.inventory_service import InventoryService
from app.services.notification_service import NotificationService, QueuedMessage
from app.utils.exceptions import ConflictError, NotFoundError, ValidationError
from app.utils.validators import nonnegative_money


class DamageService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._inventory = InventoryService(session)
        self._audit = AuditService(session)
        self._numbers = DocumentNumberService(session)
        self._notifications = NotificationService(session)

    def create(
        self,
        command: CreateDamageCommand,
        actor: AuthenticatedUser,
        *,
        owner_email: str | None = None,
    ) -> DamageRecord:
        require_permission(actor.role, Permission.RECORD_DAMAGE)
        if not command.description.strip():
            raise ValidationError("Damage description is required.")
        phone = self._inventory.lock_by_imei(command.imei)
        if phone.status not in {PhoneStatus.IN_STOCK, PhoneStatus.RETURNED, PhoneStatus.REPAIRED}:
            raise ConflictError(f"A phone in {phone.status.value} status cannot be marked damaged.")
        damage_date = command.damage_date or datetime.now(UTC)
        damage = DamageRecord(
            damage_number=self._numbers.next_number("damage", "DMG", damage_date),
            phone_id=phone.id,
            imei=phone.imei_1,
            damage_type=command.damage_type,
            description=command.description.strip(),
            estimated_loss=nonnegative_money(command.estimated_loss, field="Estimated loss"),
            repair_cost=nonnegative_money(command.repair_cost, field="Repair cost"),
            reported_by=actor.id,
            damage_date=damage_date,
            status=DamageStatus.REPORTED,
            notes=command.notes,
        )
        self._session.add(damage)
        self._session.flush()
        self._inventory.transition(
            phone,
            PhoneStatus.DAMAGED,
            transaction_type=InventoryTransactionType.DAMAGE,
            reference_id=damage.id,
            reference_type="DamageRecord",
            actor_id=actor.id,
            notes=damage.damage_number,
        )
        self._audit.record(
            actor_id=actor.id,
            action="DAMAGE_CREATED",
            entity_type="DamageRecord",
            entity_id=damage.id,
            new_value={
                "damage_number": damage.damage_number,
                "imei": damage.imei,
                "type": damage.damage_type.value,
                "estimated_loss": str(damage.estimated_loss),
            },
        )
        if owner_email:
            self._notifications.queue(
                message=QueuedMessage(
                    recipient=owner_email,
                    subject=f"Damage Reported - {damage.damage_number}",
                    body_text=(
                        f"IMEI: {damage.imei}\nProduct: {phone.product.display_name}\n"
                        f"Type: {damage.damage_type.value}\nDescription: {damage.description}\n"
                        f"Estimated loss: {damage.estimated_loss}\nReported by: {actor.full_name}\n"
                    ),
                ),
                template="damage_owner",
                entity_type="DamageRecord",
                entity_id=damage.id,
            )
        self._session.flush()
        return damage

    def send_for_repair(self, damage_id: uuid.UUID, actor: AuthenticatedUser) -> DamageRecord:
        require_permission(actor.role, Permission.RECORD_DAMAGE)
        damage = self._session.execute(
            select(DamageRecord)
            .where(DamageRecord.id == damage_id)
            .with_for_update(of=DamageRecord)
        ).scalar_one_or_none()
        if damage is None:
            raise NotFoundError("Damage record was not found.")
        if damage.status is not DamageStatus.REPORTED:
            raise ConflictError("Only a reported damage can be sent for repair.")
        phone = self._inventory.lock_by_imei(damage.imei)
        self._inventory.transition(
            phone,
            PhoneStatus.SENT_FOR_REPAIR,
            transaction_type=InventoryTransactionType.REPAIR,
            reference_id=damage.id,
            reference_type="DamageRecord",
            actor_id=actor.id,
        )
        damage.status = DamageStatus.UNDER_REPAIR
        return damage

    def complete_repair(
        self,
        damage_id: uuid.UUID,
        actor: AuthenticatedUser,
        *,
        repair_cost: Decimal | str | int,
        resolution: str,
        return_to_stock: bool = True,
    ) -> DamageRecord:
        require_permission(actor.role, Permission.RECORD_DAMAGE)
        damage = self._session.execute(
            select(DamageRecord)
            .where(DamageRecord.id == damage_id)
            .with_for_update(of=DamageRecord)
        ).scalar_one_or_none()
        if damage is None:
            raise NotFoundError("Damage record was not found.")
        if damage.status is not DamageStatus.UNDER_REPAIR:
            raise ConflictError("Only a phone under repair can complete repair.")
        if not resolution.strip():
            raise ValidationError("A repair resolution is required.")
        phone = self._inventory.lock_by_imei(damage.imei)
        self._inventory.transition(
            phone,
            PhoneStatus.REPAIRED,
            transaction_type=InventoryTransactionType.REPAIR,
            reference_id=damage.id,
            reference_type="DamageRecord",
            actor_id=actor.id,
        )
        if return_to_stock:
            self._inventory.transition(
                phone,
                PhoneStatus.IN_STOCK,
                transaction_type=InventoryTransactionType.REPAIR,
                reference_id=damage.id,
                reference_type="DamageRecord",
                actor_id=actor.id,
            )
        damage.repair_cost = nonnegative_money(repair_cost, field="Repair cost")
        damage.resolution = resolution.strip()
        damage.status = DamageStatus.REPAIRED
        self._audit.record(
            actor_id=actor.id,
            action="DAMAGE_REPAIRED",
            entity_type="DamageRecord",
            entity_id=damage.id,
            new_value={"repair_cost": str(damage.repair_cost), "resolution": damage.resolution},
        )
        return damage
