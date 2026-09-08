"""Audited manual corrections for quantity-based stock batches."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import InventoryTransactionType
from app.models.inventory import StockBatch, StockMovement
from app.models.sale import SaleItem
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

    def update_batch(
        self,
        batch_id: uuid.UUID,
        *,
        actor: AuthenticatedUser,
        batch_number: str,
        manufacture_date: date | None,
        expiry_date: date | None,
        cartons: int,
        packs_per_carton: int,
        purchase_price: Decimal,
        selling_price: Decimal,
        location: str | None = None,
        notes: str | None = None,
    ) -> StockBatch:
        """Correct a batch's identifying details, dates, packing, and prices.

        Quantities are deliberately excluded: they move only through ``adjust`` so the
        movement ledger stays the single record of every stock change.
        """

        require_permission(actor.role, Permission.MANAGE_INVENTORY)
        batch = self._session.execute(
            select(StockBatch).where(StockBatch.id == batch_id).with_for_update(of=StockBatch)
        ).scalar_one_or_none()
        if batch is None:
            raise NotFoundError("Stock batch was not found.")
        cleaned_number = batch_number.strip().upper()
        if not cleaned_number:
            raise ValidationError("Batch number is required.")
        if cartons < 0 or packs_per_carton < 0:
            raise ValidationError("Cartons and packs per carton cannot be negative.")
        if purchase_price < 0 or selling_price < 0:
            raise ValidationError("Prices cannot be negative.")
        if manufacture_date and expiry_date and expiry_date < manufacture_date:
            raise ValidationError("Expiry date cannot be before the manufacture date.")
        old = {
            "batch_number": batch.batch_number,
            "expiry_date": batch.expiry_date.isoformat() if batch.expiry_date else None,
            "purchase_price": str(batch.purchase_price),
            "selling_price": str(batch.selling_price),
        }
        batch.batch_number = cleaned_number
        batch.manufacture_date = manufacture_date
        batch.expiry_date = expiry_date
        batch.cartons = cartons
        batch.packs_per_carton = packs_per_carton
        batch.purchase_price = purchase_price
        batch.selling_price = selling_price
        batch.location = location.strip() if location and location.strip() else None
        batch.notes = notes.strip() if notes and notes.strip() else None
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "Another batch of this product already uses that batch number."
            ) from exc
        self._audit.record(
            actor_id=actor.id,
            action="STOCK_BATCH_UPDATED",
            entity_type="StockBatch",
            entity_id=batch.id,
            old_value=old,
            new_value={
                "batch_number": batch.batch_number,
                "expiry_date": batch.expiry_date.isoformat() if batch.expiry_date else None,
                "purchase_price": str(batch.purchase_price),
                "selling_price": str(batch.selling_price),
            },
        )
        return batch

    def delete_batch(
        self, batch_id: uuid.UUID, *, actor: AuthenticatedUser, reason: str
    ) -> StockBatch:
        """Write the batch's remaining stock off and retire it.

        Batch rows are never erased: invoices reference them and the movement ledger is
        append-only in the database itself. Removing a batch therefore reverses its
        remaining quantity through an audited adjustment and deactivates the batch, so
        it disappears from sales and stock figures while history stays intact.
        """

        require_permission(actor.role, Permission.MANAGE_INVENTORY)
        if not reason.strip():
            raise ValidationError("A reason is required to remove a stock batch.")
        batch = self._session.execute(
            select(StockBatch).where(StockBatch.id == batch_id).with_for_update(of=StockBatch)
        ).scalar_one_or_none()
        if batch is None:
            raise NotFoundError("Stock batch was not found.")
        if not batch.is_active and batch.quantity_available == 0:
            raise ConflictError("This batch has already been removed.")
        remaining = batch.quantity_available
        if remaining:
            batch.quantity_available = 0
            self._session.add(
                StockMovement(
                    batch_id=batch.id,
                    transaction_type=InventoryTransactionType.ADJUSTMENT,
                    quantity_change=-remaining,
                    balance_after=0,
                    reference_id=batch.id,
                    reference_type="Batch Removal",
                    performed_by=actor.id,
                    created_at=datetime.now(UTC),
                    notes=reason.strip(),
                )
            )
        batch.is_active = False
        self._audit.record(
            actor_id=actor.id,
            action="STOCK_BATCH_REMOVED",
            entity_type="StockBatch",
            entity_id=batch.id,
            old_value={"quantity_available": remaining, "is_active": True},
            new_value={"quantity_available": 0, "is_active": False, "reason": reason.strip()},
        )
        return batch

    def batch_is_sold(self, batch_id: uuid.UUID) -> bool:
        """Report whether any invoice line already draws on this batch."""

        return (
            self._session.scalar(
                select(SaleItem.id).where(SaleItem.stock_batch_id == batch_id).limit(1)
            )
            is not None
        )
