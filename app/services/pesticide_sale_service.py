"""Quantity-based pesticide sales, inventory ledger, and email notifications."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.dealer import Dealer
from app.models.enums import InventoryTransactionType, PaymentDirection, SaleStatus
from app.models.inventory import StockBatch, StockMovement
from app.models.payment import Payment
from app.models.sale import Sale, SaleItem
from app.models.sale_return import SaleReturn
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, require_permission
from app.services.audit_service import AuditService
from app.services.document_service import DocumentNumberService
from app.services.dto import CreatePesticideSaleCommand
from app.services.money_service import document_totals, payment_status
from app.services.notification_service import NotificationService, QueuedMessage
from app.utils.exceptions import ConflictError, NotFoundError, ValidationError
from app.utils.validators import nonnegative_money


class PesticideSaleService:
    """Commit a customer/dealer sale and decrement exact batches atomically."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._audit = AuditService(session)
        self._numbers = DocumentNumberService(session)
        self._notifications = NotificationService(session)

    def create(
        self,
        command: CreatePesticideSaleCommand,
        actor: AuthenticatedUser,
        *,
        invoice_prefix: str = "INV",
        shop_name: str = "Pesticide Shop",
        owner_email: str | None = None,
    ) -> Sale:
        require_permission(actor.role, Permission.CREATE_SALE)
        if not command.lines:
            raise ValidationError("A sale must contain at least one product.")
        if command.customer_id and command.dealer_id:
            raise ValidationError("Select either a customer or a dealer, not both.")
        batch_ids = [line.stock_batch_id for line in command.lines]
        if len(set(batch_ids)) != len(batch_ids):
            raise ConflictError("Add each batch only once; edit its quantity in the cart.")
        if any(line.quantity <= 0 for line in command.lines):
            raise ValidationError("Sale quantity must be greater than zero.")

        sold_at = command.sale_date or datetime.now(UTC)
        customer = self._session.get(Customer, command.customer_id) if command.customer_id else None
        dealer = (
            self._session.execute(
                select(Dealer).where(Dealer.id == command.dealer_id).with_for_update(of=Dealer)
            ).scalar_one_or_none()
            if command.dealer_id
            else None
        )
        if command.customer_id and customer is None:
            raise NotFoundError("The selected customer was not found.")
        if command.dealer_id and (dealer is None or not dealer.is_active):
            raise NotFoundError("The selected active dealer was not found.")

        batches = list(
            self._session.scalars(
                select(StockBatch)
                .where(StockBatch.id.in_(batch_ids))
                .order_by(StockBatch.id)
                .with_for_update(of=StockBatch)
            ).all()
        )
        by_id = {batch.id: batch for batch in batches}
        if len(by_id) != len(batch_ids):
            raise NotFoundError("One or more selected stock batches were not found.")
        ordered_batches = [by_id[batch_id] for batch_id in batch_ids]
        for line, batch in zip(command.lines, ordered_batches, strict=True):
            if batch.expiry_date and batch.expiry_date < sold_at.date():
                raise ConflictError(f"Batch {batch.batch_number} expired on {batch.expiry_date}.")
            if not batch.is_active or batch.quantity_available < line.quantity:
                raise ConflictError(
                    f"Batch {batch.batch_number} has only "
                    f"{batch.quantity_available} unit(s) available."
                )

        unit_prices: list[Decimal] = []
        discounts: list[Decimal] = []
        other_costs: list[Decimal] = []
        gross_lines: list[Decimal] = []
        for line, batch in zip(command.lines, ordered_batches, strict=True):
            unit_price = nonnegative_money(
                line.unit_price if line.unit_price is not None else batch.selling_price,
                field="Unit price",
            )
            gross = unit_price * line.quantity
            discount = nonnegative_money(line.discount, field="Line discount")
            if discount > gross:
                raise ValidationError("A line discount cannot exceed its line amount.")
            unit_prices.append(unit_price)
            gross_lines.append(gross)
            discounts.append(discount)
            other_costs.append(nonnegative_money(line.other_cost, field="Other cost"))
        subtotal = sum(gross_lines, Decimal("0.00"))
        total_discount = sum(discounts, Decimal("0.00")) + nonnegative_money(
            command.order_discount, field="Order discount"
        )
        subtotal, total_discount, tax, total = document_totals(
            subtotal, total_discount, command.tax
        )
        if total <= 0:
            raise ValidationError("A completed sale total must be greater than zero.")
        payments = [
            nonnegative_money(payment.amount, field="Payment") for payment in command.payments
        ]
        paid = sum(payments, Decimal("0.00"))
        status = payment_status(total, paid)
        remaining = total - paid
        if dealer and dealer.credit_limit > 0 and dealer.balance + remaining > dealer.credit_limit:
            raise ConflictError("This sale would exceed the dealer's credit limit.")

        recipient_type = "DEALER" if dealer else "CUSTOMER"
        sale = Sale(
            invoice_number=self._numbers.next_number("invoice", invoice_prefix, sold_at),
            customer_id=customer.id if customer else None,
            dealer_id=dealer.id if dealer else None,
            recipient_type=recipient_type,
            order_number=command.order_number.strip() if command.order_number else None,
            territory=(command.territory or (dealer.territory if dealer else None)),
            delivery_address=(
                command.delivery_address
                or (dealer.address if dealer else customer.address if customer else None)
            ),
            policy=command.policy.strip() if command.policy else None,
            store=command.store.strip() if command.store else None,
            sale_date=sold_at,
            subtotal=subtotal,
            discount=total_discount,
            tax=tax,
            total=total,
            paid_amount=paid,
            remaining_amount=remaining,
            payment_status=status,
            created_by=actor.id,
            status=SaleStatus.COMPLETED,
            notes=command.notes.strip() if command.notes else None,
        )
        self._session.add(sale)
        self._session.flush()

        for line, batch, unit_price, gross, discount, other_cost in zip(
            command.lines,
            ordered_batches,
            unit_prices,
            gross_lines,
            discounts,
            other_costs,
            strict=True,
        ):
            batch.quantity_available -= line.quantity
            self._session.add(
                SaleItem(
                    sale_id=sale.id,
                    stock_batch_id=batch.id,
                    product_id=batch.product_id,
                    batch_number=batch.batch_number,
                    quantity=line.quantity,
                    unit_price=unit_price,
                    price=gross,
                    discount=discount,
                    total=gross - discount,
                    purchase_cost=batch.purchase_price * line.quantity,
                    other_cost=other_cost,
                )
            )
            self._session.add(
                StockMovement(
                    batch_id=batch.id,
                    transaction_type=InventoryTransactionType.SALE,
                    quantity_change=-line.quantity,
                    balance_after=batch.quantity_available,
                    reference_id=sale.id,
                    reference_type="Sale",
                    performed_by=actor.id,
                    created_at=datetime.now(UTC),
                    notes=sale.invoice_number,
                )
            )
        for payment_input, amount in zip(command.payments, payments, strict=True):
            if amount:
                self._session.add(
                    Payment(
                        sale_id=sale.id,
                        # Stamping the dealer keeps their account statement complete,
                        # including the amount paid at the counter.
                        dealer_id=dealer.id if dealer else None,
                        method=payment_input.method,
                        direction=PaymentDirection.INCOMING,
                        amount=amount,
                        reference=payment_input.reference,
                        received_by=actor.id,
                    )
                )
        if dealer:
            dealer.balance += remaining
        self._audit.record(
            actor_id=actor.id,
            action="PESTICIDE_SALE_CREATED",
            entity_type="Sale",
            entity_id=sale.id,
            new_value={
                "invoice_number": sale.invoice_number,
                "recipient_type": recipient_type,
                "total": str(total),
                "quantity": sum(line.quantity for line in command.lines),
            },
        )

        recipient = dealer or customer
        if recipient and recipient.email:
            self._queue_email(
                recipient.email,
                recipient.display_name if isinstance(recipient, Dealer) else recipient.name,
                sale,
                shop_name,
                status.value,
                "sale_dealer" if dealer else "sale_customer",
            )
        if owner_email and (
            recipient is None or owner_email.lower() != (recipient.email or "").lower()
        ):
            recipient_name = (
                dealer.display_name if dealer else customer.name if customer else "Walk-in Customer"
            )
            self._notifications.queue(
                message=QueuedMessage(
                    recipient=owner_email,
                    subject=f"New pesticide sale - {sale.invoice_number}",
                    body_text=(
                        f"Recipient: {recipient_name}\nSalesperson: {actor.full_name}\n"
                        f"Total: {total}\nPayment: {status.value}\n"
                        "The delivery challan / invoice is attached."
                    ),
                ),
                template="sale_owner",
                entity_type="Sale",
                entity_id=sale.id,
            )
        self._session.flush()
        return sale

    def _queue_email(
        self,
        email: str,
        name: str,
        sale: Sale,
        shop_name: str,
        payment_status_text: str,
        template: str,
    ) -> None:
        self._notifications.queue(
            message=QueuedMessage(
                recipient=email,
                subject=f"Delivery Challan / Invoice - {sale.invoice_number}",
                body_text=(
                    f"Dear {name},\n\nThank you for your purchase from {shop_name}.\n\n"
                    f"Invoice: {sale.invoice_number}\nTotal: {sale.total}\n"
                    f"Payment status: {payment_status_text}\n\n"
                    "Your delivery challan / invoice is attached as a PDF."
                ),
            ),
            template=template,
            entity_type="Sale",
            entity_id=sale.id,
        )

    def void(self, sale_id: uuid.UUID, actor: AuthenticatedUser, *, reason: str) -> Sale:
        """Cancel a completed sale, returning its stock and clearing the debt.

        Money is deliberately left alone: any payment must be reversed first, so the
        cash trail stays explicit instead of being unwound implicitly here. A voided
        sale drops out of reports, dealer statements, and payment allocation, which
        all select completed sales only.
        """

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
        if self._session.scalar(
            select(SaleReturn.id).where(SaleReturn.sale_id == sale.id).limit(1)
        ):
            raise ConflictError(
                "Goods have been returned against this invoice, so it can no longer be voided."
            )
        if sale.paid_amount > 0:
            raise ConflictError(
                "This invoice has payments against it. Reverse them first, then void it."
            )
        items = list(self._session.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id)))
        batches = {
            batch.id: batch
            for batch in self._session.scalars(
                select(StockBatch)
                .where(StockBatch.id.in_([item.stock_batch_id for item in items]))
                .order_by(StockBatch.id)
                .with_for_update(of=StockBatch)
            )
        }
        voided_at = datetime.now(UTC)
        for item in items:
            batch = batches.get(item.stock_batch_id)
            if batch is None:
                raise NotFoundError("A stock batch for this sale no longer exists.")
            if batch.quantity_available + item.quantity > batch.quantity_received:
                raise ConflictError(
                    f"Returning batch {batch.batch_number} would exceed the quantity received."
                )
            batch.quantity_available += item.quantity
            self._session.add(
                StockMovement(
                    batch_id=batch.id,
                    transaction_type=InventoryTransactionType.ADJUSTMENT,
                    quantity_change=item.quantity,
                    balance_after=batch.quantity_available,
                    reference_id=sale.id,
                    reference_type="Sale Void",
                    performed_by=actor.id,
                    created_at=voided_at,
                    notes=f"Voided {sale.invoice_number}: {reason.strip()}",
                )
            )
        if sale.dealer_id:
            dealer = self._session.execute(
                select(Dealer).where(Dealer.id == sale.dealer_id).with_for_update(of=Dealer)
            ).scalar_one()
            dealer.balance -= sale.remaining_amount
        sale.status = SaleStatus.VOIDED
        self._audit.record(
            actor_id=actor.id,
            action="SALE_VOIDED",
            entity_type="Sale",
            entity_id=sale.id,
            old_value={"status": SaleStatus.COMPLETED.value, "total": str(sale.total)},
            new_value={"status": SaleStatus.VOIDED.value, "reason": reason.strip()},
        )
        self._session.flush()
        return sale

    def amend(
        self,
        sale_id: uuid.UUID,
        command: CreatePesticideSaleCommand,
        actor: AuthenticatedUser,
    ) -> Sale:
        """Correct an unpaid invoice in place, moving only the stock that changed.

        Re-keying a sale as a void plus a new invoice burns an invoice number and
        breaks the delivery reference the customer already has, so the document is
        amended instead. Payments stay out of it: an invoice with money against it
        must have that reversed first, which keeps the cash trail explicit.
        """

        require_permission(actor.role, Permission.CREATE_SALE)
        if not command.lines:
            raise ValidationError("A sale must contain at least one product.")
        if command.customer_id and command.dealer_id:
            raise ValidationError("Select either a customer or a dealer, not both.")
        batch_ids = [line.stock_batch_id for line in command.lines]
        if len(set(batch_ids)) != len(batch_ids):
            raise ConflictError("Add each batch only once; edit its quantity in the cart.")
        if any(line.quantity <= 0 for line in command.lines):
            raise ValidationError("Sale quantity must be greater than zero.")

        sale = self._session.execute(
            select(Sale).where(Sale.id == sale_id).with_for_update(of=Sale)
        ).scalar_one_or_none()
        if sale is None:
            raise NotFoundError("Sale was not found.")
        if sale.status is not SaleStatus.COMPLETED:
            raise ConflictError("Only a completed sale can be edited.")
        # A return credits the invoice like a payment, so check for it first or the
        # operator is told to reverse payments they never took.
        if self._session.scalar(
            select(SaleReturn.id).where(SaleReturn.sale_id == sale.id).limit(1)
        ):
            raise ConflictError(
                "Goods have been returned against this invoice, so it can no longer be edited."
            )
        if sale.paid_amount > 0:
            raise ConflictError(
                "This invoice has payments against it. Reverse them first, then edit it."
            )

        existing = list(self._session.scalars(select(SaleItem).where(SaleItem.sale_id == sale.id)))
        wanted = {line.stock_batch_id: line for line in command.lines}
        touched = set(wanted) | {item.stock_batch_id for item in existing}
        batches = {
            batch.id: batch
            for batch in self._session.scalars(
                select(StockBatch)
                .where(StockBatch.id.in_(touched))
                .order_by(StockBatch.id)
                .with_for_update(of=StockBatch)
            )
        }
        if len(batches) != len(touched):
            raise NotFoundError("One or more selected stock batches were not found.")

        customer = self._session.get(Customer, command.customer_id) if command.customer_id else None
        dealer = (
            self._session.execute(
                select(Dealer).where(Dealer.id == command.dealer_id).with_for_update(of=Dealer)
            ).scalar_one_or_none()
            if command.dealer_id
            else None
        )
        if command.customer_id and customer is None:
            raise NotFoundError("The selected customer was not found.")
        if command.dealer_id and (dealer is None or not dealer.is_active):
            raise NotFoundError("The selected active dealer was not found.")

        sold_at = sale.sale_date
        previous = {item.stock_batch_id: item.quantity for item in existing}
        for batch_id, line in wanted.items():
            batch = batches[batch_id]
            delta = line.quantity - previous.get(batch_id, 0)
            if delta > 0:
                if batch.expiry_date and batch.expiry_date < sold_at.date():
                    raise ConflictError(
                        f"Batch {batch.batch_number} expired on {batch.expiry_date}."
                    )
                if not batch.is_active or batch.quantity_available < delta:
                    raise ConflictError(
                        f"Batch {batch.batch_number} has only "
                        f"{batch.quantity_available} unit(s) available."
                    )

        unit_prices: list[Decimal] = []
        discounts: list[Decimal] = []
        other_costs: list[Decimal] = []
        gross_lines: list[Decimal] = []
        ordered_batches = [batches[line.stock_batch_id] for line in command.lines]
        for line, batch in zip(command.lines, ordered_batches, strict=True):
            unit_price = nonnegative_money(
                line.unit_price if line.unit_price is not None else batch.selling_price,
                field="Unit price",
            )
            gross = unit_price * line.quantity
            discount = nonnegative_money(line.discount, field="Line discount")
            if discount > gross:
                raise ValidationError("A line discount cannot exceed its line amount.")
            unit_prices.append(unit_price)
            gross_lines.append(gross)
            discounts.append(discount)
            other_costs.append(nonnegative_money(line.other_cost, field="Other cost"))
        subtotal = sum(gross_lines, Decimal("0.00"))
        total_discount = sum(discounts, Decimal("0.00")) + nonnegative_money(
            command.order_discount, field="Order discount"
        )
        subtotal, total_discount, tax, total = document_totals(
            subtotal, total_discount, command.tax
        )
        if total <= 0:
            raise ValidationError("A completed sale total must be greater than zero.")
        if dealer and dealer.credit_limit > 0:
            projected = dealer.balance - sale.remaining_amount + total
            if projected > dealer.credit_limit:
                raise ConflictError("This sale would exceed the dealer's credit limit.")

        amended_at = datetime.now(UTC)
        old_value = {
            "total": str(sale.total),
            "lines": [{"batch": item.batch_number, "quantity": item.quantity} for item in existing],
        }
        # Put every original quantity back, then take the new ones, so a batch that
        # merely changed quantity records one net movement.
        for item in existing:
            previous_batch = batches[item.stock_batch_id]
            wanted_line = wanted.get(item.stock_batch_id)
            new_quantity = wanted_line.quantity if wanted_line else 0
            delta = new_quantity - item.quantity
            if delta:
                previous_batch.quantity_available -= delta
                self._session.add(
                    StockMovement(
                        batch_id=previous_batch.id,
                        transaction_type=InventoryTransactionType.ADJUSTMENT,
                        quantity_change=-delta,
                        balance_after=previous_batch.quantity_available,
                        reference_id=sale.id,
                        reference_type="Sale Edit",
                        performed_by=actor.id,
                        created_at=amended_at,
                        notes=f"Edited {sale.invoice_number}",
                    )
                )
            if wanted_line is None:
                self._session.delete(item)
        for line, batch, unit_price, gross, discount, other_cost in zip(
            command.lines,
            ordered_batches,
            unit_prices,
            gross_lines,
            discounts,
            other_costs,
            strict=True,
        ):
            current = next((i for i in existing if i.stock_batch_id == batch.id), None)
            if current is None:
                batch.quantity_available -= line.quantity
                self._session.add(
                    StockMovement(
                        batch_id=batch.id,
                        transaction_type=InventoryTransactionType.SALE,
                        quantity_change=-line.quantity,
                        balance_after=batch.quantity_available,
                        reference_id=sale.id,
                        reference_type="Sale Edit",
                        performed_by=actor.id,
                        created_at=amended_at,
                        notes=f"Added to {sale.invoice_number}",
                    )
                )
                self._session.add(
                    SaleItem(
                        sale_id=sale.id,
                        stock_batch_id=batch.id,
                        product_id=batch.product_id,
                        batch_number=batch.batch_number,
                        quantity=line.quantity,
                        unit_price=unit_price,
                        price=gross,
                        discount=discount,
                        total=gross - discount,
                        purchase_cost=batch.purchase_price * line.quantity,
                        other_cost=other_cost,
                    )
                )
                continue
            current.quantity = line.quantity
            current.unit_price = unit_price
            current.price = gross
            current.discount = discount
            current.total = gross - discount
            current.purchase_cost = batch.purchase_price * line.quantity
            current.other_cost = other_cost

        if dealer is not None:
            dealer.balance += total - sale.remaining_amount
        elif sale.dealer_id:
            previous_dealer = self._session.execute(
                select(Dealer).where(Dealer.id == sale.dealer_id).with_for_update(of=Dealer)
            ).scalar_one()
            previous_dealer.balance -= sale.remaining_amount
        sale.customer_id = customer.id if customer else None
        sale.dealer_id = dealer.id if dealer else None
        sale.recipient_type = "DEALER" if dealer else "CUSTOMER"
        sale.order_number = command.order_number.strip() if command.order_number else None
        sale.territory = command.territory or (dealer.territory if dealer else None)
        sale.delivery_address = command.delivery_address or (
            dealer.address if dealer else customer.address if customer else None
        )
        sale.policy = command.policy.strip() if command.policy else None
        sale.store = command.store.strip() if command.store else None
        sale.subtotal = subtotal
        sale.discount = total_discount
        sale.tax = tax
        sale.total = total
        sale.remaining_amount = total
        sale.payment_status = payment_status(total, Decimal("0.00"))
        sale.notes = command.notes.strip() if command.notes else None
        self._audit.record(
            actor_id=actor.id,
            action="SALE_AMENDED",
            entity_type="Sale",
            entity_id=sale.id,
            old_value=old_value,
            new_value={
                "total": str(total),
                "lines": [
                    {"batch": batch.batch_number, "quantity": line.quantity}
                    for line, batch in zip(command.lines, ordered_batches, strict=True)
                ],
            },
        )
        self._session.flush()
        return sale
