"""Pesticide dealer accounts and contact details."""

from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, money_column


class Dealer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A trade customer that may buy on account and receive invoices by email."""

    __tablename__ = "dealers"
    __table_args__ = (
        CheckConstraint("credit_limit >= 0", name="credit_limit_nonnegative"),
        Index("ix_dealers_name", "name"),
        Index("ix_dealers_phone", "phone"),
        Index("ix_dealers_territory", "territory"),
    )

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    business_name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    email: Mapped[str | None] = mapped_column(String(254))
    address: Mapped[str | None] = mapped_column(Text)
    cnic: Mapped[str | None] = mapped_column(String(32), unique=True)
    tax_number: Mapped[str | None] = mapped_column(String(80), unique=True)
    territory: Mapped[str | None] = mapped_column(String(120))
    credit_limit: Mapped[Decimal] = money_column()
    balance: Mapped[Decimal] = money_column()
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    @property
    def display_name(self) -> str:
        return self.business_name or self.name
