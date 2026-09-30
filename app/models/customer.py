"""Customer model."""

from sqlalchemy import Boolean, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Customer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "customers"
    __table_args__ = (
        Index("ix_customers_name", "name"),
        Index("ix_customers_phone", "phone"),
    )

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    email: Mapped[str | None] = mapped_column(String(254))
    address: Mapped[str | None] = mapped_column(Text)
    cnic: Mapped[str | None] = mapped_column(String(32), unique=True)
    notes: Mapped[str | None] = mapped_column(Text)
    #: A practice record (see scripts/create_test_data.py); never counted in any figure.
    is_test: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
