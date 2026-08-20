"""PostgreSQL-backed dashboard, sales, return, damage, and profit reports."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.damage import DamageRecord
from app.models.enums import PaymentDirection, PhoneStatus, ReturnStatus, SaleStatus
from app.models.inventory import PhoneInventory
from app.models.product import Product
from app.models.return_record import ReturnItem, SaleReturn
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
    returns: Decimal
    damage: Decimal
    profit: Decimal
    phones_sold: int
    phones_returned: int
    damaged_phones: int
    current_inventory: int
    low_stock_models: int
    outstanding_payments: Decimal


@dataclass(frozen=True, slots=True)
class SalesReportRow:
    invoice: str
    customer: str
    phone: str
    imei: str
    salesperson: str
    amount: Decimal
    payment_status: str
    sold_at: datetime


@dataclass(frozen=True, slots=True)
class ReturnReportRow:
    return_number: str
    invoice: str
    customer: str
    imei: str
    model: str
    reason: str
    refund: Decimal
    approved_by: str
    returned_at: datetime


@dataclass(frozen=True, slots=True)
class DamageReportRow:
    damage_number: str
    imei: str
    model: str
    damage_type: str
    estimated_loss: Decimal
    repair_cost: Decimal
    status: str
    reported_by: str
    damaged_at: datetime


@dataclass(frozen=True, slots=True)
class DailyFinancialPoint:
    day: date
    sales: Decimal
    returns: Decimal
    damage: Decimal
    profit: Decimal


@dataclass(frozen=True, slots=True)
class TopSellingModel:
    product: str
    units: int
    revenue: Decimal


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
        phones_sold = self._session.scalar(
            select(func.count(SaleItem.id))
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(
                Sale.status == SaleStatus.COMPLETED,
                Sale.sale_date >= period.start,
                Sale.sale_date < period.end,
            )
        )
        return_total, phones_returned = self._session.execute(
            select(
                func.coalesce(func.sum(SaleReturn.refund_amount), Decimal("0.00")),
                func.count(SaleReturn.id),
            ).where(
                SaleReturn.status == ReturnStatus.COMPLETED,
                SaleReturn.return_date >= period.start,
                SaleReturn.return_date < period.end,
            )
        ).one()
        damage_total, damaged_phones = self._session.execute(
            select(
                func.coalesce(func.sum(DamageRecord.estimated_loss), Decimal("0.00")),
                func.count(DamageRecord.id),
            ).where(
                DamageRecord.damage_date >= period.start,
                DamageRecord.damage_date < period.end,
            )
        ).one()
        current_inventory = int(
            self._session.scalar(
                select(func.count(PhoneInventory.id)).where(
                    PhoneInventory.status.in_(
                        {
                            PhoneStatus.IN_STOCK,
                            PhoneStatus.RESERVED,
                            PhoneStatus.RETURNED,
                            PhoneStatus.DAMAGED,
                            PhoneStatus.SENT_FOR_REPAIR,
                            PhoneStatus.REPAIRED,
                        }
                    )
                )
            )
            or 0
        )
        outstanding = self._session.scalar(
            select(func.coalesce(func.sum(Sale.remaining_amount), Decimal("0.00"))).where(
                Sale.status == SaleStatus.COMPLETED, Sale.remaining_amount > 0
            )
        )
        stock_counts = (
            select(
                Product.id.label("product_id"),
                Product.minimum_stock.label("minimum_stock"),
                func.count(PhoneInventory.id)
                .filter(PhoneInventory.status == PhoneStatus.IN_STOCK)
                .label("stock_count"),
            )
            .outerjoin(PhoneInventory, PhoneInventory.product_id == Product.id)
            .where(Product.is_active.is_(True))
            .group_by(Product.id, Product.minimum_stock)
            .subquery()
        )
        low_stock = int(
            self._session.scalar(
                select(func.count())
                .select_from(stock_counts)
                .where(stock_counts.c.stock_count <= stock_counts.c.minimum_stock)
            )
            or 0
        )
        return DashboardMetrics(
            sales=Decimal(sales_total or 0),
            returns=Decimal(return_total or 0),
            damage=Decimal(damage_total or 0),
            profit=self.profit(period),
            phones_sold=int(phones_sold or 0),
            phones_returned=int(phones_returned or 0),
            damaged_phones=int(damaged_phones or 0),
            current_inventory=current_inventory,
            low_stock_models=low_stock,
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
        sales_profit = self._session.scalar(
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
        returned_profit = self._session.scalar(
            select(
                func.coalesce(
                    func.sum(SaleItem.total - SaleItem.purchase_cost - SaleItem.other_cost),
                    Decimal("0.00"),
                )
            )
            .select_from(SaleReturn)
            .join(ReturnItem, ReturnItem.return_id == SaleReturn.id)
            .join(SaleItem, SaleItem.id == ReturnItem.sale_item_id)
            .where(
                SaleReturn.status == ReturnStatus.COMPLETED,
                SaleReturn.return_date >= period.start,
                SaleReturn.return_date < period.end,
            )
        )
        return Decimal(sales_profit or 0) - Decimal(returned_profit or 0)

    def sales(self, period: DateRange) -> list[SalesReportRow]:
        sales = list(
            self._session.scalars(
                select(Sale)
                .options(selectinload(Sale.items))
                .where(
                    Sale.status == SaleStatus.COMPLETED,
                    Sale.sale_date >= period.start,
                    Sale.sale_date < period.end,
                )
                .order_by(Sale.sale_date.desc())
            ).all()
        )
        return [
            SalesReportRow(
                invoice=sale.invoice_number,
                customer=sale.customer.name if sale.customer else "Walk-in",
                phone=item.product.display_name,
                imei=item.imei,
                salesperson=sale.creator.full_name,
                amount=item.total,
                payment_status=sale.payment_status.value,
                sold_at=sale.sale_date,
            )
            for sale in sales
            for item in sale.items
        ]

    def returns(self, period: DateRange) -> list[ReturnReportRow]:
        documents = list(
            self._session.scalars(
                select(SaleReturn)
                .options(selectinload(SaleReturn.items))
                .where(
                    SaleReturn.status == ReturnStatus.COMPLETED,
                    SaleReturn.return_date >= period.start,
                    SaleReturn.return_date < period.end,
                )
                .order_by(SaleReturn.return_date.desc())
            ).all()
        )
        return [
            ReturnReportRow(
                return_number=document.return_number,
                invoice=document.sale.invoice_number,
                customer=document.customer.name if document.customer else "Walk-in",
                imei=item.sale_item.imei,
                model=item.sale_item.product.display_name,
                reason=document.reason.value,
                refund=item.refund_amount,
                approved_by=document.approver.full_name,
                returned_at=document.return_date,
            )
            for document in documents
            for item in document.items
        ]

    def damages(self, period: DateRange) -> list[DamageReportRow]:
        records = self._session.scalars(
            select(DamageRecord)
            .where(
                DamageRecord.damage_date >= period.start,
                DamageRecord.damage_date < period.end,
            )
            .order_by(DamageRecord.damage_date.desc())
        )
        return [
            DamageReportRow(
                damage_number=record.damage_number,
                imei=record.imei,
                model=record.phone.product.display_name,
                damage_type=record.damage_type.value,
                estimated_loss=record.estimated_loss,
                repair_cost=record.repair_cost,
                status=record.status.value,
                reported_by=record.reporter.full_name,
                damaged_at=record.damage_date,
            )
            for record in records
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
        monthly: dict[date, Decimal] = defaultdict(lambda: Decimal("0.00"))
        for day, amount in self.sales_by_day(period, timezone):
            monthly[day.replace(day=1)] += amount
        return sorted(monthly.items())

    def financial_by_day(self, period: DateRange, timezone: str) -> list[DailyFinancialPoint]:
        """Return chart-ready daily sales, returns, damage, and realized profit."""

        zone = ZoneInfo(timezone)
        totals: dict[date, list[Decimal]] = defaultdict(
            lambda: [Decimal("0.00") for _index in range(4)]
        )
        costs = (
            select(
                SaleItem.sale_id,
                func.sum(SaleItem.purchase_cost + SaleItem.other_cost).label("cost"),
            )
            .group_by(SaleItem.sale_id)
            .subquery()
        )
        sale_rows = self._session.execute(
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
        for occurred_at, amount, profit in sale_rows:
            values = totals[occurred_at.astimezone(zone).date()]
            values[0] += Decimal(amount)
            values[3] += Decimal(profit)
        return_rows = self._session.execute(
            select(
                SaleReturn.return_date,
                SaleReturn.refund_amount,
                SaleItem.total - SaleItem.purchase_cost - SaleItem.other_cost,
            )
            .join(ReturnItem, ReturnItem.return_id == SaleReturn.id)
            .join(SaleItem, SaleItem.id == ReturnItem.sale_item_id)
            .where(
                SaleReturn.status == ReturnStatus.COMPLETED,
                SaleReturn.return_date >= period.start,
                SaleReturn.return_date < period.end,
            )
        )
        for occurred_at, refund, reversed_profit in return_rows:
            values = totals[occurred_at.astimezone(zone).date()]
            values[1] += Decimal(refund)
            values[3] -= Decimal(reversed_profit)
        damage_rows = self._session.execute(
            select(DamageRecord.damage_date, DamageRecord.estimated_loss).where(
                DamageRecord.damage_date >= period.start,
                DamageRecord.damage_date < period.end,
            )
        )
        for occurred_at, loss in damage_rows:
            totals[occurred_at.astimezone(zone).date()][2] += Decimal(loss)
        return [
            DailyFinancialPoint(day, values[0], values[1], values[2], values[3])
            for day, values in sorted(totals.items())
        ]

    def top_selling_models(self, period: DateRange, *, limit: int = 5) -> list[TopSellingModel]:
        rows = self._session.execute(
            select(
                Product,
                func.count(SaleItem.id).label("units"),
                func.coalesce(func.sum(SaleItem.total), Decimal("0.00")).label("revenue"),
            )
            .join(SaleItem, SaleItem.product_id == Product.id)
            .join(Sale, Sale.id == SaleItem.sale_id)
            .where(
                Sale.status == SaleStatus.COMPLETED,
                Sale.sale_date >= period.start,
                Sale.sale_date < period.end,
            )
            .group_by(Product.id)
            .order_by(func.count(SaleItem.id).desc(), func.sum(SaleItem.total).desc())
            .limit(limit)
        )
        return [
            TopSellingModel(product.display_name, int(units), Decimal(revenue))
            for product, units, revenue in rows
        ]

    def low_stock_models(self, *, limit: int = 100) -> list[LowStockModel]:
        rows = self._session.execute(
            select(
                Product,
                func.count(PhoneInventory.id)
                .filter(PhoneInventory.status == PhoneStatus.IN_STOCK)
                .label("stock"),
            )
            .outerjoin(PhoneInventory, PhoneInventory.product_id == Product.id)
            .where(Product.is_active.is_(True))
            .group_by(Product.id)
            .order_by(Product.brand, Product.model)
        )
        result = [
            LowStockModel(product.display_name, int(stock), product.minimum_stock)
            for product, stock in rows
            if int(stock) <= product.minimum_stock
        ]
        return result[:limit]

    def inventory_status(self) -> dict[str, int]:
        rows = self._session.execute(
            select(PhoneInventory.status, func.count(PhoneInventory.id)).group_by(
                PhoneInventory.status
            )
        )
        return {status.value: int(count) for status, count in rows}

    def payment_breakdown(self, period: DateRange) -> dict[str, Decimal]:
        from app.models.payment import Payment

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
