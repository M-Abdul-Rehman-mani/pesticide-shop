"""Goods returned against a completed sale, with the stock put back."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, money_column

if TYPE_CHECKING:
    from app.models.inventory import StockBatch
    from app.models.product import Product
    from app.models.sale import Sale, SaleItem
    from app.models.user import User


class SaleReturn(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A credit note: what came back from one invoice, and what it was worth."""

    __tablename__ = "sale_returns"
    __table_args__ = (
        CheckConstraint("total >= 0", name="ck_sale_returns_total"),
        Index("ix_sale_returns_sale", "sale_id"),
        Index("ix_sale_returns_date", "returned_at"),
    )

    return_number: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    sale_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales.id", ondelete="RESTRICT"), nullable=False
    )
    returned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total: Mapped[Decimal] = money_column()
    #: True when the money was handed back rather than left on the account.
    refunded: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    sale: Mapped[Sale] = relationship(lazy="joined")
    creator: Mapped[User] = relationship(lazy="joined")
    items: Mapped[list[SaleReturnItem]] = relationship(
        back_populates="sale_return", cascade="all, delete-orphan", lazy="selectin"
    )


class SaleReturnItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sale_return_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_sale_return_items_quantity"),
        CheckConstraint("total >= 0", name="ck_sale_return_items_total"),
        Index("ix_sale_return_items_return", "sale_return_id"),
        Index("ix_sale_return_items_sale_item", "sale_item_id"),
    )

    sale_return_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sale_returns.id", ondelete="CASCADE"), nullable=False
    )
    sale_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sale_items.id", ondelete="RESTRICT"), nullable=False
    )
    stock_batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stock_batches.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(nullable=False)
    unit_price: Mapped[Decimal] = money_column()
    total: Mapped[Decimal] = money_column()
    #: Damaged or expired goods come back but cannot be sold again.
    restocked: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")

    sale_return: Mapped[SaleReturn] = relationship(back_populates="items")
    sale_item: Mapped[SaleItem] = relationship(lazy="joined")
    stock_batch: Mapped[StockBatch] = relationship(lazy="joined")
    product: Mapped[Product] = relationship(lazy="joined")
