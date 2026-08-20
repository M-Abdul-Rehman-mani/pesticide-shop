"""Sales return documents and returned serialized items."""

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
from app.models.enums import (
    PaymentMethod,
    ReturnCondition,
    ReturnReason,
    ReturnStatus,
)

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.sale import Sale, SaleItem
    from app.models.user import User


class SaleReturn(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sale_returns"
    __table_args__ = (
        CheckConstraint("refund_amount >= 0", name="refund_nonnegative"),
        Index("ix_sale_returns_date", "return_date"),
        Index("ix_sale_returns_sale", "sale_id"),
    )

    return_number: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    sale_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales.id", ondelete="RESTRICT"), nullable=False
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="RESTRICT")
    )
    return_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[ReturnReason] = mapped_column(
        Enum(ReturnReason, name="return_reason"), nullable=False
    )
    condition: Mapped[ReturnCondition] = mapped_column(
        Enum(ReturnCondition, name="return_condition"), nullable=False
    )
    refund_amount: Mapped[Decimal] = money_column()
    refund_method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod, name="payment_method", create_type=False), nullable=False
    )
    approved_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[ReturnStatus] = mapped_column(
        Enum(ReturnStatus, name="return_status"), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text)

    sale: Mapped[Sale] = relationship(lazy="joined")
    customer: Mapped[Customer | None] = relationship(lazy="joined")
    approver: Mapped[User] = relationship(foreign_keys=[approved_by], lazy="joined")
    creator: Mapped[User] = relationship(foreign_keys=[created_by], lazy="joined")
    items: Mapped[list[ReturnItem]] = relationship(
        back_populates="sale_return", cascade="all, delete-orphan", lazy="selectin"
    )


class ReturnItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "return_items"
    __table_args__ = (
        UniqueConstraint("sale_item_id", name="uq_returned_sale_item"),
        CheckConstraint("refund_amount >= 0", name="refund_nonnegative"),
    )

    return_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sale_returns.id", ondelete="RESTRICT"), nullable=False
    )
    sale_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sale_items.id", ondelete="RESTRICT"), nullable=False
    )
    refund_amount: Mapped[Decimal] = money_column()

    sale_return: Mapped[SaleReturn] = relationship(back_populates="items")
    sale_item: Mapped[SaleItem] = relationship(lazy="joined")
