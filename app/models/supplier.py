"""Supplier model and account balance."""

from sqlalchemy import Boolean, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, money_column


class Supplier(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "suppliers"
    __table_args__ = (
        Index("ix_suppliers_name", "name"),
        Index("ix_suppliers_phone", "phone"),
    )

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    email: Mapped[str | None] = mapped_column(String(254))
    address: Mapped[str | None] = mapped_column(Text)
    tax_number: Mapped[str | None] = mapped_column(String(80), unique=True)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    #: A practice record (see scripts/create_test_data.py); never counted in any figure.
    is_test: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    balance = money_column()
