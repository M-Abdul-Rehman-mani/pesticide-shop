"""Sales invoices and individually serialized line items."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, money_column
from app.models.enums import PaymentStatus, SaleStatus

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.inventory import PhoneInventory
    from app.models.product import Product
    from app.models.user import User


class Sale(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sales"
    __table_args__ = (
        CheckConstraint("subtotal >= 0", name="subtotal_nonnegative"),
        CheckConstraint("discount >= 0 AND discount <= subtotal", name="discount_within_subtotal"),
        CheckConstraint("tax >= 0", name="tax_nonnegative"),
        CheckConstraint("total = subtotal - discount + tax", name="total_matches_components"),
        CheckConstraint("paid_amount >= 0 AND paid_amount <= total", name="paid_within_total"),
        CheckConstraint("remaining_amount = total - paid_amount", name="remaining_matches_total"),
        Index("ix_sales_date", "sale_date"),
        Index("ix_sales_customer_date", "customer_id", "sale_date"),
        Index("ix_sales_created_by_date", "created_by", "sale_date"),
    )

    invoice_number: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="RESTRICT")
    )
    sale_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    subtotal: Mapped[Decimal] = money_column()
    discount: Mapped[Decimal] = money_column()
    tax: Mapped[Decimal] = money_column()
    total: Mapped[Decimal] = money_column()
    paid_amount: Mapped[Decimal] = money_column()
    remaining_amount: Mapped[Decimal] = money_column()
    payment_status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status", create_type=False), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[SaleStatus] = mapped_column(Enum(SaleStatus, name="sale_status"), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    customer: Mapped[Customer | None] = relationship(lazy="joined")
    creator: Mapped[User] = relationship(lazy="joined")
    items: Mapped[list[SaleItem]] = relationship(
        back_populates="sale", cascade="all, delete-orphan", lazy="selectin"
    )


class SaleItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sale_items"
    __table_args__ = (
        UniqueConstraint("sale_id", "phone_id", name="uq_sale_phone"),
        CheckConstraint("price >= 0", name="price_nonnegative"),
        CheckConstraint("discount >= 0 AND discount <= price", name="discount_within_price"),
        CheckConstraint("total = price - discount", name="total_matches_components"),
        Index("ix_sale_items_phone", "phone_id"),
    )

    sale_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales.id", ondelete="RESTRICT"), nullable=False
    )
    phone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("phone_inventory.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    imei: Mapped[str] = mapped_column(String(15), nullable=False)
    price: Mapped[Decimal] = money_column()
    discount: Mapped[Decimal] = money_column()
    total: Mapped[Decimal] = money_column()
    purchase_cost: Mapped[Decimal] = money_column()
    other_cost: Mapped[Decimal] = money_column()

    sale: Mapped[Sale] = relationship(back_populates="items")
    phone: Mapped[PhoneInventory] = relationship(lazy="joined")
    product: Mapped[Product] = relationship(lazy="joined")
