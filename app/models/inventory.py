"""Individually tracked phones and immutable inventory movement ledger."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, Mapper, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, money_column
from app.models.enums import InventoryTransactionType, PhoneCondition, PhoneStatus

if TYPE_CHECKING:
    from app.models.product import Product
    from app.models.purchase import Purchase, PurchaseItem
    from app.models.supplier import Supplier
    from app.models.user import User


class PhoneInventory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "phone_inventory"
    __table_args__ = (
        CheckConstraint("imei_1 ~ '^[0-9]{15}$'", name="imei_1_format"),
        CheckConstraint("imei_2 IS NULL OR imei_2 ~ '^[0-9]{15}$'", name="imei_2_format"),
        CheckConstraint("imei_2 IS NULL OR imei_1 <> imei_2", name="imeis_different"),
        CheckConstraint("purchase_price >= 0", name="purchase_price_nonnegative"),
        CheckConstraint("selling_price >= 0", name="selling_price_nonnegative"),
        CheckConstraint(
            "warranty_end IS NULL OR warranty_start IS NULL OR warranty_end >= warranty_start",
            name="warranty_dates_valid",
        ),
        Index("ix_phone_inventory_product_status", "product_id", "status"),
        Index("ix_phone_inventory_supplier", "supplier_id"),
        Index("ix_phone_inventory_created", "created_at"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    imei_1: Mapped[str] = mapped_column(String(15), nullable=False, unique=True)
    imei_2: Mapped[str | None] = mapped_column(String(15), unique=True)
    serial_number: Mapped[str | None] = mapped_column(String(100), unique=True)
    purchase_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchases.id", ondelete="RESTRICT"), nullable=False
    )
    purchase_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_items.id", ondelete="RESTRICT"), nullable=False
    )
    purchase_price: Mapped[Decimal] = money_column()
    selling_price: Mapped[Decimal] = money_column()
    status: Mapped[PhoneStatus] = mapped_column(
        Enum(PhoneStatus, name="phone_status"), nullable=False, index=True
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False
    )
    condition: Mapped[PhoneCondition] = mapped_column(
        Enum(PhoneCondition, name="phone_condition"), nullable=False
    )
    warranty_start: Mapped[date | None] = mapped_column(Date)
    warranty_end: Mapped[date | None] = mapped_column(Date)
    location: Mapped[str | None] = mapped_column(String(160))
    notes: Mapped[str | None] = mapped_column(Text)

    product: Mapped[Product] = relationship(lazy="joined")
    purchase: Mapped[Purchase] = relationship(lazy="joined")
    purchase_item: Mapped[PurchaseItem] = relationship(lazy="joined")
    supplier: Mapped[Supplier] = relationship(lazy="joined")
    transactions: Mapped[list[InventoryTransaction]] = relationship(
        back_populates="phone", order_by="InventoryTransaction.created_at", lazy="selectin"
    )


class PhoneIMEI(Base):
    """Normalized uniqueness projection for both SIM slots.

    PostgreSQL triggers keep this table synchronized with ``phone_inventory``. A single
    primary-key namespace guarantees that an IMEI cannot be slot 1 on one phone and slot
    2 on another phone, including under concurrent writes.
    """

    __tablename__ = "phone_imeis"
    __table_args__ = (
        CheckConstraint("slot IN (1, 2)", name="valid_slot"),
        UniqueConstraint("phone_id", "slot", name="uq_phone_imei_slot"),
    )

    imei: Mapped[str] = mapped_column(String(15), primary_key=True)
    phone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("phone_inventory.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    slot: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class InventoryTransaction(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "inventory_transactions"
    __table_args__ = (
        Index("ix_inventory_transactions_phone_created", "phone_id", "created_at"),
        Index("ix_inventory_transactions_reference", "reference_type", "reference_id"),
    )

    phone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("phone_inventory.id", ondelete="RESTRICT"), nullable=False
    )
    transaction_type: Mapped[InventoryTransactionType] = mapped_column(
        Enum(InventoryTransactionType, name="inventory_transaction_type"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(nullable=False)
    reference_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reference_type: Mapped[str] = mapped_column(String(50), nullable=False)
    previous_status: Mapped[PhoneStatus | None] = mapped_column(
        Enum(PhoneStatus, name="phone_status", create_type=False)
    )
    new_status: Mapped[PhoneStatus] = mapped_column(
        Enum(PhoneStatus, name="phone_status", create_type=False), nullable=False
    )
    performed_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    notes: Mapped[str | None] = mapped_column(Text)

    phone: Mapped[PhoneInventory] = relationship(back_populates="transactions")
    performer: Mapped[User] = relationship(lazy="joined")


def _immutable_ledger(
    _mapper: Mapper[InventoryTransaction], _connection: object, _target: object
) -> None:
    raise ValueError("Inventory transactions are immutable")


event.listen(InventoryTransaction, "before_update", _immutable_ledger)
event.listen(InventoryTransaction, "before_delete", _immutable_ledger)
