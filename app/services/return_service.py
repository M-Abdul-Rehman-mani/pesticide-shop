"""Validated, atomic sale return and refund workflow."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.enums import (
    InventoryTransactionType,
    PaymentDirection,
    PaymentStatus,
    PhoneCondition,
    PhoneStatus,
    ReturnCondition,
    ReturnStatus,
    SaleStatus,
)
from app.models.payment import Payment
from app.models.return_record import ReturnItem, SaleReturn
from app.models.sale import Sale, SaleItem
from app.models.user import User
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, require_permission
from app.services.audit_service import AuditService
from app.services.document_service import DocumentNumberService
from app.services.dto import CreateReturnCommand
from app.services.inventory_service import InventoryService
from app.services.notification_service import NotificationService, QueuedMessage
from app.utils.exceptions import ConflictError, NotFoundError, ValidationError
from app.utils.validators import nonnegative_money, validate_imei


class ReturnService:
    """Create a return inside the caller-owned database transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._inventory = InventoryService(session)
        self._audit = AuditService(session)
        self._numbers = DocumentNumberService(session)
        self._notifications = NotificationService(session)

    def create(
        self,
        command: CreateReturnCommand,
        actor: AuthenticatedUser,
        *,
        return_prefix: str = "RET",
        shop_name: str = "Mobile Shop",
        owner_email: str | None = None,
    ) -> SaleReturn:
        require_permission(actor.role, Permission.CREATE_RETURN)
        approver = self._session.get(User, command.approved_by)
        if approver is None or not approver.is_active:
            raise NotFoundError("The selected approver was not found or is inactive.")
        require_permission(approver.role, Permission.APPROVE_RETURN)
        imei = validate_imei(command.imei)
        sale = self._session.execute(
            select(Sale)
            .where(Sale.invoice_number == command.invoice_number.strip())
            .with_for_update(of=Sale)
        ).scalar_one_or_none()
        if sale is None:
            raise NotFoundError("The invoice was not found.")
        if sale.status is not SaleStatus.COMPLETED:
            raise ConflictError("Only completed sales can be returned.")
        sale_item = self._session.execute(
            select(SaleItem)
            .where(SaleItem.sale_id == sale.id, SaleItem.imei == imei)
            .with_for_update(of=SaleItem)
        ).scalar_one_or_none()
        if sale_item is None:
            raise NotFoundError("That IMEI was not sold on the selected invoice.")
        duplicate = self._session.scalar(
            select(ReturnItem.id).where(ReturnItem.sale_item_id == sale_item.id).limit(1)
        )
        if duplicate:
            raise ConflictError("This sale item has already been returned.")
        phone = self._inventory.lock_by_imei(imei)
        if phone.id != sale_item.phone_id or phone.status is not PhoneStatus.SOLD:
            raise ConflictError("The phone is not in a returnable SOLD state.")

        refund = nonnegative_money(command.refund_amount, field="Refund")
        if refund > sale_item.total:
            raise ValidationError("Refund cannot exceed the original item total.")
        previous_refunds = self._session.scalar(
            select(func.coalesce(func.sum(SaleReturn.refund_amount), Decimal("0.00"))).where(
                SaleReturn.sale_id == sale.id,
                SaleReturn.status == ReturnStatus.COMPLETED,
            )
        )
        previous_refunds = previous_refunds or Decimal("0.00")
        refundable_paid = sale.paid_amount - previous_refunds
        if refund > refundable_paid:
            raise ValidationError("Refund cannot exceed the remaining amount originally paid.")

        return_date = command.return_date or datetime.now(UTC)
        sale_return = SaleReturn(
            return_number=self._numbers.next_number("return", return_prefix, return_date),
            sale_id=sale.id,
            customer_id=sale.customer_id,
            return_date=return_date,
            reason=command.reason,
            condition=command.condition,
            refund_amount=refund,
            refund_method=command.refund_method,
            approved_by=approver.id,
            created_by=actor.id,
            status=ReturnStatus.COMPLETED,
            notes=command.notes,
        )
        self._session.add(sale_return)
        self._session.flush()
        self._session.add(
            ReturnItem(
                return_id=sale_return.id,
                sale_item_id=sale_item.id,
                refund_amount=refund,
            )
        )
        self._inventory.transition(
            phone,
            PhoneStatus.RETURNED,
            transaction_type=InventoryTransactionType.RETURN,
            reference_id=sale_return.id,
            reference_type="SaleReturn",
            actor_id=actor.id,
            notes=sale_return.return_number,
        )
        final_status = (
            PhoneStatus.DAMAGED
            if command.condition is ReturnCondition.DAMAGED
            else PhoneStatus.IN_STOCK
        )
        self._inventory.transition(
            phone,
            final_status,
            transaction_type=InventoryTransactionType.RETURN,
            reference_id=sale_return.id,
            reference_type="SaleReturn",
            actor_id=actor.id,
            notes=f"Condition: {command.condition.value}",
        )
        if command.condition is ReturnCondition.USED:
            phone.condition = PhoneCondition.USED
        elif command.condition is ReturnCondition.OPENED:
            phone.condition = PhoneCondition.OPEN_BOX
        if refund > Decimal("0.00"):
            self._session.add(
                Payment(
                    sale_return_id=sale_return.id,
                    method=command.refund_method,
                    direction=PaymentDirection.OUTGOING,
                    amount=refund,
                    received_by=actor.id,
                    notes=f"Refund for {sale.invoice_number}",
                )
            )
        if previous_refunds + refund == sale.paid_amount and sale.paid_amount > 0:
            sale.payment_status = PaymentStatus.REFUNDED
        self._audit.record(
            actor_id=actor.id,
            action="RETURN_CREATED",
            entity_type="SaleReturn",
            entity_id=sale_return.id,
            new_value={
                "return_number": sale_return.return_number,
                "invoice_number": sale.invoice_number,
                "imei": imei,
                "refund": str(refund),
                "condition": command.condition.value,
            },
        )
        if sale.customer and sale.customer.email:
            self._notifications.queue(
                message=QueuedMessage(
                    recipient=sale.customer.email,
                    subject=f"Return Confirmation - {sale_return.return_number}",
                    body_text=(
                        f"Dear {sale.customer.name},\n\n{shop_name} has completed your return.\n"
                        f"Return: {sale_return.return_number}\nInvoice: {sale.invoice_number}\n"
                        f"IMEI: {imei}\nRefund: {refund}\n"
                    ),
                ),
                template="return_customer",
                entity_type="SaleReturn",
                entity_id=sale_return.id,
            )
        if owner_email:
            self._notifications.queue(
                message=QueuedMessage(
                    recipient=owner_email,
                    subject=f"New Return - {sale_return.return_number}",
                    body_text=(
                        f"Invoice: {sale.invoice_number}\nIMEI: {imei}\n"
                        f"Reason: {command.reason.value}\nCondition: {command.condition.value}\n"
                        f"Refund: {refund}\nApproved by: {approver.full_name}\n"
                    ),
                ),
                template="return_owner",
                entity_type="SaleReturn",
                entity_id=sale_return.id,
            )
        self._session.flush()
        return sale_return
