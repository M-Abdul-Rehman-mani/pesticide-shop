"""Outstanding customer and supplier payment collection."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.dealer import Dealer
from app.models.enums import PaymentDirection, PaymentMethod, PurchaseStatus, SaleStatus
from app.models.payment import Payment
from app.models.purchase import Purchase
from app.models.sale import Sale
from app.models.supplier import Supplier
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, require_permission
from app.services.audit_service import AuditService
from app.services.money_service import payment_status
from app.utils.exceptions import ConflictError, NotFoundError, ValidationError
from app.utils.validators import nonnegative_money


class PaymentService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._audit = AuditService(session)

    def collect_sale_payment(
        self,
        sale_id: uuid.UUID,
        *,
        actor: AuthenticatedUser,
        method: PaymentMethod,
        amount: Decimal,
        reference: str | None = None,
    ) -> Payment:
        require_permission(actor.role, Permission.CREATE_SALE)
        sale = self._session.execute(
            select(Sale).where(Sale.id == sale_id).with_for_update(of=Sale)
        ).scalar_one_or_none()
        if sale is None:
            raise NotFoundError("Sale was not found.")
        if sale.status is not SaleStatus.COMPLETED:
            raise ConflictError("Payments can only be added to a completed sale.")
        amount = nonnegative_money(amount, field="Payment")
        if amount <= 0:
            raise ValidationError("Payment must be greater than zero.")
        if amount > sale.remaining_amount:
            raise ValidationError("Payment cannot exceed the outstanding sale balance.")
        payment = Payment(
            sale_id=sale.id,
            method=method,
            direction=PaymentDirection.INCOMING,
            amount=amount,
            reference=reference.strip() if reference else None,
            received_by=actor.id,
        )
        self._session.add(payment)
        sale.paid_amount += amount
        sale.remaining_amount -= amount
        sale.payment_status = payment_status(sale.total, sale.paid_amount)
        if sale.dealer_id:
            dealer = self._session.execute(
                select(Dealer).where(Dealer.id == sale.dealer_id).with_for_update(of=Dealer)
            ).scalar_one()
            dealer.balance -= amount
        self._audit.record(
            actor_id=actor.id,
            action="SALE_PAYMENT_RECORDED",
            entity_type="Sale",
            entity_id=sale.id,
            new_value={"amount": str(amount), "method": method.value},
        )
        self._session.flush()
        return payment

    def pay_supplier(
        self,
        purchase_id: uuid.UUID,
        *,
        actor: AuthenticatedUser,
        method: PaymentMethod,
        amount: Decimal,
        reference: str | None = None,
    ) -> Payment:
        require_permission(actor.role, Permission.RECORD_PURCHASE)
        purchase = self._session.execute(
            select(Purchase).where(Purchase.id == purchase_id).with_for_update(of=Purchase)
        ).scalar_one_or_none()
        if purchase is None:
            raise NotFoundError("Purchase was not found.")
        if purchase.status is not PurchaseStatus.COMPLETED:
            raise ConflictError("Payments can only be added to a completed purchase.")
        supplier = self._session.execute(
            select(Supplier).where(Supplier.id == purchase.supplier_id).with_for_update(of=Supplier)
        ).scalar_one()
        amount = nonnegative_money(amount, field="Payment")
        if amount <= 0:
            raise ValidationError("Payment must be greater than zero.")
        if amount > purchase.remaining_amount:
            raise ValidationError("Payment cannot exceed the outstanding purchase balance.")
        payment = Payment(
            purchase_id=purchase.id,
            method=method,
            direction=PaymentDirection.OUTGOING,
            amount=amount,
            reference=reference.strip() if reference else None,
            received_by=actor.id,
        )
        self._session.add(payment)
        purchase.paid_amount += amount
        purchase.remaining_amount -= amount
        purchase.payment_status = payment_status(purchase.total, purchase.paid_amount)
        supplier.balance -= amount
        self._audit.record(
            actor_id=actor.id,
            action="SUPPLIER_PAYMENT_RECORDED",
            entity_type="Purchase",
            entity_id=purchase.id,
            new_value={"amount": str(amount), "method": method.value},
        )
        self._session.flush()
        return payment
