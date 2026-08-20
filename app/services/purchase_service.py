"""Atomic supplier purchase and serialized inventory intake workflow."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import PaymentDirection, PhoneStatus, PurchaseStatus
from app.models.inventory import PhoneInventory
from app.models.payment import Payment
from app.models.product import Product
from app.models.purchase import Purchase, PurchaseItem
from app.models.supplier import Supplier
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, require_permission
from app.services.audit_service import AuditService
from app.services.document_service import DocumentNumberService
from app.services.dto import CreatePurchaseCommand
from app.services.inventory_service import InventoryService
from app.services.money_service import document_totals, payment_status
from app.utils.exceptions import ConflictError, NotFoundError, ValidationError
from app.utils.validators import nonnegative_money, validate_imei


class PurchaseService:
    """Create purchases inside the caller-owned SQLAlchemy transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._inventory = InventoryService(session)
        self._audit = AuditService(session)
        self._numbers = DocumentNumberService(session)

    def create(self, command: CreatePurchaseCommand, actor: AuthenticatedUser) -> Purchase:
        require_permission(actor.role, Permission.RECORD_PURCHASE)
        if not command.phones:
            raise ValidationError("A purchase must contain at least one phone.")
        supplier = self._session.execute(
            select(Supplier).where(Supplier.id == command.supplier_id).with_for_update(of=Supplier)
        ).scalar_one_or_none()
        if supplier is None or not supplier.is_active:
            raise NotFoundError("The selected active supplier was not found.")

        normalized_imeis: list[str] = []
        normalized_phone_data: list[tuple[str, str | None]] = []
        for item in command.phones:
            imei_1 = validate_imei(item.imei_1)
            imei_2 = validate_imei(item.imei_2) if item.imei_2 else None
            if imei_2 == imei_1:
                raise ValidationError("A phone's two IMEI numbers must be different.")
            normalized_phone_data.append((imei_1, imei_2))
            normalized_imeis.append(imei_1)
            if imei_2:
                normalized_imeis.append(imei_2)
        duplicates = [imei for imei, count in Counter(normalized_imeis).items() if count > 1]
        if duplicates:
            raise ConflictError(f"Duplicate IMEI in purchase: {duplicates[0]}")
        existing = self._session.scalar(
            select(PhoneInventory.id)
            .where(
                or_(
                    PhoneInventory.imei_1.in_(normalized_imeis),
                    PhoneInventory.imei_2.in_(normalized_imeis),
                )
            )
            .limit(1)
        )
        if existing:
            raise ConflictError("One or more IMEI numbers already exist in inventory.")

        product_ids = {item.product_id for item in command.phones}
        products = set(
            self._session.scalars(
                select(Product.id).where(Product.id.in_(product_ids), Product.is_active.is_(True))
            ).all()
        )
        if products != product_ids:
            raise NotFoundError("One or more selected products do not exist or are inactive.")

        prices = [
            nonnegative_money(item.purchase_price, field="Purchase price")
            for item in command.phones
        ]
        subtotal = sum(prices, Decimal("0.00"))
        subtotal, discount, tax, total = document_totals(subtotal, command.discount, command.tax)
        payment_amounts = [
            nonnegative_money(item.amount, field="Payment") for item in command.payments
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

        for phone_input, (imei_1, imei_2), purchase_price in zip(
            command.phones, normalized_phone_data, prices, strict=True
        ):
            selling_price = nonnegative_money(phone_input.selling_price, field="Selling price")
            line = PurchaseItem(
                purchase_id=purchase.id,
                product_id=phone_input.product_id,
                quantity=1,
                purchase_price=purchase_price,
                line_total=purchase_price,
            )
            self._session.add(line)
            self._session.flush()
            phone = PhoneInventory(
                product_id=phone_input.product_id,
                imei_1=imei_1,
                imei_2=imei_2,
                serial_number=phone_input.serial_number.strip()
                if phone_input.serial_number
                else None,
                purchase_id=purchase.id,
                purchase_item_id=line.id,
                purchase_price=purchase_price,
                selling_price=selling_price,
                status=PhoneStatus.IN_STOCK,
                supplier_id=supplier.id,
                condition=phone_input.condition,
                warranty_start=phone_input.warranty_start,
                warranty_end=phone_input.warranty_end,
                location=phone_input.location,
                notes=phone_input.notes,
            )
            if (
                phone.warranty_start
                and phone.warranty_end
                and phone.warranty_end < phone.warranty_start
            ):
                raise ValidationError("Warranty end date cannot be before its start date.")
            self._session.add(phone)
            try:
                self._session.flush()
            except IntegrityError as exc:
                raise ConflictError(
                    "An IMEI or serial number already exists in inventory."
                ) from exc
            self._inventory.record_purchase(
                phone, reference_id=purchase.id, actor_id=actor.id, notes=purchase.purchase_number
            )

        for payment_input, amount in zip(command.payments, payment_amounts, strict=True):
            if amount == Decimal("0.00"):
                continue
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
            action="PURCHASE_CREATED",
            entity_type="Purchase",
            entity_id=purchase.id,
            new_value={
                "purchase_number": purchase.purchase_number,
                "total": str(total),
                "phone_count": len(command.phones),
            },
        )
        self._session.flush()
        return purchase
