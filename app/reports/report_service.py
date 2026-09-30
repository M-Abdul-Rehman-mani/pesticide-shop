"""Sales, stock, payment, and profit reporting queries."""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql import Subquery

from app.models.customer import Customer
from app.models.dealer import Dealer
from app.models.enums import PaymentDirection, SaleStatus
from app.models.inventory import StockBatch
from app.models.payment import Payment
from app.models.product import Product
from app.models.sale import Sale, SaleItem
from app.models.sale_return import SaleReturn, SaleReturnItem
from app.reports.live_data import (
    live_batch,
    live_customer,
    live_dealer,
    live_payment,
    live_product,
    live_return,
    live_sale,
)
from app.utils.stock import StockLevel, needs_reorder, stock_level


@dataclass(frozen=True, slots=True)
class DateRange:
    start: datetime
    end: datetime

    @classmethod
    def local_days(cls, start: date, end: date, timezone: str) -> DateRange:
        if end < start:
            raise ValueError("End date cannot be before start date")
        zone = ZoneInfo(timezone)
        local_start = datetime.combine(start, time.min, tzinfo=zone)
        local_end = datetime.combine(end + timedelta(days=1), time.min, tzinfo=zone)
        return cls(local_start.astimezone(UTC), local_end.astimezone(UTC))

    @classmethod
    def today(cls, timezone: str) -> DateRange:
        today = datetime.now(ZoneInfo(timezone)).date()
        return cls.local_days(today, today, timezone)


@dataclass(frozen=True, slots=True)
class DashboardMetrics:
    sales: Decimal
    profit: Decimal
    units_sold: int
    current_inventory: int
    low_stock_products: int
    expiring_units: int
    outstanding_payments: Decimal
    #: Credited back for goods returned in the period, and the units that came back.
    returns: Decimal = Decimal("0.00")
    returned_units: int = 0


@dataclass(frozen=True, slots=True)
class SalesReportRow:
    invoice: str
    recipient: str
    product: str
    batch: str
    quantity: int
    salesperson: str
    amount: Decimal
    payment_status: str
    sold_at: datetime


@dataclass(frozen=True, slots=True)
class DailyFinancialPoint:
    day: date
    sales: Decimal
    profit: Decimal


@dataclass(frozen=True, slots=True)
class TopSellingModel:
    product: str
    units: int
    revenue: Decimal


@dataclass(frozen=True, slots=True)
class ProductSummary:
    """Identity of one product, used to build the dashboard's product tabs.

    ``name`` is the short product name that fits a tab; ``full_name`` carries the
    formulation and pack size for tooltips and headings.
    """

    id: uuid.UUID
    name: str
    is_active: bool
    full_name: str = ""

    @property
    def display_name(self) -> str:
        return self.full_name or self.name


@dataclass(frozen=True, slots=True)
class ProductRow:
    """One product on the dashboard's product list, with its period figures."""

    id: uuid.UUID
    name: str
    full_name: str
    manufacturer: str
    is_active: bool
    in_stock: int
    minimum_stock: int
    active_batches: int
    units_sold: int
    sales: Decimal
    profit: Decimal
    last_sold: datetime | None
    units_returned: int = 0
    returns: Decimal = Decimal("0.00")

    @property
    def summary(self) -> ProductSummary:
        return ProductSummary(self.id, self.name, self.is_active, self.full_name)

    @property
    def needs_reorder(self) -> bool:
        return needs_reorder(self.in_stock, self.minimum_stock)

    @property
    def stock_level(self) -> StockLevel:
        """The stock warning to show; a deactivated product is never reordered."""

        if not self.is_active:
            return StockLevel.OK
        return stock_level(self.in_stock, self.minimum_stock)


@dataclass(frozen=True, slots=True)
class ProductMetrics:
    """Everything the dashboard shows for a single product in one period."""

    units_sold: int
    sales: Decimal
    profit: Decimal
    in_stock: int
    active_batches: int
    expiring_units: int
    minimum_stock: int
    stock_value: Decimal
    last_sold: datetime | None
    units_returned: int = 0
    returns: Decimal = Decimal("0.00")

    @property
    def stock_level(self) -> StockLevel:
        return stock_level(self.in_stock, self.minimum_stock)


@dataclass(frozen=True, slots=True)
class ProductBatchRow:
    batch_number: str
    expiry_date: date | None
    received: int
    available: int
    purchase_price: Decimal
    selling_price: Decimal


@dataclass(frozen=True, slots=True)
class PartyMetrics:
    """Headline figures for the customer or dealer side of the business."""

    parties: int
    active_parties: int
    buyers_in_period: int
    invoices: int
    sales: Decimal
    profit: Decimal
    outstanding: Decimal
    average_sale: Decimal
    #: Credited back to this side of the trade for goods returned in the period.
    returns: Decimal = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class PartyRow:
    """One customer or dealer, as shown in the dashboard's ranking table."""

    name: str
    contact: str
    invoices: int
    units: int
    sales: Decimal
    outstanding: Decimal
    last_sold: datetime | None
    returned: Decimal = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class LowStockModel:
    product: str
    in_stock: int
    minimum_stock: int


class ReportService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def dashboard(self, period: DateRange) -> DashboardMetrics:
        sales_total = self._session.scalar(
            select(func.coalesce(func.sum(Sale.total), Decimal("0.00"))).where(
                Sale.status == SaleStatus.COMPLETED,
                live_sale(),
                Sale.sale_date >= period.start,
                Sale.sale_date < period.end,
            )
        )
        units_sold = self._session.scalar(
            select(func.coalesce(func.sum(SaleItem.quantity), 0))
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(
                Sale.status == SaleStatus.COMPLETED,
                live_sale(),
                Sale.sale_date >= period.start,
                Sale.sale_date < period.end,
            )
        )
        current_inventory = self._session.scalar(
            select(func.coalesce(func.sum(StockBatch.quantity_available), 0)).where(live_batch())
        )
        outstanding = self._session.scalar(
            select(func.coalesce(func.sum(Sale.remaining_amount), Decimal("0.00"))).where(
                Sale.status == SaleStatus.COMPLETED, live_sale(), Sale.remaining_amount > 0
            )
        )
        expiring = self._session.scalar(
            select(func.coalesce(func.sum(StockBatch.quantity_available), 0)).where(
                live_batch(),
                StockBatch.quantity_available > 0,
                StockBatch.expiry_date.is_not(None),
                StockBatch.expiry_date <= date.today() + timedelta(days=90),
            )
        )
        returned_units, returned_value = self.returns(period)
        return DashboardMetrics(
            sales=Decimal(sales_total or 0),
            profit=self.profit(period),
            units_sold=int(units_sold or 0),
            current_inventory=int(current_inventory or 0),
            low_stock_products=len(self.low_stock_models()),
            expiring_units=int(expiring or 0),
            outstanding_payments=Decimal(outstanding or 0),
            returns=returned_value,
            returned_units=returned_units,
        )

    def returns(self, period: DateRange) -> tuple[int, Decimal]:
        """Units and value credited back for goods returned in the period."""

        row = self._session.execute(
            select(
                func.coalesce(func.sum(SaleReturnItem.quantity), 0),
                func.coalesce(func.sum(SaleReturnItem.total), Decimal("0.00")),
            )
            .join(SaleReturn, SaleReturn.id == SaleReturnItem.sale_return_id)
            .where(
                live_return(),
                SaleReturn.returned_at >= period.start,
                SaleReturn.returned_at < period.end,
            )
        ).one()
        return int(row[0] or 0), Decimal(row[1] or 0)

    def _returns_by_party(self, period: DateRange, *, dealers: bool) -> dict[uuid.UUID, Decimal]:
        """Value returned in the period, per customer or per dealer.

        Aggregated in one query rather than one per party, and grouped on the return
        lines so a multi-line credit note is not counted once per line.
        """

        identity = Sale.dealer_id if dealers else Sale.customer_id
        rows = self._session.execute(
            select(identity, func.coalesce(func.sum(SaleReturnItem.total), Decimal("0.00")))
            .join(SaleReturn, SaleReturn.id == SaleReturnItem.sale_return_id)
            .join(Sale, Sale.id == SaleReturn.sale_id)
            .where(
                identity.is_not(None),
                live_return(),
                SaleReturn.returned_at >= period.start,
                SaleReturn.returned_at < period.end,
            )
            .group_by(identity)
        )
        return {party_id: Decimal(total or 0) for party_id, total in rows}

    def profit(self, period: DateRange) -> Decimal:
        costs = (
            select(
                SaleItem.sale_id,
                func.sum(SaleItem.purchase_cost + SaleItem.other_cost).label("cost"),
            )
            .group_by(SaleItem.sale_id)
            .subquery()
        )
        value = self._session.scalar(
            select(
                func.coalesce(
                    func.sum(Sale.total - Sale.tax - func.coalesce(costs.c.cost, 0)),
                    Decimal("0.00"),
                )
            )
            .outerjoin(costs, costs.c.sale_id == Sale.id)
            .where(
                Sale.status == SaleStatus.COMPLETED,
                live_sale(),
                Sale.sale_date >= period.start,
                Sale.sale_date < period.end,
            )
        )
        return Decimal(value or 0)

    def sales(self, period: DateRange) -> list[SalesReportRow]:
        documents = self._session.scalars(
            select(Sale)
            .options(selectinload(Sale.items))
            .where(
                Sale.status == SaleStatus.COMPLETED,
                live_sale(),
                Sale.sale_date >= period.start,
                Sale.sale_date < period.end,
            )
            .order_by(Sale.sale_date.desc())
        )
        return [
            SalesReportRow(
                invoice=sale.invoice_number,
                recipient=(
                    sale.dealer.display_name
                    if sale.dealer
                    else sale.customer.name
                    if sale.customer
                    else "Walk-in"
                ),
                product=item.product.display_name,
                batch=item.batch_number,
                quantity=item.quantity,
                salesperson=sale.creator.full_name,
                amount=item.total,
                payment_status=sale.payment_status.value,
                sold_at=sale.sale_date,
            )
            for sale in documents
            for item in sale.items
        ]

    def sales_by_day(self, period: DateRange, timezone: str) -> list[tuple[date, Decimal]]:
        zone = ZoneInfo(timezone)
        rows = self._session.execute(
            select(Sale.sale_date, Sale.total).where(
                Sale.status == SaleStatus.COMPLETED,
                live_sale(),
                Sale.sale_date >= period.start,
                Sale.sale_date < period.end,
            )
        )
        totals: dict[date, Decimal] = defaultdict(lambda: Decimal("0.00"))
        for sold_at, total in rows:
            totals[sold_at.astimezone(zone).date()] += total
        return sorted(totals.items())

    def sales_by_month(self, period: DateRange, timezone: str) -> list[tuple[date, Decimal]]:
        totals: dict[date, Decimal] = defaultdict(lambda: Decimal("0.00"))
        for day, amount in self.sales_by_day(period, timezone):
            totals[day.replace(day=1)] += amount
        return sorted(totals.items())

    def financial_by_day(self, period: DateRange, timezone: str) -> list[DailyFinancialPoint]:
        zone = ZoneInfo(timezone)
        costs = (
            select(
                SaleItem.sale_id,
                func.sum(SaleItem.purchase_cost + SaleItem.other_cost).label("cost"),
            )
            .group_by(SaleItem.sale_id)
            .subquery()
        )
        rows = self._session.execute(
            select(
                Sale.sale_date,
                Sale.total,
                Sale.total - Sale.tax - func.coalesce(costs.c.cost, 0),
            )
            .outerjoin(costs, costs.c.sale_id == Sale.id)
            .where(
                Sale.status == SaleStatus.COMPLETED,
                live_sale(),
                Sale.sale_date >= period.start,
                Sale.sale_date < period.end,
            )
        )
        totals: dict[date, list[Decimal]] = defaultdict(lambda: [Decimal("0.00"), Decimal("0.00")])
        for occurred_at, amount, profit in rows:
            values = totals[occurred_at.astimezone(zone).date()]
            values[0] += Decimal(amount)
            values[1] += Decimal(profit)
        return [
            DailyFinancialPoint(day, values[0], values[1]) for day, values in sorted(totals.items())
        ]

    def top_selling_models(self, period: DateRange, *, limit: int = 5) -> list[TopSellingModel]:
        rows = self._session.execute(
            select(
                Product,
                func.sum(SaleItem.quantity).label("units"),
                func.sum(SaleItem.total).label("revenue"),
            )
            .join(SaleItem, SaleItem.product_id == Product.id)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(
                Sale.status == SaleStatus.COMPLETED,
                live_sale(),
                Sale.sale_date >= period.start,
                Sale.sale_date < period.end,
            )
            .group_by(Product.id)
            .order_by(func.sum(SaleItem.quantity).desc())
            .limit(limit)
        )
        return [
            TopSellingModel(product.display_name, int(units), Decimal(revenue))
            for product, units, revenue in rows
        ]

    @staticmethod
    def low_stock_product_ids() -> Select[tuple[uuid.UUID]]:
        """Products whose active stock is at or below a reorder level that is set.

        The product's total is what is compared -- the same rule as the dashboard --
        never a single batch against it. Products with no batch at all are out of
        stock rather than low, and are not included.
        """

        return (
            select(StockBatch.product_id)
            .join(Product, Product.id == StockBatch.product_id)
            .where(
                StockBatch.is_active.is_(True),
                Product.is_active.is_(True),
                live_product(),
                Product.minimum_stock > 0,
            )
            .group_by(StockBatch.product_id, Product.minimum_stock)
            .having(func.sum(StockBatch.quantity_available) <= Product.minimum_stock)
        )

    def low_stock_models(self, *, limit: int = 100) -> list[LowStockModel]:
        rows = self._session.execute(
            select(Product, func.coalesce(func.sum(StockBatch.quantity_available), 0))
            .outerjoin(
                StockBatch,
                (StockBatch.product_id == Product.id) & StockBatch.is_active.is_(True),
            )
            .where(Product.is_active.is_(True), live_product(), Product.minimum_stock > 0)
            .group_by(Product.id)
            .order_by(Product.manufacturer, Product.name)
        )
        result = [
            LowStockModel(product.display_name, int(stock), product.minimum_stock)
            for product, stock in rows
            if needs_reorder(int(stock), product.minimum_stock)
        ]
        return result[:limit]

    def inventory_status(self) -> dict[str, int]:
        today = date.today()
        in_stock = self._session.scalar(
            select(func.coalesce(func.sum(StockBatch.quantity_available), 0)).where(
                live_batch(),
                StockBatch.quantity_available > 0,
                (StockBatch.expiry_date.is_(None) | (StockBatch.expiry_date >= today)),
            )
        )
        expired = self._session.scalar(
            select(func.coalesce(func.sum(StockBatch.quantity_available), 0)).where(
                live_batch(), StockBatch.quantity_available > 0, StockBatch.expiry_date < today
            )
        )
        out = self._session.scalar(
            select(func.count(StockBatch.id)).where(
                live_batch(), StockBatch.quantity_available == 0
            )
        )
        return {
            "IN_STOCK": int(in_stock or 0),
            "EXPIRED": int(expired or 0),
            "OUT_OF_STOCK_BATCHES": int(out or 0),
        }

    def payment_breakdown(self, period: DateRange) -> dict[str, Decimal]:
        rows = self._session.execute(
            select(Payment.method, func.sum(Payment.amount))
            .where(
                Payment.direction == PaymentDirection.INCOMING,
                live_payment(),
                Payment.created_at >= period.start,
                Payment.created_at < period.end,
            )
            .group_by(Payment.method)
        )
        return {method.value: Decimal(total) for method, total in rows}

    def products(self, *, include_inactive: bool = False) -> list[ProductSummary]:
        """List products for the dashboard's per-product tabs, in display order."""

        statement = (
            select(Product).where(live_product()).order_by(Product.manufacturer, Product.name)
        )
        if not include_inactive:
            statement = statement.where(Product.is_active.is_(True))
        return [
            ProductSummary(
                product.id,
                product.name,
                product.is_active,
                product.display_name,
            )
            for product in self._session.scalars(statement)
        ]

    def product_catalogue(
        self, period: DateRange, *, include_inactive: bool = False
    ) -> list[ProductRow]:
        """List every product with its stock and its trading for the period.

        Aggregated per product in two grouped queries rather than one query per
        product, so a large catalogue stays as quick as a small one.
        """

        sold = {
            product_id: row
            for product_id, *row in self._session.execute(
                select(
                    SaleItem.product_id,
                    func.coalesce(func.sum(SaleItem.quantity), 0),
                    func.coalesce(func.sum(SaleItem.total), Decimal("0.00")),
                    func.coalesce(
                        func.sum(SaleItem.total - SaleItem.purchase_cost - SaleItem.other_cost),
                        Decimal("0.00"),
                    ),
                    func.max(Sale.sale_date),
                )
                .join(Sale, Sale.id == SaleItem.sale_id)
                .where(
                    Sale.status == SaleStatus.COMPLETED,
                    live_sale(),
                    Sale.sale_date >= period.start,
                    Sale.sale_date < period.end,
                )
                .group_by(SaleItem.product_id)
            )
        }
        returned = {
            product_id: row
            for product_id, *row in self._session.execute(
                select(
                    SaleReturnItem.product_id,
                    func.coalesce(func.sum(SaleReturnItem.quantity), 0),
                    func.coalesce(func.sum(SaleReturnItem.total), Decimal("0.00")),
                )
                .join(SaleReturn, SaleReturn.id == SaleReturnItem.sale_return_id)
                .where(
                    live_return(),
                    SaleReturn.returned_at >= period.start,
                    SaleReturn.returned_at < period.end,
                )
                .group_by(SaleReturnItem.product_id)
            )
        }
        stock = {
            product_id: row
            for product_id, *row in self._session.execute(
                select(
                    StockBatch.product_id,
                    func.coalesce(func.sum(StockBatch.quantity_available), 0),
                    func.count(StockBatch.id),
                )
                .where(StockBatch.is_active.is_(True), live_batch())
                .group_by(StockBatch.product_id)
            )
        }
        statement = (
            select(Product).where(live_product()).order_by(Product.manufacturer, Product.name)
        )
        if not include_inactive:
            statement = statement.where(Product.is_active.is_(True))
        rows: list[ProductRow] = []
        for product in self._session.scalars(statement):
            units, sales, profit, last_sold = sold.get(
                product.id, (0, Decimal("0.00"), Decimal("0.00"), None)
            )
            available, batches = stock.get(product.id, (0, 0))
            back, credited = returned.get(product.id, (0, Decimal("0.00")))
            rows.append(
                ProductRow(
                    id=product.id,
                    name=product.name,
                    full_name=product.display_name,
                    manufacturer=product.manufacturer,
                    is_active=product.is_active,
                    in_stock=int(available or 0),
                    minimum_stock=int(product.minimum_stock or 0),
                    active_batches=int(batches or 0),
                    units_sold=int(units or 0),
                    sales=Decimal(sales or 0),
                    profit=Decimal(profit or 0),
                    last_sold=last_sold,
                    units_returned=int(back or 0),
                    returns=Decimal(credited or 0),
                )
            )
        return rows

    def product_dashboard(self, product_id: uuid.UUID, period: DateRange) -> ProductMetrics:
        """Return the same figures as the main dashboard, scoped to one product."""

        sold = self._session.execute(
            select(
                func.coalesce(func.sum(SaleItem.quantity), 0),
                func.coalesce(func.sum(SaleItem.total), Decimal("0.00")),
                func.coalesce(
                    func.sum(SaleItem.total - SaleItem.purchase_cost - SaleItem.other_cost),
                    Decimal("0.00"),
                ),
                func.max(Sale.sale_date),
            )
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(
                SaleItem.product_id == product_id,
                Sale.status == SaleStatus.COMPLETED,
                live_sale(),
                Sale.sale_date >= period.start,
                Sale.sale_date < period.end,
            )
        ).one()
        stock = self._session.execute(
            select(
                func.coalesce(func.sum(StockBatch.quantity_available), 0),
                func.count(StockBatch.id),
                func.coalesce(
                    func.sum(StockBatch.quantity_available * StockBatch.purchase_price),
                    Decimal("0.00"),
                ),
            ).where(StockBatch.product_id == product_id, StockBatch.is_active.is_(True))
        ).one()
        expiring = self._session.scalar(
            select(func.coalesce(func.sum(StockBatch.quantity_available), 0)).where(
                StockBatch.product_id == product_id,
                StockBatch.quantity_available > 0,
                StockBatch.expiry_date.is_not(None),
                StockBatch.expiry_date <= date.today() + timedelta(days=90),
            )
        )
        minimum_stock = self._session.scalar(
            select(Product.minimum_stock).where(Product.id == product_id)
        )
        returned = self._session.execute(
            select(
                func.coalesce(func.sum(SaleReturnItem.quantity), 0),
                func.coalesce(func.sum(SaleReturnItem.total), Decimal("0.00")),
            )
            .join(SaleReturn, SaleReturn.id == SaleReturnItem.sale_return_id)
            .where(
                SaleReturnItem.product_id == product_id,
                live_return(),
                SaleReturn.returned_at >= period.start,
                SaleReturn.returned_at < period.end,
            )
        ).one()
        return ProductMetrics(
            units_sold=int(sold[0] or 0),
            sales=Decimal(sold[1] or 0),
            profit=Decimal(sold[2] or 0),
            in_stock=int(stock[0] or 0),
            active_batches=int(stock[1] or 0),
            expiring_units=int(expiring or 0),
            minimum_stock=int(minimum_stock or 0),
            stock_value=Decimal(stock[2] or 0),
            last_sold=sold[3],
            units_returned=int(returned[0] or 0),
            returns=Decimal(returned[1] or 0),
        )

    def product_financial_by_day(
        self, product_id: uuid.UUID, period: DateRange, timezone: str
    ) -> list[DailyFinancialPoint]:
        """Return one product's daily revenue and profit across the period."""

        zone = ZoneInfo(timezone)
        rows = self._session.execute(
            select(
                Sale.sale_date,
                SaleItem.total,
                SaleItem.total - SaleItem.purchase_cost - SaleItem.other_cost,
            )
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(
                SaleItem.product_id == product_id,
                Sale.status == SaleStatus.COMPLETED,
                live_sale(),
                Sale.sale_date >= period.start,
                Sale.sale_date < period.end,
            )
        )
        totals: dict[date, list[Decimal]] = defaultdict(lambda: [Decimal("0.00"), Decimal("0.00")])
        for occurred_at, amount, profit in rows:
            values = totals[occurred_at.astimezone(zone).date()]
            values[0] += Decimal(amount)
            values[1] += Decimal(profit)
        return [
            DailyFinancialPoint(day, values[0], values[1]) for day, values in sorted(totals.items())
        ]

    def product_batches(self, product_id: uuid.UUID, *, limit: int = 50) -> list[ProductBatchRow]:
        """Return a product's live batches, soonest to expire first."""

        rows = self._session.scalars(
            select(StockBatch)
            .where(StockBatch.product_id == product_id, StockBatch.is_active.is_(True))
            .order_by(StockBatch.expiry_date.asc().nullslast(), StockBatch.created_at)
            .limit(limit)
        )
        return [
            ProductBatchRow(
                batch_number=batch.batch_number,
                expiry_date=batch.expiry_date,
                received=batch.quantity_received,
                available=batch.quantity_available,
                purchase_price=batch.purchase_price,
                selling_price=batch.selling_price,
            )
            for batch in rows
        ]

    def _party_totals(
        self, period: DateRange, *, dealers: bool
    ) -> tuple[int, Decimal, Decimal, int]:
        """Invoice count, revenue, profit, and distinct buyers for one side."""

        recipient = Sale.dealer_id.is_not(None) if dealers else Sale.customer_id.is_not(None)
        identity = Sale.dealer_id if dealers else Sale.customer_id
        costs = (
            select(
                SaleItem.sale_id,
                func.sum(SaleItem.purchase_cost + SaleItem.other_cost).label("cost"),
            )
            .group_by(SaleItem.sale_id)
            .subquery()
        )
        row = self._session.execute(
            select(
                func.count(Sale.id),
                func.coalesce(func.sum(Sale.total), Decimal("0.00")),
                func.coalesce(
                    func.sum(Sale.total - Sale.tax - func.coalesce(costs.c.cost, 0)),
                    Decimal("0.00"),
                ),
                func.count(func.distinct(identity)),
            )
            .outerjoin(costs, costs.c.sale_id == Sale.id)
            .where(
                recipient,
                Sale.status == SaleStatus.COMPLETED,
                live_sale(),
                Sale.sale_date >= period.start,
                Sale.sale_date < period.end,
            )
        ).one()
        return int(row[0] or 0), Decimal(row[1] or 0), Decimal(row[2] or 0), int(row[3] or 0)

    def customer_dashboard(self, period: DateRange) -> PartyMetrics:
        """Headline figures for retail customers."""

        invoices, sales, profit, buyers = self._party_totals(period, dealers=False)
        returned = sum(self._returns_by_party(period, dealers=False).values(), Decimal("0.00"))
        total = (
            self._session.scalar(select(func.count()).select_from(Customer).where(live_customer()))
            or 0
        )
        outstanding = self._session.scalar(
            select(func.coalesce(func.sum(Sale.remaining_amount), Decimal("0.00"))).where(
                Sale.customer_id.is_not(None),
                Sale.status == SaleStatus.COMPLETED,
                live_sale(),
                Sale.remaining_amount > 0,
            )
        )
        return PartyMetrics(
            parties=int(total),
            active_parties=int(total),
            buyers_in_period=buyers,
            invoices=invoices,
            sales=sales,
            profit=profit,
            outstanding=Decimal(outstanding or 0),
            average_sale=(sales / invoices) if invoices else Decimal("0.00"),
            returns=returned,
        )

    def dealer_dashboard(self, period: DateRange) -> PartyMetrics:
        """Headline figures for trade dealers, whose balances sit on account."""

        invoices, sales, profit, buyers = self._party_totals(period, dealers=True)
        returned = sum(self._returns_by_party(period, dealers=True).values(), Decimal("0.00"))
        total = (
            self._session.scalar(select(func.count()).select_from(Dealer).where(live_dealer())) or 0
        )
        active = (
            self._session.scalar(
                select(func.count())
                .select_from(Dealer)
                .where(Dealer.is_active.is_(True), live_dealer())
            )
            or 0
        )
        outstanding = self._session.scalar(
            select(func.coalesce(func.sum(Dealer.balance), Decimal("0.00"))).where(
                Dealer.balance > 0, live_dealer()
            )
        )
        return PartyMetrics(
            parties=int(total),
            active_parties=int(active),
            buyers_in_period=buyers,
            invoices=invoices,
            sales=sales,
            profit=profit,
            outstanding=Decimal(outstanding or 0),
            average_sale=(sales / invoices) if invoices else Decimal("0.00"),
            returns=returned,
        )

    @staticmethod
    def _units_per_sale() -> Subquery:
        """Units per invoice, aggregated before any join.

        Summing an invoice total across a join to its lines multiplies that total by
        the number of lines -- a three-line 900 invoice ranked as 2,700. Rolling the
        lines up first keeps one row per sale, so the totals stay the totals.
        """

        return (
            select(SaleItem.sale_id.label("sale_id"), func.sum(SaleItem.quantity).label("units"))
            .group_by(SaleItem.sale_id)
            .subquery()
        )

    def top_customers(self, period: DateRange, *, limit: int = 25) -> list[PartyRow]:
        """Rank customers by what they bought in the period."""

        units = self._units_per_sale()
        rows = self._session.execute(
            select(
                Customer,
                func.count(Sale.id),
                func.coalesce(func.sum(units.c.units), 0),
                func.coalesce(func.sum(Sale.total), Decimal("0.00")),
                func.max(Sale.sale_date),
            )
            .join(Sale, Sale.customer_id == Customer.id)
            .outerjoin(units, units.c.sale_id == Sale.id)
            .where(
                Sale.status == SaleStatus.COMPLETED,
                live_sale(),
                Sale.sale_date >= period.start,
                Sale.sale_date < period.end,
            )
            .group_by(Customer.id)
            .order_by(func.coalesce(func.sum(Sale.total), Decimal("0.00")).desc())
            .limit(limit)
        ).all()
        outstanding = self._customer_outstanding([customer.id for customer, *_rest in rows])
        returned = self._returns_by_party(period, dealers=False)
        return [
            PartyRow(
                name=customer.name,
                contact=customer.phone or "",
                invoices=int(invoices or 0),
                units=int(units_sold or 0),
                sales=Decimal(sales or 0),
                outstanding=outstanding.get(customer.id, Decimal("0.00")),
                last_sold=last_sold,
                returned=returned.get(customer.id, Decimal("0.00")),
            )
            for customer, invoices, units_sold, sales, last_sold in rows
        ]

    def top_dealers(self, period: DateRange, *, limit: int = 25) -> list[PartyRow]:
        """Rank dealers by what they bought, alongside what they still owe."""

        units = self._units_per_sale()
        rows = self._session.execute(
            select(
                Dealer,
                func.count(Sale.id),
                func.coalesce(func.sum(units.c.units), 0),
                func.coalesce(func.sum(Sale.total), Decimal("0.00")),
                func.max(Sale.sale_date),
            )
            .join(Sale, Sale.dealer_id == Dealer.id)
            .outerjoin(units, units.c.sale_id == Sale.id)
            .where(
                Sale.status == SaleStatus.COMPLETED,
                live_sale(),
                Sale.sale_date >= period.start,
                Sale.sale_date < period.end,
            )
            .group_by(Dealer.id)
            .order_by(func.coalesce(func.sum(Sale.total), Decimal("0.00")).desc())
            .limit(limit)
        )
        returned = self._returns_by_party(period, dealers=True)
        return [
            PartyRow(
                name=dealer.display_name,
                contact=dealer.phone or "",
                invoices=int(invoices or 0),
                units=int(units_sold or 0),
                sales=Decimal(sales or 0),
                outstanding=dealer.balance,
                last_sold=last_sold,
                returned=returned.get(dealer.id, Decimal("0.00")),
            )
            for dealer, invoices, units_sold, sales, last_sold in rows
        ]

    def _customer_outstanding(self, customer_ids: list[uuid.UUID]) -> dict[uuid.UUID, Decimal]:
        """Total unpaid invoice value per customer, in one query for the whole list."""

        if not customer_ids:
            return {}
        rows = self._session.execute(
            select(
                Sale.customer_id, func.coalesce(func.sum(Sale.remaining_amount), Decimal("0.00"))
            )
            .where(
                Sale.customer_id.in_(customer_ids),
                Sale.status == SaleStatus.COMPLETED,
                live_sale(),
                Sale.remaining_amount > 0,
            )
            .group_by(Sale.customer_id)
        )
        return {customer_id: Decimal(total or 0) for customer_id, total in rows}
