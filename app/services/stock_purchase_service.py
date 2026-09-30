"""Atomic pesticide stock intake by product batch and expiry date."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import InventoryTransactionType, PaymentDirection, PurchaseStatus
from app.models.inventory import StockBatch, StockMovement
from app.models.payment import Payment
from app.models.product import Product
from app.models.purchase import Purchase, PurchaseItem
from app.models.supplier import Supplier
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, require_permission
from app.services.audit_service import AuditService
from app.services.document_service import DocumentNumberService
from app.services.dto import CreateStockPurchaseCommand
from app.services.money_service import document_totals, payment_status
from app.services.practice_guard import require_unmixed_purchase
from app.utils.exceptions import ConflictError, NotFoundError, ValidationError
from app.utils.validators import nonnegative_money


class StockPurchaseService:
    """Create a purchase and its available stock in one transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._audit = AuditService(session)
        self._numbers = DocumentNumberService(session)

    def create(self, command: CreateStockPurchaseCommand, actor: AuthenticatedUser) -> Purchase:
        require_permission(actor.role, Permission.RECORD_PURCHASE)
        if not command.batches:
            raise ValidationError("A purchase must contain at least one stock batch.")
        supplier = self._session.execute(
            select(Supplier).where(Supplier.id == command.supplier_id).with_for_update(of=Supplier)
        ).scalar_one_or_none()
        if supplier is None or not supplier.is_active:
            raise NotFoundError("The selected active supplier was not found.")

        keys: list[tuple[object, str]] = []
        for item in command.batches:
            batch_number = item.batch_number.strip().upper()
            if not batch_number:
                raise ValidationError("Batch number is required.")
            if item.quantity <= 0:
                raise ValidationError("Batch quantity must be greater than zero.")
            if item.cartons < 0 or item.packs_per_carton < 0:
                raise ValidationError("Cartons and packs cannot be negative.")
            if (
                item.manufacture_date
                and item.expiry_date
                and item.expiry_date < item.manufacture_date
            ):
                raise ValidationError("Expiry date cannot be before manufacture date.")
            keys.append((item.product_id, batch_number))
        duplicate = next((key for key, count in Counter(keys).items() if count > 1), None)
        if duplicate:
            raise ConflictError(f"Batch {duplicate[1]} was added more than once.")

        product_ids = {item.product_id for item in command.batches}
        active_products = set(
            self._session.scalars(
                select(Product.id).where(Product.id.in_(product_ids), Product.is_active.is_(True))
            ).all()
        )
        if active_products != product_ids:
            raise NotFoundError("One or more selected products are missing or inactive.")
        require_unmixed_purchase(self._session, product_ids, supplier_is_test=supplier.is_test)
        key_set = set(keys)
        existing_batches = {
            (batch.product_id, batch.batch_number): batch
            for batch in self._session.scalars(
                select(StockBatch)
                .where(
                    StockBatch.product_id.in_(product_ids),
                    StockBatch.batch_number.in_({batch_number for _, batch_number in keys}),
                )
                .with_for_update(of=StockBatch)
            )
            if (batch.product_id, batch.batch_number) in key_set
        }

        prices = [
            nonnegative_money(item.purchase_price, field="Purchase price")
            for item in command.batches
        ]
        selling_prices = [
            nonnegative_money(item.selling_price, field="Selling price") for item in command.batches
        ]
        subtotal = sum(
            (price * item.quantity for item, price in zip(command.batches, prices, strict=True)),
            Decimal("0.00"),
        )
        subtotal, discount, tax, total = document_totals(subtotal, command.discount, command.tax)
        payment_amounts = [
            nonnegative_money(payment.amount, field="Payment") for payment in command.payments
        ]
        paid = sum(payment_amounts, Decimal("0.00"))
        status = payment_status(total, paid)
        purchase_date = command.purchase_date or datetime.now(UTC)
        purchase = Purchase(
            purchase_number=self._numbers.next_number("purchase", "PUR", purchase_date),
            supplier_id=supplier.id,
            purchase_date=purchase_date,
            subtotal=subtotal,
            discount=discount,
            tax=tax,
            total=total,
            paid_amount=paid,
            remaining_amount=total - paid,
            payment_status=status,
            status=PurchaseStatus.COMPLETED,
            created_by=actor.id,
            notes=command.notes,
        )
        self._session.add(purchase)
        self._session.flush()

        for item, purchase_price, selling_price in zip(
            command.batches, prices, selling_prices, strict=True
        ):
            purchase_item = PurchaseItem(
                purchase_id=purchase.id,
                product_id=item.product_id,
                quantity=item.quantity,
                purchase_price=purchase_price,
                line_total=purchase_price * item.quantity,
            )
            self._session.add(purchase_item)
            self._session.flush()
            batch_number = item.batch_number.strip().upper()
            existing = existing_batches.get((item.product_id, batch_number))
            if existing is not None:
                # Restock: the batch already exists, so its record stays intact and
                # only its available quantity grows, with the ledger carrying the
                # rest of this purchase's history for it.
                existing.quantity_received += item.quantity
                existing.quantity_available += item.quantity
                batch = existing
            else:
                batch = StockBatch(
                    product_id=item.product_id,
                    supplier_id=supplier.id,
                    purchase_id=purchase.id,
                    purchase_item_id=purchase_item.id,
                    batch_number=batch_number,
                    manufacture_date=item.manufacture_date,
                    expiry_date=item.expiry_date,
                    quantity_received=item.quantity,
                    quantity_available=item.quantity,
                    cartons=item.cartons,
                    packs_per_carton=item.packs_per_carton,
                    purchase_price=purchase_price,
                    selling_price=selling_price,
                    location=item.location.strip() if item.location else None,
                    notes=item.notes.strip() if item.notes else None,
                    is_active=True,
                )
                self._session.add(batch)
            self._session.flush()
            self._session.add(
                StockMovement(
                    batch_id=batch.id,
                    transaction_type=InventoryTransactionType.PURCHASE,
                    quantity_change=item.quantity,
                    balance_after=batch.quantity_available,
                    reference_id=purchase.id,
                    reference_type="Purchase",
                    performed_by=actor.id,
                    created_at=datetime.now(UTC),
                    notes=purchase.purchase_number,
                )
            )

        for payment_input, amount in zip(command.payments, payment_amounts, strict=True):
            if amount:
                self._session.add(
                    Payment(
                        purchase_id=purchase.id,
                        method=payment_input.method,
                        direction=PaymentDirection.OUTGOING,
                        amount=amount,
                        reference=payment_input.reference,
                        received_by=actor.id,
                    )
                )
        supplier.balance += purchase.remaining_amount
        self._audit.record(
            actor_id=actor.id,
            action="PESTICIDE_PURCHASE_CREATED",
            entity_type="Purchase",
            entity_id=purchase.id,
            new_value={
                "purchase_number": purchase.purchase_number,
                "total": str(total),
                "batch_count": len(command.batches),
                "quantity": sum(item.quantity for item in command.batches),
            },
        )
        self._session.flush()
        return purchase

    def cancel(self, purchase_id: uuid.UUID, actor: AuthenticatedUser) -> Purchase:
        """Cancel an untouched, unpaid purchase and reverse its available stock."""

        require_permission(actor.role, Permission.RECORD_PURCHASE)
        purchase = self._session.execute(
            select(Purchase).where(Purchase.id == purchase_id).with_for_update(of=Purchase)
        ).scalar_one_or_none()
        if purchase is None:
            raise NotFoundError("Purchase was not found.")
        if purchase.status is not PurchaseStatus.COMPLETED:
            raise ConflictError("Only a completed purchase can be cancelled.")
        if purchase.paid_amount > 0:
            raise ConflictError(
                "This purchase has supplier payments. Record a correcting transaction instead."
            )
        movements = list(
            self._session.scalars(
                select(StockMovement).where(
                    StockMovement.reference_type == "Purchase",
                    StockMovement.reference_id == purchase.id,
                )
            )
        )
        batch_ids = {movement.batch_id for movement in movements}
        batches = {
            batch.id: batch
            for batch in self._session.scalars(
                select(StockBatch)
                .where(StockBatch.id.in_(batch_ids))
                .order_by(StockBatch.id)
                .with_for_update(of=StockBatch)
            )
        }
        if not movements or set(batches) != batch_ids:
            raise ConflictError(
                "Stock from this purchase has already changed. Use a stock correction instead."
            )
        for movement in movements:
            batch = batches[movement.batch_id]
            if batch.purchase_id == purchase.id:
                # This purchase created the batch outright; nothing may have moved since.
                if batch.quantity_available != batch.quantity_received:
                    raise ConflictError(
                        "Stock from this purchase has already changed. "
                        "Use a stock correction instead."
                    )
            elif batch.quantity_available < movement.quantity_change:
                # This purchase only restocked an existing batch; some of what it added
                # has already been sold or adjusted away, so undoing it would go negative.
                raise ConflictError(
                    "Stock from this purchase has already changed. Use a stock correction instead."
                )
        supplier = self._session.execute(
            select(Supplier).where(Supplier.id == purchase.supplier_id).with_for_update(of=Supplier)
        ).scalar_one()
        for movement in movements:
            batch = batches[movement.batch_id]
            if batch.purchase_id == purchase.id:
                quantity = batch.quantity_available
                batch.quantity_available = 0
                batch.is_active = False
                balance_after = 0
            else:
                quantity = movement.quantity_change
                batch.quantity_received -= quantity
                batch.quantity_available -= quantity
                balance_after = batch.quantity_available
            self._session.add(
                StockMovement(
                    batch_id=batch.id,
                    transaction_type=InventoryTransactionType.ADJUSTMENT,
                    quantity_change=-quantity,
                    balance_after=balance_after,
                    reference_id=purchase.id,
                    reference_type="Purchase Cancellation",
                    performed_by=actor.id,
                    created_at=datetime.now(UTC),
                    notes=f"Cancelled {purchase.purchase_number}",
                )
            )
        supplier.balance -= purchase.remaining_amount
        purchase.status = PurchaseStatus.CANCELLED
        self._audit.record(
            actor_id=actor.id,
            action="PURCHASE_CANCELLED",
            entity_type="Purchase",
            entity_id=purchase.id,
            old_value={"status": PurchaseStatus.COMPLETED.value},
            new_value={"status": PurchaseStatus.CANCELLED.value},
        )
        self._session.flush()
        return purchase
