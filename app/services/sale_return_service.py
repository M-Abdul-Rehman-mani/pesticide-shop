"""Goods returned against a completed sale."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.dealer import Dealer
from app.models.enums import InventoryTransactionType, PaymentDirection, PaymentMethod, SaleStatus
from app.models.inventory import StockBatch, StockMovement
from app.models.payment import Payment
from app.models.sale import Sale, SaleItem
from app.models.sale_return import SaleReturn, SaleReturnItem
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, require_permission
from app.services.audit_service import AuditService
from app.services.document_service import DocumentNumberService
from app.services.money_service import payment_status
from app.utils.exceptions import ConflictError, NotFoundError, ValidationError

ZERO = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class ReturnLineInput:
    """One invoice line coming back, in whole units."""

    sale_item_id: uuid.UUID
    quantity: int
    #: Damaged or expired goods are recorded but not put back on the shelf.
    restock: bool = True


@dataclass(frozen=True, slots=True)
class ReturnableLine:
    """An invoice line with the quantity still available to return."""

    sale_item_id: uuid.UUID
    product: str
    batch_number: str
    sold: int
    already_returned: int
    unit_price: Decimal

    @property
    def returnable(self) -> int:
        return self.sold - self.already_returned


class SaleReturnService:
    """Take goods back onto the shelf and credit the invoice."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._audit = AuditService(session)
        self._numbers = DocumentNumberService(session)

    def returnable_lines(self, sale_id: uuid.UUID) -> list[ReturnableLine]:
        """Report what can still be returned from an invoice."""

        sale = self._session.get(Sale, sale_id)
        if sale is None:
            raise NotFoundError("Sale was not found.")
        returned: dict[uuid.UUID, int] = {
            item_id: int(quantity or 0)
            for item_id, quantity in self._session.execute(
                select(SaleReturnItem.sale_item_id, func.sum(SaleReturnItem.quantity))
                .join(SaleReturn, SaleReturn.id == SaleReturnItem.sale_return_id)
                .where(SaleReturn.sale_id == sale_id)
                .group_by(SaleReturnItem.sale_item_id)
            ).all()
        }
        return [
            ReturnableLine(
                sale_item_id=item.id,
                product=item.product.display_name,
                batch_number=item.batch_number,
                sold=item.quantity,
                already_returned=returned.get(item.id, 0),
                unit_price=item.unit_price,
            )
            for item in sale.items
        ]

    def record(
        self,
        sale_id: uuid.UUID,
        lines: tuple[ReturnLineInput, ...],
        actor: AuthenticatedUser,
        *,
        reason: str,
        refund_method: PaymentMethod = PaymentMethod.CASH,
        return_prefix: str = "RET",
    ) -> SaleReturn:
        """Record a full or partial return against a completed sale.

        The invoice is left exactly as it was issued; the return is a separate
        credit document. Restocked goods go back to the batch they came from, and
        the credit either reduces what is still owed or is refunded in cash.
        """

        # Taking goods back hands money or credit out again, so it carries the same
        # authority as voiding a sale rather than the authority to make one.
        require_permission(actor.role, Permission.RECORD_RETURN)
        if not reason.strip():
            raise ValidationError("A reason is required to record a return.")
        if not lines:
            raise ValidationError("Select at least one line to return.")
        sale = self._session.execute(
            select(Sale).where(Sale.id == sale_id).with_for_update(of=Sale)
        ).scalar_one_or_none()
        if sale is None:
            raise NotFoundError("Sale was not found.")
        if sale.status is not SaleStatus.COMPLETED:
            raise ConflictError("Only a completed sale can accept a return.")

        available = {line.sale_item_id: line for line in self.returnable_lines(sale_id)}
        if len({line.sale_item_id for line in lines}) != len(lines):
            raise ValidationError("List each invoice line only once.")
        for line in lines:
            entry = available.get(line.sale_item_id)
            if entry is None:
                raise NotFoundError("A line being returned is not on this invoice.")
            if line.quantity <= 0:
                raise ValidationError("A returned quantity must be greater than zero.")
            if line.quantity > entry.returnable:
                raise ValidationError(
                    f"Only {entry.returnable} of {entry.product} can still be returned."
                )

        returned_at = datetime.now(UTC)
        document = SaleReturn(
            return_number=self._numbers.next_number("sale_return", return_prefix, returned_at),
            sale_id=sale.id,
            returned_at=returned_at,
            total=ZERO,
            refunded=False,
            reason=reason.strip(),
            created_by=actor.id,
        )
        self._session.add(document)
        self._session.flush()

        items = {
            item.id: item
            for item in self._session.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id))
        }
        batch_ids = [items[line.sale_item_id].stock_batch_id for line in lines if line.restock]
        batches = {
            batch.id: batch
            for batch in self._session.scalars(
                select(StockBatch)
                .where(StockBatch.id.in_(batch_ids))
                .order_by(StockBatch.id)
                .with_for_update(of=StockBatch)
            )
        }
        credit = ZERO
        for line in lines:
            item = items[line.sale_item_id]
            line_total = item.unit_price * line.quantity
            credit += line_total
            self._session.add(
                SaleReturnItem(
                    sale_return_id=document.id,
                    sale_item_id=item.id,
                    stock_batch_id=item.stock_batch_id,
                    product_id=item.product_id,
                    quantity=line.quantity,
                    unit_price=item.unit_price,
                    total=line_total,
                    restocked=line.restock,
                )
            )
            if not line.restock:
                continue
            batch = batches.get(item.stock_batch_id)
            if batch is None:
                raise NotFoundError("The stock batch for a returned line no longer exists.")
            if batch.quantity_available + line.quantity > batch.quantity_received:
                raise ConflictError(
                    f"Returning batch {batch.batch_number} would exceed the quantity received."
                )
            batch.quantity_available += line.quantity
            self._session.add(
                StockMovement(
                    batch_id=batch.id,
                    transaction_type=InventoryTransactionType.ADJUSTMENT,
                    quantity_change=line.quantity,
                    balance_after=batch.quantity_available,
                    reference_id=document.id,
                    reference_type="Sale Return",
                    performed_by=actor.id,
                    created_at=returned_at,
                    notes=f"{document.return_number} against {sale.invoice_number}",
                )
            )
        document.total = credit
        document.refunded = self._settle(sale, document, credit, actor, method=refund_method)
        self._audit.record(
            actor_id=actor.id,
            action="SALE_RETURN_RECORDED",
            entity_type="SaleReturn",
            entity_id=document.id,
            new_value={
                "return_number": document.return_number,
                "invoice": sale.invoice_number,
                "total": str(credit),
                "refunded": document.refunded,
                "reason": document.reason,
            },
        )
        self._session.flush()
        return document

    def _settle(
        self,
        sale: Sale,
        document: SaleReturn,
        credit: Decimal,
        actor: AuthenticatedUser,
        *,
        method: PaymentMethod,
    ) -> bool:
        """Apply the credit to what is still owed, then refund any excess.

        The invoice's own totals are never rewritten -- it stands as issued, and the
        return is a separate credit document. The credit therefore settles the
        invoice the same way a payment would, and anything beyond the outstanding
        balance is money already collected, so it goes back as cash.
        """

        dealer = (
            self._session.execute(
                select(Dealer).where(Dealer.id == sale.dealer_id).with_for_update(of=Dealer)
            ).scalar_one()
            if sale.dealer_id
            else None
        )
        against_balance = min(credit, sale.remaining_amount)
        if against_balance:
            self._session.add(
                Payment(
                    sale_id=sale.id,
                    dealer_id=sale.dealer_id,
                    sale_return_id=document.id,
                    method=method,
                    direction=PaymentDirection.INCOMING,
                    amount=against_balance,
                    reference=document.return_number,
                    notes=f"Goods returned: {document.reason}",
                    received_by=actor.id,
                )
            )
            sale.paid_amount += against_balance
            sale.remaining_amount -= against_balance
            sale.payment_status = payment_status(sale.total, sale.paid_amount)
            if dealer is not None:
                dealer.balance -= against_balance
        refundable = credit - against_balance
        if refundable:
            self._session.add(
                Payment(
                    sale_id=sale.id,
                    dealer_id=sale.dealer_id,
                    sale_return_id=document.id,
                    method=method,
                    direction=PaymentDirection.OUTGOING,
                    amount=refundable,
                    reference=document.return_number,
                    notes=f"Refund for returned goods: {document.reason}",
                    received_by=actor.id,
                )
            )
        return bool(refundable)
