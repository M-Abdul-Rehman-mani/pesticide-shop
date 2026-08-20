"""Fast indexed inventory lookup and paginated listing."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.enums import PhoneStatus
from app.models.inventory import InventoryTransaction, PhoneInventory
from app.models.product import Product
from app.models.sale import Sale, SaleItem
from app.repositories.base import Page, paginate
from app.utils.validators import validate_imei


@dataclass(frozen=True, slots=True)
class InventoryFilters:
    search: str = ""
    status: PhoneStatus | None = None
    supplier_id: uuid.UUID | None = None
    created_from: date | None = None
    created_to: date | None = None
    low_stock_only: bool = False


@dataclass(frozen=True, slots=True)
class IMEILookupDetails:
    phone_id: uuid.UUID
    imei: str
    product: str
    status: PhoneStatus
    customer: str | None
    invoice: str | None
    sale_date: datetime | None


class InventoryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_by_imei(self, imei: str) -> PhoneInventory | None:
        normalized = validate_imei(imei)
        return self._session.execute(
            select(PhoneInventory).where(
                or_(PhoneInventory.imei_1 == normalized, PhoneInventory.imei_2 == normalized)
            )
        ).scalar_one_or_none()

    def lookup_details(self, imei: str) -> IMEILookupDetails | None:
        phone = self.find_by_imei(imei)
        if phone is None:
            return None
        latest_sale = self._session.execute(
            select(Sale)
            .join(SaleItem, SaleItem.sale_id == Sale.id)
            .where(SaleItem.phone_id == phone.id)
            .order_by(Sale.sale_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        return IMEILookupDetails(
            phone_id=phone.id,
            imei=phone.imei_1,
            product=phone.product.display_name,
            status=phone.status,
            customer=(latest_sale.customer.name if latest_sale and latest_sale.customer else None),
            invoice=latest_sale.invoice_number if latest_sale else None,
            sale_date=latest_sale.sale_date if latest_sale else None,
        )

    def search(
        self, filters: InventoryFilters, page: int = 1, page_size: int = 50
    ) -> Page[PhoneInventory]:
        statement = select(PhoneInventory).join(PhoneInventory.product)
        search = filters.search.strip()
        if search:
            pattern = f"%{search}%"
            statement = statement.where(
                or_(
                    PhoneInventory.imei_1.ilike(pattern),
                    PhoneInventory.imei_2.ilike(pattern),
                    Product.brand.ilike(pattern),
                    Product.model.ilike(pattern),
                )
            )
        if filters.status:
            statement = statement.where(PhoneInventory.status == filters.status)
        if filters.supplier_id:
            statement = statement.where(PhoneInventory.supplier_id == filters.supplier_id)
        if filters.created_from:
            statement = statement.where(
                func.date(PhoneInventory.created_at) >= filters.created_from
            )
        if filters.created_to:
            statement = statement.where(func.date(PhoneInventory.created_at) <= filters.created_to)
        if filters.low_stock_only:
            stock_count = (
                select(func.count(PhoneInventory.id))
                .where(
                    PhoneInventory.product_id == Product.id,
                    PhoneInventory.status == PhoneStatus.IN_STOCK,
                )
                .correlate(Product)
                .scalar_subquery()
            )
            statement = statement.where(stock_count <= Product.minimum_stock)
        statement = statement.order_by(PhoneInventory.created_at.desc(), PhoneInventory.id)
        return paginate(self._session, statement, page, page_size)

    def history(self, phone_id: uuid.UUID) -> list[InventoryTransaction]:
        return list(
            self._session.scalars(
                select(InventoryTransaction)
                .where(InventoryTransaction.phone_id == phone_id)
                .order_by(InventoryTransaction.created_at, InventoryTransaction.id)
            ).all()
        )
