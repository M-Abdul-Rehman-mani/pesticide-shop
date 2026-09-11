"""Sales, stock, payment, and profit reporting queries."""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.customer import Customer
from app.models.dealer import Dealer
from app.models.enums import PaymentDirection, SaleStatus
from app.models.inventory import StockBatch
from app.models.payment import Payment
from app.models.product import Product
from app.models.sale import Sale, SaleItem


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
                Sale.sale_date >= period.start,
                Sale.sale_date < period.end,
            )
        )
        units_sold = self._session.scalar(
            select(func.coalesce(func.sum(SaleItem.quantity), 0))
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(
                Sale.status == SaleStatus.COMPLETED,
                Sale.sale_date >= period.start,
                Sale.sale_date < period.end,
            )
        )
        current_inventory = self._session.scalar(
            select(func.coalesce(func.sum(StockBatch.quantity_available), 0))
        )
        outstanding = self._session.scalar(
            select(func.coalesce(func.sum(Sale.remaining_amount), Decimal("0.00"))).where(
                Sale.status == SaleStatus.COMPLETED, Sale.remaining_amount > 0
            )
        )
        expiring = self._session.scalar(
            select(func.coalesce(func.sum(StockBatch.quantity_available), 0)).where(
                StockBatch.quantity_available > 0,
                StockBatch.expiry_date.is_not(None),
                StockBatch.expiry_date <= date.today() + timedelta(days=90),
            )
        )
        return DashboardMetrics(
            sales=Decimal(sales_total or 0),
            profit=self.profit(period),
            units_sold=int(units_sold or 0),
            current_inventory=int(current_inventory or 0),
            low_stock_products=len(self.low_stock_models()),
            expiring_units=int(expiring or 0),
            outstanding_payments=Decimal(outstanding or 0),
        )

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

    def low_stock_models(self, *, limit: int = 100) -> list[LowStockModel]:
        rows = self._session.execute(
            select(Product, func.coalesce(func.sum(StockBatch.quantity_available), 0))
            .outerjoin(StockBatch, StockBatch.product_id == Product.id)
            .where(Product.is_active.is_(True))
            .group_by(Product.id)
            .order_by(Product.manufacturer, Product.name)
        )
        result = [
            LowStockModel(product.display_name, int(stock), product.minimum_stock)
            for product, stock in rows
            if int(stock) <= product.minimum_stock
        ]
        return result[:limit]

    def inventory_status(self) -> dict[str, int]:
        today = date.today()
        in_stock = self._session.scalar(
            select(func.coalesce(func.sum(StockBatch.quantity_available), 0)).where(
                StockBatch.quantity_available > 0,
                (StockBatch.expiry_date.is_(None) | (StockBatch.expiry_date >= today)),
            )
        )
        expired = self._session.scalar(
            select(func.coalesce(func.sum(StockBatch.quantity_available), 0)).where(
                StockBatch.quantity_available > 0, StockBatch.expiry_date < today
            )
        )
        out = self._session.scalar(
            select(func.count(StockBatch.id)).where(StockBatch.quantity_available == 0)
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
                Payment.created_at >= period.start,
                Payment.created_at < period.end,
            )
            .group_by(Payment.method)
        )
        return {method.value: Decimal(total) for method, total in rows}

    def products(self, *, include_inactive: bool = False) -> list[ProductSummary]:
        """List products for the dashboard's per-product tabs, in display order."""

        statement = select(Product).order_by(Product.manufacturer, Product.name)
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
                Sale.sale_date >= period.start,
                Sale.sale_date < period.end,
            )
        ).one()
        return int(row[0] or 0), Decimal(row[1] or 0), Decimal(row[2] or 0), int(row[3] or 0)

    def customer_dashboard(self, period: DateRange) -> PartyMetrics:
        """Headline figures for retail customers."""

        invoices, sales, profit, buyers = self._party_totals(period, dealers=False)
        total = self._session.scalar(select(func.count()).select_from(Customer)) or 0
        outstanding = self._session.scalar(
            select(func.coalesce(func.sum(Sale.remaining_amount), Decimal("0.00"))).where(
                Sale.customer_id.is_not(None),
                Sale.status == SaleStatus.COMPLETED,
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
        )

    def dealer_dashboard(self, period: DateRange) -> PartyMetrics:
        """Headline figures for trade dealers, whose balances sit on account."""

        invoices, sales, profit, buyers = self._party_totals(period, dealers=True)
        total = self._session.scalar(select(func.count()).select_from(Dealer)) or 0
        active = (
            self._session.scalar(
                select(func.count()).select_from(Dealer).where(Dealer.is_active.is_(True))
            )
            or 0
        )
        outstanding = self._session.scalar(
            select(func.coalesce(func.sum(Dealer.balance), Decimal("0.00"))).where(
                Dealer.balance > 0
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
        )

    def top_customers(self, period: DateRange, *, limit: int = 25) -> list[PartyRow]:
        """Rank customers by what they bought in the period."""

        rows = self._session.execute(
            select(
                Customer,
                func.count(func.distinct(Sale.id)),
                func.coalesce(func.sum(SaleItem.quantity), 0),
                func.coalesce(func.sum(Sale.total), Decimal("0.00")),
                func.max(Sale.sale_date),
            )
            .join(Sale, Sale.customer_id == Customer.id)
            .outerjoin(SaleItem, SaleItem.sale_id == Sale.id)
            .where(
                Sale.status == SaleStatus.COMPLETED,
                Sale.sale_date >= period.start,
                Sale.sale_date < period.end,
            )
            .group_by(Customer.id)
            .order_by(func.coalesce(func.sum(Sale.total), Decimal("0.00")).desc())
            .limit(limit)
        )
        return [
            PartyRow(
                name=customer.name,
                contact=customer.phone or "",
                invoices=int(invoices or 0),
                units=int(units or 0),
                sales=Decimal(sales or 0),
                outstanding=self._customer_outstanding(customer.id),
                last_sold=last_sold,
            )
            for customer, invoices, units, sales, last_sold in rows
        ]

    def top_dealers(self, period: DateRange, *, limit: int = 25) -> list[PartyRow]:
        """Rank dealers by what they bought, alongside what they still owe."""

        rows = self._session.execute(
            select(
                Dealer,
                func.count(func.distinct(Sale.id)),
                func.coalesce(func.sum(SaleItem.quantity), 0),
                func.coalesce(func.sum(Sale.total), Decimal("0.00")),
                func.max(Sale.sale_date),
            )
            .join(Sale, Sale.dealer_id == Dealer.id)
            .outerjoin(SaleItem, SaleItem.sale_id == Sale.id)
            .where(
                Sale.status == SaleStatus.COMPLETED,
                Sale.sale_date >= period.start,
                Sale.sale_date < period.end,
            )
            .group_by(Dealer.id)
            .order_by(func.coalesce(func.sum(Sale.total), Decimal("0.00")).desc())
            .limit(limit)
        )
        return [
            PartyRow(
                name=dealer.display_name,
                contact=dealer.phone or "",
                invoices=int(invoices or 0),
                units=int(units or 0),
                sales=Decimal(sales or 0),
                outstanding=dealer.balance,
                last_sold=last_sold,
            )
            for dealer, invoices, units, sales, last_sold in rows
        ]

    def _customer_outstanding(self, customer_id: uuid.UUID) -> Decimal:
        value = self._session.scalar(
            select(func.coalesce(func.sum(Sale.remaining_amount), Decimal("0.00"))).where(
                Sale.customer_id == customer_id,
                Sale.status == SaleStatus.COMPLETED,
                Sale.remaining_amount > 0,
            )
        )
        return Decimal(value or 0)
