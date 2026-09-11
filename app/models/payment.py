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
    from app.models.dealer import Dealer
    from app.models.purchase import Purchase
    from app.models.sale import Sale
    from app.models.sale_return import SaleReturn
    from app.models.user import User


class Payment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            # A payment settles exactly one sale or one purchase, or -- when a dealer
            # pays more than they currently owe -- sits on their account as credit.
            "num_nonnulls(sale_id, purchase_id) = 1"
            " OR (sale_id IS NULL AND purchase_id IS NULL AND dealer_id IS NOT NULL)",
            name="exactly_one_document",
        ),
        Index("ix_payments_created", "created_at"),
        Index("ix_payments_sale", "sale_id"),
        Index("ix_payments_purchase", "purchase_id"),
        Index("ix_payments_dealer_created", "dealer_id", "created_at"),
        Index("ix_payments_sale_return", "sale_return_id"),
    )

    sale_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sales.id", ondelete="RESTRICT")
    )
    purchase_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchases.id", ondelete="RESTRICT")
    )
    #: Set on every payment received against a dealer account, including the
    #: allocations that settle that dealer's individual invoices.
    dealer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dealers.id", ondelete="RESTRICT")
    )
    #: Set on the credit and the refund a sale return raises. These are not money
    #: the shop received, so they can never be reversed as if they were.
    sale_return_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sale_returns.id", ondelete="RESTRICT")
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
    dealer: Mapped[Dealer | None] = relationship()
    sale_return: Mapped[SaleReturn | None] = relationship()
    receiver: Mapped[User] = relationship(lazy="joined")

    @property
    def is_return_credit(self) -> bool:
        """True when this entry came from goods coming back, not from a payment."""

        return self.sale_return_id is not None

    @property
    def is_account_credit(self) -> bool:
        """True when the payment is dealer credit not yet applied to an invoice."""

        return self.sale_id is None and self.purchase_id is None


def _immutable_payment(_mapper: Mapper[Payment], _connection: object, _target: object) -> None:
    raise ValueError("Payments are immutable; create a correcting transaction")


event.listen(Payment, "before_update", _immutable_payment)
event.listen(Payment, "before_delete", _immutable_payment)
