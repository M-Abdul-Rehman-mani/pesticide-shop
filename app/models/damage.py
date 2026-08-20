"""Damage and repair lifecycle records."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, money_column
from app.models.enums import DamageStatus, DamageType

if TYPE_CHECKING:
    from app.models.inventory import PhoneInventory
    from app.models.user import User


class DamageRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "damage_records"
    __table_args__ = (
        CheckConstraint("estimated_loss >= 0", name="estimated_loss_nonnegative"),
        CheckConstraint("repair_cost >= 0", name="repair_cost_nonnegative"),
        Index("ix_damage_records_date", "damage_date"),
        Index("ix_damage_records_phone_status", "phone_id", "status"),
    )

    damage_number: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    phone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("phone_inventory.id", ondelete="RESTRICT"), nullable=False
    )
    imei: Mapped[str] = mapped_column(String(15), nullable=False, index=True)
    damage_type: Mapped[DamageType] = mapped_column(
        Enum(DamageType, name="damage_type"), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_loss: Mapped[Decimal] = money_column()
    repair_cost: Mapped[Decimal] = money_column()
    reported_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    damage_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[DamageStatus] = mapped_column(
        Enum(DamageStatus, name="damage_status"), nullable=False
    )
    resolution: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    phone: Mapped[PhoneInventory] = relationship(lazy="joined")
    reporter: Mapped[User] = relationship(lazy="joined")
