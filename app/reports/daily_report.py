"""The owner's daily shop report, emailed on request from Settings."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.email.configuration import parse_owner_emails
from app.models.email_history import EmailHistory
from app.models.enums import EmailStatus, SettingCategory
from app.reports.pdf_exporter import PDFReportExporter
from app.reports.report_service import DateRange, ReportService
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, require_permission
from app.services.settings_service import SettingsService
from app.utils.exceptions import ValidationError

#: Email History groups every copy of one day's report under this entity type.
DAILY_REPORT_ENTITY = "DailyReport"


def daily_report_id(report_date: date) -> uuid.UUID:
    """The same id for every copy of one day's report, so they list together."""

    return uuid.uuid5(uuid.NAMESPACE_URL, f"pesticide-shop-daily-report:{report_date}")


def queue_daily_report(
    session: Session,
    settings: Settings,
    report_date: date,
    actor: AuthenticatedUser,
) -> uuid.UUID:
    """Build one day's report as a PDF and queue a copy for every owner address.

    Returns the report id; send the queued copies with ``deliver_pending_for``
    using ``DAILY_REPORT_ENTITY`` and that id.
    """

    require_permission(actor.role, Permission.VIEW_REPORTS)
    stored = SettingsService(session, settings.app_secret_key.get_secret_value())
    owner_emails = parse_owner_emails(
        stored.get(SettingCategory.EMAIL, "owner_email", settings.owner_email)
    )
    if not owner_emails:
        raise ValidationError("Add at least one owner email address under Settings > Email first.")
    shop_name = stored.get(SettingCategory.SHOP, "name", "Pesticide Shop") or "Pesticide Shop"
    currency = (
        stored.get(SettingCategory.GENERAL, "currency", settings.app_currency)
        or settings.app_currency
    )
    period = DateRange.local_days(report_date, report_date, settings.app_timezone)
    reports = ReportService(session)
    metrics = reports.dashboard(period)
    rows: list[tuple[str, object]] = [
        ("Total Sales", f"{currency} {metrics.sales:,.0f}"),
        ("Total Profit", f"{currency} {metrics.profit:,.0f}"),
        ("Returns", f"{currency} {metrics.returns:,.0f}"),
        ("Units Sold", metrics.units_sold),
        ("Current Inventory", metrics.current_inventory),
        ("Low Stock Products", metrics.low_stock_products),
        ("Expiring in 90 Days", metrics.expiring_units),
        ("Outstanding Payments", f"{currency} {metrics.outstanding_payments:,.0f}"),
    ]
    rows.extend(
        (f"{method.replace('_', ' ').title()} Received (net)", f"{currency} {amount:,.0f}")
        for method, amount in reports.payment_breakdown(period).items()
    )
    rows.extend(
        (
            f"Top Model — {model.product}",
            f"{model.units} unit(s), {currency} {model.revenue:,.0f}",
        )
        for model in reports.top_selling_models(period)
    )
    rows.extend(
        (
            f"Low Stock — {model.product}",
            f"{model.in_stock} available / minimum {model.minimum_stock}",
        )
        for model in reports.low_stock_models(limit=10)
    )
    payload = PDFReportExporter().render(
        title=f"{shop_name} — Daily Shop Report",
        subtitle=report_date.strftime("%d-%b-%Y"),
        headers=("Metric", "Value"),
        rows=rows,
        landscape_page=False,
    )
    report_id = daily_report_id(report_date)
    session.add_all(
        EmailHistory(
            recipient=owner_email,
            subject=f"Daily Shop Report - {report_date:%d-%b-%Y}",
            template="daily_owner_report",
            entity_type=DAILY_REPORT_ENTITY,
            entity_id=report_id,
            status=EmailStatus.PENDING,
            attempts=0,
            body_text=f"Attached is the {shop_name} daily report for {report_date:%d-%b-%Y}.",
            attachment_name=f"daily-report-{report_date.isoformat()}.pdf",
            attachment_data=payload,
        )
        for owner_email in owner_emails
    )
    session.flush()
    return report_id
