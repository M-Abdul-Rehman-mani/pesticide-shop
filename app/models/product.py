"""Mobile product/model catalog."""

from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, money_column


class Product(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint(
            "brand", "model", "variant", "storage", "ram", "color", name="uq_product_variant"
        ),
        CheckConstraint("minimum_stock >= 0", name="minimum_stock_nonnegative"),
        CheckConstraint("default_purchase_price >= 0", name="purchase_price_nonnegative"),
        CheckConstraint("default_sale_price >= 0", name="sale_price_nonnegative"),
        Index("ix_products_brand_model", "brand", "model"),
    )

    brand: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(140), nullable=False)
    variant: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    storage: Mapped[str] = mapped_column(String(50), nullable=False, default="", server_default="")
    ram: Mapped[str] = mapped_column(String(50), nullable=False, default="", server_default="")
    color: Mapped[str] = mapped_column(String(80), nullable=False, default="", server_default="")
    category: Mapped[str] = mapped_column(
        String(80), nullable=False, default="PHONE", server_default="PHONE"
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
        return " ".join(
            filter(None, (self.brand, self.model, self.variant, self.storage, self.color))
        )
