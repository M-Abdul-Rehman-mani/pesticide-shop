"""Pesticide and crop-care product catalog."""

from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, money_column


class Product(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint(
            "manufacturer", "name", "formulation", "pack_size", name="uq_product_variant"
        ),
        CheckConstraint("minimum_stock >= 0", name="minimum_stock_nonnegative"),
        CheckConstraint("default_purchase_price >= 0", name="purchase_price_nonnegative"),
        CheckConstraint("default_sale_price >= 0", name="sale_price_nonnegative"),
        Index("ix_products_manufacturer_name", "manufacturer", "name"),
    )

    manufacturer: Mapped[str] = mapped_column(String(140), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    active_ingredient: Mapped[str] = mapped_column(
        String(180), nullable=False, default="", server_default=""
    )
    formulation: Mapped[str] = mapped_column(
        String(100), nullable=False, default="", server_default=""
    )
    pack_size: Mapped[str] = mapped_column(
        String(80), nullable=False, default="", server_default=""
    )
    registration_number: Mapped[str] = mapped_column(
        String(100), nullable=False, default="", server_default=""
    )
    unit: Mapped[str] = mapped_column(
        String(30), nullable=False, default="PACK", server_default="PACK"
    )
    category: Mapped[str] = mapped_column(
        String(80), nullable=False, default="PESTICIDE", server_default="PESTICIDE"
    )
    description: Mapped[str | None] = mapped_column(Text)
    default_purchase_price: Mapped[Decimal] = money_column()
    default_sale_price: Mapped[Decimal] = money_column()
    minimum_stock: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    @property
    def display_name(self) -> str:
        return " ".join(filter(None, (self.name, self.formulation, self.pack_size)))

    @property
    def product_name(self) -> str:
        return self.name
