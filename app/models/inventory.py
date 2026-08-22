"""Batch stock and append-only inventory movement ledger."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, Mapper, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, money_column
from app.models.enums import InventoryTransactionType

if TYPE_CHECKING:
    from app.models.product import Product
    from app.models.purchase import Purchase, PurchaseItem
    from app.models.supplier import Supplier
    from app.models.user import User


class StockBatch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "stock_batches"
    __table_args__ = (
        UniqueConstraint("product_id", "batch_number", name="uq_stock_batches_product_batch"),
        CheckConstraint("quantity_received > 0", name="ck_stock_batches_received_positive"),
        CheckConstraint(
            "quantity_available >= 0 AND quantity_available <= quantity_received",
            name="ck_stock_batches_available_valid",
        ),
        CheckConstraint("purchase_price >= 0", name="ck_stock_batches_purchase_price"),
        CheckConstraint("selling_price >= 0", name="ck_stock_batches_selling_price"),
        CheckConstraint("cartons >= 0", name="ck_stock_batches_cartons"),
        CheckConstraint("packs_per_carton >= 0", name="ck_stock_batches_packs"),
        CheckConstraint(
            "expiry_date IS NULL OR manufacture_date IS NULL OR expiry_date >= manufacture_date",
            name="ck_stock_batches_dates",
        ),
        Index("ix_stock_batches_product_expiry", "product_id", "expiry_date"),
        Index("ix_stock_batches_supplier", "supplier_id"),
        Index("ix_stock_batches_available", "quantity_available"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False
    )
    purchase_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchases.id", ondelete="RESTRICT"), nullable=False
    )
    purchase_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_items.id", ondelete="RESTRICT"), nullable=False
    )
    batch_number: Mapped[str] = mapped_column(String(100), nullable=False)
    manufacture_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    quantity_received: Mapped[int] = mapped_column(nullable=False)
    quantity_available: Mapped[int] = mapped_column(nullable=False)
    cartons: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    packs_per_carton: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    purchase_price: Mapped[Decimal] = money_column()
    selling_price: Mapped[Decimal] = money_column()
    location: Mapped[str | None] = mapped_column(String(160))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    product: Mapped[Product] = relationship(lazy="joined")
    purchase: Mapped[Purchase] = relationship(lazy="joined")
    purchase_item: Mapped[PurchaseItem] = relationship(lazy="joined")
    supplier: Mapped[Supplier] = relationship(lazy="joined")
    movements: Mapped[list[StockMovement]] = relationship(
        back_populates="batch", order_by="StockMovement.created_at", lazy="selectin"
    )

    @property
    def stock_value(self) -> Decimal:
        return self.purchase_price * self.quantity_available


class StockMovement(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "stock_movements"
    __table_args__ = (
        CheckConstraint("quantity_change <> 0", name="ck_stock_movements_nonzero"),
        CheckConstraint("balance_after >= 0", name="ck_stock_movements_balance"),
        Index("ix_stock_movements_batch_created", "batch_id", "created_at"),
        Index("ix_stock_movements_reference", "reference_type", "reference_id"),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stock_batches.id", ondelete="RESTRICT"), nullable=False
    )
    transaction_type: Mapped[InventoryTransactionType] = mapped_column(
        Enum(InventoryTransactionType, name="inventory_transaction_type"), nullable=False
    )
    quantity_change: Mapped[int] = mapped_column(nullable=False)
    balance_after: Mapped[int] = mapped_column(nullable=False)
    reference_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reference_type: Mapped[str] = mapped_column(String(50), nullable=False)
    performed_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    notes: Mapped[str | None] = mapped_column(Text)

    batch: Mapped[StockBatch] = relationship(back_populates="movements")
    performer: Mapped[User] = relationship(lazy="joined")


def _immutable_movement(
    _mapper: Mapper[StockMovement], _connection: object, _target: object
) -> None:
    raise ValueError("Stock movements are immutable; create an adjustment instead")


event.listen(StockMovement, "before_update", _immutable_movement)
event.listen(StockMovement, "before_delete", _immutable_movement)
