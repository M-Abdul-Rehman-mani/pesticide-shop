"""Typed command objects accepted by transactional services."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from app.models.enums import (
    DamageType,
    PaymentMethod,
    PhoneCondition,
    ReturnCondition,
    ReturnReason,
)


@dataclass(frozen=True, slots=True)
class PaymentInput:
    method: PaymentMethod
    amount: Decimal
    reference: str | None = None


@dataclass(frozen=True, slots=True)
class PurchasedPhoneInput:
    product_id: uuid.UUID
    imei_1: str
    purchase_price: Decimal
    selling_price: Decimal
    imei_2: str | None = None
    serial_number: str | None = None
    condition: PhoneCondition = PhoneCondition.NEW
    warranty_start: date | None = None
    warranty_end: date | None = None
    location: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class CreatePurchaseCommand:
    supplier_id: uuid.UUID
    phones: tuple[PurchasedPhoneInput, ...]
    payments: tuple[PaymentInput, ...] = field(default_factory=tuple)
    purchase_date: datetime | None = None
    discount: Decimal = Decimal("0.00")
    tax: Decimal = Decimal("0.00")
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class SaleLineInput:
    imei: str
    price: Decimal | None = None
    discount: Decimal = Decimal("0.00")
    other_cost: Decimal = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class CreateSaleCommand:
    lines: tuple[SaleLineInput, ...]
    payments: tuple[PaymentInput, ...]
    customer_id: uuid.UUID | None = None
    sale_date: datetime | None = None
    order_discount: Decimal = Decimal("0.00")
    tax: Decimal = Decimal("0.00")
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class CreateReturnCommand:
    invoice_number: str
    imei: str
    reason: ReturnReason
    condition: ReturnCondition
    refund_amount: Decimal
    refund_method: PaymentMethod
    approved_by: uuid.UUID
    return_date: datetime | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class CreateDamageCommand:
    imei: str
    damage_type: DamageType
    description: str
    estimated_loss: Decimal = Decimal("0.00")
    repair_cost: Decimal = Decimal("0.00")
    damage_date: datetime | None = None
    notes: str | None = None
