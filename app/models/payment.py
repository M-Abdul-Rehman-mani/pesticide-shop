"""Immutable incoming payments and outgoing refunds."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, String, Text, event, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, Mapper, mapped_column, relationship

from app.database.base import Base, UUIDPrimaryKeyMixin, money_column
from app.models.enums import PaymentDirection, PaymentMethod

if TYPE_CHECKING:
    from app.models.purchase import Purchase
    from app.models.sale import Sale
    from app.models.user import User


class Payment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "num_nonnulls(sale_id, purchase_id) = 1",
            name="exactly_one_document",
        ),
        Index("ix_payments_created", "created_at"),
        Index("ix_payments_sale", "sale_id"),
        Index("ix_payments_purchase", "purchase_id"),
    )

    sale_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales.id", ondelete="RESTRICT")
    )
    purchase_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchases.id", ondelete="RESTRICT")
    )
    method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod, name="payment_method", create_type=False), nullable=False
    )
    direction: Mapped[PaymentDirection] = mapped_column(
        Enum(PaymentDirection, name="payment_direction"), nullable=False
    )
    amount: Mapped[Decimal] = money_column()
    reference: Mapped[str | None] = mapped_column(String(160))
    received_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    notes: Mapped[str | None] = mapped_column(Text)

    sale: Mapped[Sale | None] = relationship()
    purchase: Mapped[Purchase | None] = relationship()
    receiver: Mapped[User] = relationship(lazy="joined")


def _immutable_payment(_mapper: Mapper[Payment], _connection: object, _target: object) -> None:
    raise ValueError("Payments are immutable; create a correcting transaction")


event.listen(Payment, "before_update", _immutable_payment)
event.listen(Payment, "before_delete", _immutable_payment)
