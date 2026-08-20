from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.enums import PaymentMethod
from app.models.inventory import PhoneInventory
from app.reports.excel_exporter import ExcelExporter
from app.reports.pdf_exporter import PDFReportExporter
from app.reports.report_service import DateRange, ReportService
from app.security.authentication import AuthenticatedUser
from app.services.dto import CreateSaleCommand, PaymentInput, SaleLineInput
from app.services.payment_service import PaymentService
from app.services.sale_service import SaleService
from app.utils.exceptions import ValidationError


def test_dashboard_daily_sales_and_outstanding_payment(
    db_session: Session,
    owner: AuthenticatedUser,
    customer: Customer,
    purchase_phone: object,
) -> None:
    phone: PhoneInventory = purchase_phone("350000000000401", None)  # type: ignore[operator]
    sale = SaleService(db_session).create(
        CreateSaleCommand(
            customer_id=customer.id,
            lines=(SaleLineInput(phone.imei_1),),
            payments=(PaymentInput(PaymentMethod.CASH, Decimal("50000.00")),),
        ),
        owner,
    )
    db_session.flush()
    period = DateRange(datetime.now(UTC) - timedelta(days=1), datetime.now(UTC) + timedelta(days=1))
    reports = ReportService(db_session)
    metrics = reports.dashboard(period)
    assert metrics.sales == Decimal("125000.00")
    assert metrics.phones_sold == 1
    assert metrics.outstanding_payments == Decimal("75000.00")
    assert reports.sales(period)[0].imei == phone.imei_1
    assert reports.sales_by_day(period, "UTC")[0][1] == Decimal("125000.00")
    assert reports.sales_by_month(period, "UTC")[0][1] == Decimal("125000.00")
    daily = reports.financial_by_day(period, "UTC")
    assert daily[0].sales == Decimal("125000.00")
    assert daily[0].profit == Decimal("25000.00")
    assert reports.top_selling_models(period)[0].units == 1
    assert reports.low_stock_models()[0].in_stock == 0
    assert reports.inventory_status() == {"SOLD": 1}
    payment = PaymentService(db_session).collect_sale_payment(
        sale.id,
        actor=owner,
        method=PaymentMethod.BANK_TRANSFER,
        amount=Decimal("75000.00"),
        reference="FINAL-PAYMENT",
    )
    assert payment.amount == Decimal("75000.00")
    assert sale.remaining_amount == Decimal("0.00")


def test_outstanding_payment_cannot_be_overpaid(
    db_session: Session,
    owner: AuthenticatedUser,
    purchase_phone: object,
) -> None:
    phone: PhoneInventory = purchase_phone("350000000000402", None)  # type: ignore[operator]
    sale = SaleService(db_session).create(
        CreateSaleCommand(
            lines=(SaleLineInput(phone.imei_1),),
            payments=(),
        ),
        owner,
    )
    with pytest.raises(ValidationError, match="outstanding"):
        PaymentService(db_session).collect_sale_payment(
            sale.id,
            actor=owner,
            method=PaymentMethod.CASH,
            amount=Decimal("125000.01"),
        )


def test_report_exports(tmp_path: object) -> None:
    from pathlib import Path

    directory = Path(str(tmp_path))
    rows = (("INV-1", Decimal("125000.00"), datetime.now(UTC)),)
    excel = ExcelExporter().export(
        directory / "sales.xlsx",
        title="Sales",
        headers=("Invoice", "Amount", "Date"),
        rows=rows,
        currency_columns=frozenset({2}),
        date_columns=frozenset({3}),
    )
    pdf = PDFReportExporter().render(
        title="Sales",
        headers=("Invoice", "Amount", "Date"),
        rows=rows,
    )
    assert excel.read_bytes().startswith(b"PK")
    assert pdf.startswith(b"%PDF")
