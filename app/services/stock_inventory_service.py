"""Audited manual corrections for quantity-based stock batches."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import InventoryTransactionType
from app.models.inventory import StockBatch, StockMovement
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, require_permission
from app.services.audit_service import AuditService
from app.utils.exceptions import ConflictError, NotFoundError, ValidationError


class StockInventoryService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._audit = AuditService(session)

    def adjust(
        self, batch_id: uuid.UUID, *, quantity: int, reason: str, actor: AuthenticatedUser
    ) -> StockBatch:
        require_permission(actor.role, Permission.MANAGE_INVENTORY)
        if not reason.strip():
            raise ValidationError("A reason is required for every stock adjustment.")
        batch = self._session.execute(
            select(StockBatch).where(StockBatch.id == batch_id).with_for_update(of=StockBatch)
        ).scalar_one_or_none()
        if batch is None:
            raise NotFoundError("Stock batch was not found.")
        if quantity < 0 or quantity > batch.quantity_received:
            raise ValidationError(
                f"Available quantity must be between 0 and {batch.quantity_received}."
            )
        change = quantity - batch.quantity_available
        if change == 0:
            raise ConflictError("The new quantity is the same as the current quantity.")
        old = batch.quantity_available
        batch.quantity_available = quantity
        self._session.add(
            StockMovement(
                batch_id=batch.id,
                transaction_type=InventoryTransactionType.ADJUSTMENT,
                quantity_change=change,
                balance_after=quantity,
                reference_id=batch.id,
                reference_type="StockBatch",
                performed_by=actor.id,
                created_at=datetime.now(UTC),
                notes=reason.strip(),
            )
        )
        self._audit.record(
            actor_id=actor.id,
            action="STOCK_ADJUSTED",
            entity_type="StockBatch",
            entity_id=batch.id,
            old_value={"quantity_available": old},
            new_value={"quantity_available": quantity, "reason": reason.strip()},
        )
        return batch
