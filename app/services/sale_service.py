"""Atomic IMEI-based sale workflow with split payments and an email outbox."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.enums import (
    InventoryTransactionType,
    PaymentDirection,
    PaymentMethod,
    PaymentStatus,
    PhoneStatus,
    SaleStatus,
)
from app.models.inventory import PhoneInventory
from app.models.payment import Payment
from app.models.return_record import SaleReturn
from app.models.sale import Sale, SaleItem
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, require_permission
from app.services.audit_service import AuditService
from app.services.document_service import DocumentNumberService
from app.services.dto import CreateSaleCommand
from app.services.inventory_service import InventoryService
from app.services.money_service import document_totals, payment_status
from app.services.notification_service import NotificationService, QueuedMessage
from app.utils.exceptions import ConflictError, NotFoundError, ValidationError
from app.utils.validators import nonnegative_money, validate_imei


class SaleService:
    """Create one completed sale inside the caller-owned database transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._inventory = InventoryService(session)
        self._audit = AuditService(session)
        self._numbers = DocumentNumberService(session)
        self._notifications = NotificationService(session)

    def create(
        self,
        command: CreateSaleCommand,
        actor: AuthenticatedUser,
        *,
        invoice_prefix: str = "INV",
        shop_name: str = "Mobile Shop",
        owner_email: str | None = None,
    ) -> Sale:
        require_permission(actor.role, Permission.CREATE_SALE)
        if not command.lines:
            raise ValidationError("A sale must contain at least one phone.")
        normalized_imeis = [validate_imei(line.imei) for line in command.lines]
        if len(set(normalized_imeis)) != len(normalized_imeis):
            raise ConflictError("The same IMEI cannot be added to a sale twice.")

        customer = None
        if command.customer_id:
            customer = self._session.get(Customer, command.customer_id)
            if customer is None:
                raise NotFoundError("The selected customer was not found.")

        locked_phones = list(
            self._session.scalars(
                select(PhoneInventory)
                .where(
                    or_(
                        PhoneInventory.imei_1.in_(normalized_imeis),
                        PhoneInventory.imei_2.in_(normalized_imeis),
                    )
                )
                .order_by(PhoneInventory.id)
                .with_for_update(of=PhoneInventory)
            ).all()
        )
        by_imei = {
            imei: phone
            for phone in locked_phones
            for imei in (phone.imei_1, phone.imei_2)
            if imei is not None
        }
        if any(imei not in by_imei for imei in normalized_imeis):
            raise NotFoundError("One or more IMEI numbers were not found.")
        phones = [by_imei[imei] for imei in normalized_imeis]
        if len({phone.id for phone in phones}) != len(phones):
            raise ConflictError("The same dual-SIM phone cannot be added twice.")
        unavailable = next(
            (phone for phone in phones if phone.status is not PhoneStatus.IN_STOCK), None
        )
        if unavailable:
            raise ConflictError(
                f"IMEI {unavailable.imei_1} is {unavailable.status.value} and cannot be sold."
            )

        prices: list[Decimal] = []
        line_discounts: list[Decimal] = []
        other_costs: list[Decimal] = []
        for line, phone in zip(command.lines, phones, strict=True):
            price = nonnegative_money(
                line.price if line.price is not None else phone.selling_price,
                field="Sale price",
            )
            discount = nonnegative_money(line.discount, field="Line discount")
            if discount > price:
                raise ValidationError("A line discount cannot exceed its sale price.")
            prices.append(price)
            line_discounts.append(discount)
            other_costs.append(nonnegative_money(line.other_cost, field="Other cost"))
        subtotal = sum(prices, Decimal("0.00"))
        total_discount = sum(line_discounts, Decimal("0.00")) + nonnegative_money(
            command.order_discount, field="Order discount"
        )
        subtotal, total_discount, tax, total = document_totals(
            subtotal, total_discount, command.tax
        )
        if total <= 0:
            raise ValidationError("A completed sale total must be greater than zero.")
        payment_amounts = [
            nonnegative_money(payment.amount, field="Payment") for payment in command.payments
        ]
        paid = sum(payment_amounts, Decimal("0.00"))
        status = payment_status(total, paid)
        sale_date = command.sale_date or datetime.now(UTC)

        sale = Sale(
            invoice_number=self._numbers.next_number("invoice", invoice_prefix, sale_date),
            customer_id=customer.id if customer else None,
            sale_date=sale_date,
            subtotal=subtotal,
            discount=total_discount,
            tax=tax,
            total=total,
            paid_amount=paid,
            remaining_amount=total - paid,
            payment_status=status,
            created_by=actor.id,
            status=SaleStatus.COMPLETED,
            notes=command.notes,
        )
        self._session.add(sale)
        self._session.flush()
        for phone, price, discount, other_cost in zip(
            phones, prices, line_discounts, other_costs, strict=True
        ):
            self._session.add(
                SaleItem(
                    sale_id=sale.id,
                    phone_id=phone.id,
                    product_id=phone.product_id,
                    imei=phone.imei_1,
                    price=price,
                    discount=discount,
                    total=price - discount,
                    purchase_cost=phone.purchase_price,
                    other_cost=other_cost,
                )
            )
            self._inventory.transition(
                phone,
                PhoneStatus.SOLD,
                transaction_type=InventoryTransactionType.SALE,
                reference_id=sale.id,
                reference_type="Sale",
                actor_id=actor.id,
                notes=sale.invoice_number,
            )
        for payment_input, amount in zip(command.payments, payment_amounts, strict=True):
            if amount == Decimal("0.00"):
                continue
            self._session.add(
                Payment(
                    sale_id=sale.id,
                    method=payment_input.method,
                    direction=PaymentDirection.INCOMING,
                    amount=amount,
                    reference=payment_input.reference,
                    received_by=actor.id,
                )
            )
        self._audit.record(
            actor_id=actor.id,
            action="SALE_CREATED",
            entity_type="Sale",
            entity_id=sale.id,
            new_value={
                "invoice_number": sale.invoice_number,
                "total": str(total),
                "paid": str(paid),
                "imeis": [phone.imei_1 for phone in phones],
            },
        )
        if customer and customer.email:
            products = ", ".join(phone.product.display_name for phone in phones)
            imeis = ", ".join(phone.imei_1 for phone in phones)
            self._notifications.queue(
                message=QueuedMessage(
                    recipient=customer.email,
                    subject=f"Purchase Receipt - {sale.invoice_number}",
                    body_text=(
                        f"Dear {customer.name},\n\n"
                        f"Thank you for your purchase from {shop_name}.\n\n"
                        f"Invoice: {sale.invoice_number}\nProduct: {products}\nIMEI: {imeis}\n"
                        f"Total: {total}\nPayment status: {status.value}\n\n"
                        "Your receipt is attached as a PDF.\n"
                    ),
                ),
                template="sale_customer",
                entity_type="Sale",
                entity_id=sale.id,
            )
        if owner_email:
            self._notifications.queue(
                message=QueuedMessage(
                    recipient=owner_email,
                    subject=f"New Sale - {sale.invoice_number}",
                    body_text=(
                        f"Customer: {customer.name if customer else 'Walk-in'}\n"
                        f"IMEI: {', '.join(phone.imei_1 for phone in phones)}\n"
                        f"Salesperson: {actor.full_name}\nTotal: {total}\nPayment: {status.value}\n"
                    ),
                ),
                template="sale_owner",
                entity_type="Sale",
                entity_id=sale.id,
            )
        self._session.flush()
        return sale

    def void(
        self,
        sale_id: uuid.UUID,
        actor: AuthenticatedUser,
        *,
        refund_method: PaymentMethod = PaymentMethod.CASH,
        reason: str,
    ) -> Sale:
        """Void a completed, unreturned sale without deleting its financial history."""

        require_permission(actor.role, Permission.VOID_SALE)
        if not reason.strip():
            raise ValidationError("A reason is required to void a sale.")
        sale = self._session.execute(
            select(Sale).where(Sale.id == sale_id).with_for_update(of=Sale)
        ).scalar_one_or_none()
        if sale is None:
            raise NotFoundError("Sale was not found.")
        if sale.status is not SaleStatus.COMPLETED:
            raise ConflictError("Only a completed sale can be voided.")
        existing_return = self._session.scalar(
            select(SaleReturn.id).where(SaleReturn.sale_id == sale.id).limit(1)
        )
        if existing_return:
            raise ConflictError("A sale with returns cannot be voided.")
        phones = list(
            self._session.scalars(
                select(PhoneInventory)
                .join(SaleItem, SaleItem.phone_id == PhoneInventory.id)
                .where(SaleItem.sale_id == sale.id)
                .order_by(PhoneInventory.id)
                .with_for_update(of=PhoneInventory)
            ).all()
        )
        if any(phone.status is not PhoneStatus.SOLD for phone in phones):
            raise ConflictError("All sale phones must still be SOLD before the sale can be voided.")
        for phone in phones:
            self._inventory.transition(
                phone,
                PhoneStatus.RETURNED,
                transaction_type=InventoryTransactionType.RETURN,
                reference_id=sale.id,
                reference_type="SaleVoid",
                actor_id=actor.id,
                notes=reason.strip(),
            )
            self._inventory.transition(
                phone,
                PhoneStatus.IN_STOCK,
                transaction_type=InventoryTransactionType.ADJUSTMENT,
                reference_id=sale.id,
                reference_type="SaleVoid",
                actor_id=actor.id,
                notes=reason.strip(),
            )
        if sale.paid_amount > 0:
            self._session.add(
                Payment(
                    sale_id=sale.id,
                    method=refund_method,
                    direction=PaymentDirection.OUTGOING,
                    amount=sale.paid_amount,
                    received_by=actor.id,
                    notes=f"Void refund: {reason.strip()}",
                )
            )
            sale.payment_status = PaymentStatus.REFUNDED
        sale.status = SaleStatus.VOIDED
        sale.notes = f"{sale.notes or ''}\nVOID: {reason.strip()}".strip()
        self._audit.record(
            actor_id=actor.id,
            action="SALE_VOIDED",
            entity_type="Sale",
            entity_id=sale.id,
            old_value={"status": SaleStatus.COMPLETED.value},
            new_value={"status": SaleStatus.VOIDED.value, "reason": reason.strip()},
        )
        self._session.flush()
        return sale
