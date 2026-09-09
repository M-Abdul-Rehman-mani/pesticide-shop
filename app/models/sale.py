"""Customer and dealer sales with quantity-based product lines."""

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
    Integer,
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
    from app.models.dealer import Dealer
    from app.models.inventory import StockBatch
    from app.models.product import Product
    from app.models.user import User


class Sale(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sales"
    __table_args__ = (
        CheckConstraint("subtotal >= 0", name="ck_sales_subtotal"),
        CheckConstraint("discount >= 0 AND discount <= subtotal", name="ck_sales_discount"),
        CheckConstraint("tax >= 0", name="ck_sales_tax"),
        CheckConstraint("total = subtotal - discount + tax", name="ck_sales_total"),
        CheckConstraint("paid_amount >= 0 AND paid_amount <= total", name="ck_sales_paid"),
        CheckConstraint("remaining_amount = total - paid_amount", name="ck_sales_remaining"),
        CheckConstraint(
            "NOT (customer_id IS NOT NULL AND dealer_id IS NOT NULL)",
            name="ck_sales_one_recipient",
        ),
        Index("ix_sales_date", "sale_date"),
        Index("ix_sales_customer_date", "customer_id", "sale_date"),
        Index("ix_sales_dealer_date", "dealer_id", "sale_date"),
        Index("ix_sales_created_by_date", "created_by", "sale_date"),
    )

    invoice_number: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="RESTRICT")
    )
    dealer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dealers.id", ondelete="RESTRICT")
    )
    recipient_type: Mapped[str] = mapped_column(String(20), nullable=False, default="CUSTOMER")
    order_number: Mapped[str | None] = mapped_column(String(60))
    territory: Mapped[str | None] = mapped_column(String(120))
    delivery_address: Mapped[str | None] = mapped_column(Text)
    policy: Mapped[str | None] = mapped_column(String(120))
    store: Mapped[str | None] = mapped_column(String(120))
    sale_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    subtotal: Mapped[Decimal] = money_column()
    discount: Mapped[Decimal] = money_column()
    tax: Mapped[Decimal] = money_column()
    total: Mapped[Decimal] = money_column()
    paid_amount: Mapped[Decimal] = money_column()
    remaining_amount: Mapped[Decimal] = money_column()
    payment_status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status"), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[SaleStatus] = mapped_column(Enum(SaleStatus, name="sale_status"), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    #: How many times a printable copy has been issued. Anything after the first is
    #: stamped DUPLICATE so two apparent originals cannot circulate.
    print_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    customer: Mapped[Customer | None] = relationship(lazy="joined")
    dealer: Mapped[Dealer | None] = relationship(lazy="joined")
    creator: Mapped[User] = relationship(lazy="joined")
    items: Mapped[list[SaleItem]] = relationship(
        back_populates="sale", cascade="all, delete-orphan", lazy="selectin"
    )


class SaleItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sale_items"
    __table_args__ = (
        UniqueConstraint("sale_id", "stock_batch_id", name="uq_sale_items_batch"),
        CheckConstraint("quantity > 0", name="ck_sale_items_quantity"),
        CheckConstraint("unit_price >= 0", name="ck_sale_items_unit_price"),
        CheckConstraint("price = unit_price * quantity", name="ck_sale_items_gross"),
        CheckConstraint("discount >= 0 AND discount <= price", name="ck_sale_items_discount"),
        CheckConstraint("total = price - discount", name="ck_sale_items_total"),
        Index("ix_sale_items_stock_batch", "stock_batch_id"),
        Index("ix_sale_items_product", "product_id"),
    )

    sale_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales.id", ondelete="RESTRICT"), nullable=False
    )
    stock_batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stock_batches.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    batch_number: Mapped[str] = mapped_column(String(100), nullable=False)
    quantity: Mapped[int] = mapped_column(nullable=False)
    unit_price: Mapped[Decimal] = money_column()
    price: Mapped[Decimal] = money_column()
    discount: Mapped[Decimal] = money_column()
    total: Mapped[Decimal] = money_column()
    purchase_cost: Mapped[Decimal] = money_column()
    other_cost: Mapped[Decimal] = money_column()

    sale: Mapped[Sale] = relationship(back_populates="items")
    stock_batch: Mapped[StockBatch] = relationship(lazy="joined")
    product: Mapped[Product] = relationship(lazy="joined")
