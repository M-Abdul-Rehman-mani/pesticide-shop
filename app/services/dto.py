"""Typed command objects accepted by transactional services."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from app.models.enums import PaymentMethod


@dataclass(frozen=True, slots=True)
class PaymentInput:
    method: PaymentMethod
    amount: Decimal
    reference: str | None = None


@dataclass(frozen=True, slots=True)
class PurchasedBatchInput:
    product_id: uuid.UUID
    batch_number: str
    quantity: int
    purchase_price: Decimal
    selling_price: Decimal
    manufacture_date: date | None = None
    expiry_date: date | None = None
    cartons: int = 0
    packs_per_carton: int = 0
    location: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class CreateStockPurchaseCommand:
    supplier_id: uuid.UUID
    batches: tuple[PurchasedBatchInput, ...]
    payments: tuple[PaymentInput, ...] = field(default_factory=tuple)
    purchase_date: datetime | None = None
    discount: Decimal = Decimal("0.00")
    tax: Decimal = Decimal("0.00")
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class PesticideSaleLineInput:
    stock_batch_id: uuid.UUID
    quantity: int
    unit_price: Decimal | None = None
    discount: Decimal = Decimal("0.00")
    other_cost: Decimal = Decimal("0.00")

    @property
    def price(self) -> Decimal | None:
        """Compatibility alias for generic cart and reporting integrations."""
        return self.unit_price


@dataclass(frozen=True, slots=True)
class CreatePesticideSaleCommand:
    lines: tuple[PesticideSaleLineInput, ...]
    payments: tuple[PaymentInput, ...]
    customer_id: uuid.UUID | None = None
    dealer_id: uuid.UUID | None = None
    sale_date: datetime | None = None
    order_discount: Decimal = Decimal("0.00")
    tax: Decimal = Decimal("0.00")
    order_number: str | None = None
    territory: str | None = None
    delivery_address: str | None = None
    policy: str | None = None
    store: str | None = None
    notes: str | None = None
