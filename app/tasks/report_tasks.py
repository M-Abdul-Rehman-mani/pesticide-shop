"""Scheduled daily owner report generation and queuing."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config.settings import get_settings
from app.database.session import SessionFactory
from app.models.email_history import EmailHistory
from app.models.enums import EmailStatus, SettingCategory
from app.reports.pdf_exporter import PDFReportExporter
from app.reports.report_service import DateRange, ReportService
from app.services.settings_service import SettingsService
from app.tasks.celery_app import celery_app
from app.tasks.email_tasks import send_email


@celery_app.task(name="app.tasks.report_tasks.generate_daily_owner_report")
def generate_daily_owner_report(report_date: str | None = None) -> str | None:
    settings = get_settings()
    zone = ZoneInfo(settings.app_timezone)
    selected_date = (
        datetime.strptime(report_date, "%Y-%m-%d").date()
        if report_date
        else (datetime.now(zone) - timedelta(days=1)).date()
    )
    period = DateRange.local_days(selected_date, selected_date, settings.app_timezone)
    with SessionFactory.begin() as session:
        stored = SettingsService(session, settings.app_secret_key.get_secret_value())
        owner_email = stored.get(SettingCategory.EMAIL, "owner_email", settings.owner_email)
        if not owner_email:
            return None
        shop_name = stored.get(SettingCategory.SHOP, "name", "Pesticide Shop") or "Pesticide Shop"
        currency = (
            stored.get(SettingCategory.GENERAL, "currency", settings.app_currency)
            or settings.app_currency
        )
        reports = ReportService(session)
        metrics = reports.dashboard(period)
        payments = reports.payment_breakdown(period)
        top_models = reports.top_selling_models(period)
        low_stock = reports.low_stock_models(limit=10)
        rows = [
            ("Total Sales", f"{currency} {metrics.sales:,.0f}"),
            ("Total Profit", f"{currency} {metrics.profit:,.0f}"),
            ("Units Sold", metrics.units_sold),
            ("Current Inventory", metrics.current_inventory),
            ("Low Stock Products", metrics.low_stock_products),
            ("Expiring in 90 Days", metrics.expiring_units),
            ("Outstanding Payments", f"{currency} {metrics.outstanding_payments:,.0f}"),
        ]
        rows.extend(
            (f"{method.title()} Sales", f"{currency} {amount:,.0f}")
            for method, amount in payments.items()
        )
        rows.extend(
            (
                f"Top Model — {model.product}",
                f"{model.units} unit(s), {currency} {model.revenue:,.0f}",
            )
            for model in top_models
        )
        rows.extend(
            (
                f"Low Stock — {model.product}",
                f"{model.in_stock} available / minimum {model.minimum_stock}",
            )
            for model in low_stock
        )
        payload = PDFReportExporter().render(
            title=f"{shop_name} — Daily Shop Report",
            subtitle=selected_date.strftime("%d-%b-%Y"),
            headers=("Metric", "Value"),
            rows=rows,
            landscape_page=False,
        )
        report_id = uuid.uuid5(uuid.NAMESPACE_URL, f"pesticide-shop-daily-report:{selected_date}")
        history = EmailHistory(
            recipient=owner_email,
            subject=f"Daily Shop Report - {selected_date:%d-%b-%Y}",
            template="daily_owner_report",
            entity_type="DailyReport",
            entity_id=report_id,
            status=EmailStatus.PENDING,
            attempts=0,
            body_text=f"Attached is the {shop_name} daily report for {selected_date:%d-%b-%Y}.",
            attachment_name=f"daily-report-{selected_date.isoformat()}.pdf",
            attachment_data=payload,
        )
        session.add(history)
        session.flush()
        email_id = str(history.id)
    send_email.delay(email_id)
    return email_id
