"""Pesticide sales, inventory, expiry, purchase, payment, and profit reports."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import Settings
from app.models.dealer import Dealer
from app.models.enums import SaleStatus
from app.models.inventory import StockBatch
from app.models.payment import Payment
from app.models.purchase import Purchase
from app.models.sale import Sale, SaleItem
from app.models.user import User
from app.printing.printer_service import PrinterService
from app.reports.excel_exporter import ExcelExporter
from app.reports.pdf_exporter import PDFReportExporter
from app.reports.report_service import DateRange
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, has_permission, require_permission
from app.ui.widgets import RowsTableModel, show_error
from app.ui.workers import FunctionWorker, start_worker


@dataclass(frozen=True, slots=True)
class ReportPayload:
    title: str
    headers: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    display_rows: tuple[tuple[object, ...], ...]
    currency_columns: frozenset[int]
    date_columns: frozenset[int]


class ReportsScreen(QWidget):
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        actor: AuthenticatedUser,
        settings: Settings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session_factory, self._actor, self._settings = session_factory, actor, settings
        self._worker: FunctionWorker | None = None
        self._payload: ReportPayload | None = None
        layout, toolbar = QVBoxLayout(self), QHBoxLayout()
        title = QLabel("Reports & Previous Sales")
        title.setObjectName("PageTitle")
        self.report_type = QComboBox()
        names = [
            "Sales History",
            "Inventory",
            "Expiry",
            "Purchases",
            "Payments",
            "Dealer Balances",
            "Employee Sales",
        ]
        if has_permission(actor.role, Permission.VIEW_PROFIT):
            names.insert(1, "Profit")
        self.report_type.addItems(names)
        self.preset = QComboBox()
        self.preset.addItems(
            ("Today", "Yesterday", "This Week", "This Month", "All Time", "Custom")
        )
        self.from_date, self.to_date = (
            QDateEdit(QDate.currentDate()),
            QDateEdit(QDate.currentDate()),
        )
        self.from_date.setCalendarPopup(True)
        self.to_date.setCalendarPopup(True)
        run, self.excel, self.pdf, self.print_button = (
            QPushButton("Run Report"),
            QPushButton("Export Excel"),
            QPushButton("Export PDF"),
            QPushButton("Print"),
        )
        for button in (self.excel, self.pdf, self.print_button):
            button.setProperty("secondary", True)
            button.setEnabled(False)
        for widget in (
            title,
            self.report_type,
            self.preset,
            self.from_date,
            self.to_date,
            run,
            self.excel,
            self.pdf,
            self.print_button,
        ):
            if widget is self.report_type:
                toolbar.addStretch()
            toolbar.addWidget(widget)
        self.model = RowsTableModel((), self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        layout.addLayout(toolbar)
        layout.addWidget(self.table)
        self.preset.currentTextChanged.connect(self._apply_preset)
        run.clicked.connect(self.run_report)
        self.excel.clicked.connect(self._export_excel)
        self.pdf.clicked.connect(self._export_pdf)
        self.print_button.clicked.connect(self._print)
        self._apply_preset("Today")

    def _apply_preset(self, preset: str) -> None:
        today = date.today()
        if preset == "Today":
            start = end = today
        elif preset == "Yesterday":
            start = end = today - timedelta(days=1)
        elif preset == "This Week":
            start, end = today - timedelta(days=today.weekday()), today
        elif preset == "This Month":
            start, end = today.replace(day=1), today
        elif preset == "All Time":
            start, end = date(2000, 1, 1), today
        else:
            return
        self.from_date.setDate(QDate(start.year, start.month, start.day))
        self.to_date.setDate(QDate(end.year, end.month, end.day))

    def run_report(self) -> None:
        require_permission(self._actor.role, Permission.VIEW_REPORTS)
        report_type = self.report_type.currentText()
        start = cast(date, self.from_date.date().toPython())
        end = cast(date, self.to_date.date().toPython())

        def operation() -> ReportPayload:
            period = DateRange.local_days(start, end, self._settings.app_timezone)
            raw: tuple[tuple[object, ...], ...]
            with self._session_factory() as session:
                if report_type == "Sales History":
                    rows = session.execute(
                        select(Sale, SaleItem)
                        .join(SaleItem)
                        .where(Sale.sale_date >= period.start, Sale.sale_date < period.end)
                        .order_by(Sale.sale_date.desc(), Sale.invoice_number)
                    )
                    raw = tuple(
                        (
                            sale.invoice_number,
                            sale.dealer.display_name
                            if sale.dealer
                            else sale.customer.name
                            if sale.customer
                            else "Walk-in",
                            sale.recipient_type,
                            item.product.display_name,
                            item.batch_number,
                            item.quantity,
                            item.unit_price or item.price,
                            item.discount,
                            item.total,
                            sale.payment_status.value,
                            sale.creator.full_name,
                            sale.sale_date,
                        )
                        for sale, item in rows
                    )
                    return self._payload_for(
                        "Complete Sales History",
                        (
                            "Invoice",
                            "Recipient",
                            "Type",
                            "Product",
                            "Batch",
                            "Qty",
                            "Unit Price",
                            "Discount",
                            "Line Total",
                            "Payment",
                            "Salesperson",
                            "Date",
                        ),
                        raw,
                        frozenset({7, 8, 9}),
                        frozenset({12}),
                    )
                if report_type == "Profit":
                    value = session.scalar(
                        select(
                            func.coalesce(
                                func.sum(
                                    SaleItem.total - SaleItem.purchase_cost - SaleItem.other_cost
                                ),
                                Decimal("0.00"),
                            )
                        )
                        .join(Sale)
                        .where(
                            Sale.status == SaleStatus.COMPLETED,
                            Sale.sale_date >= period.start,
                            Sale.sale_date < period.end,
                        )
                    ) or Decimal("0.00")
                    return self._payload_for(
                        "Profit Report",
                        ("From", "To", "Gross Profit"),
                        ((start, end, value),),
                        frozenset({3}),
                        frozenset({1, 2}),
                    )
                if report_type in {"Inventory", "Expiry"}:
                    statement = select(StockBatch).order_by(
                        StockBatch.expiry_date.asc().nullslast()
                    )
                    if report_type == "Expiry":
                        statement = statement.where(
                            StockBatch.expiry_date <= date.today() + timedelta(days=180)
                        )
                    batches = session.scalars(statement)
                    raw = tuple(
                        (
                            b.product.display_name,
                            b.product.manufacturer,
                            b.batch_number,
                            b.quantity_available,
                            b.product.unit,
                            b.expiry_date,
                            b.supplier.company_name or b.supplier.name,
                            b.purchase_price,
                            b.selling_price,
                            b.stock_value,
                        )
                        for b in batches
                    )
                    return self._payload_for(
                        f"{report_type} Report",
                        (
                            "Product",
                            "Manufacturer",
                            "Batch",
                            "Available",
                            "Unit",
                            "Expiry",
                            "Supplier",
                            "Purchase",
                            "Sale",
                            "Value",
                        ),
                        raw,
                        frozenset({8, 9, 10}),
                        frozenset({6}),
                    )
                if report_type == "Purchases":
                    purchases = session.scalars(
                        select(Purchase)
                        .where(
                            Purchase.purchase_date >= period.start,
                            Purchase.purchase_date < period.end,
                        )
                        .order_by(Purchase.purchase_date.desc())
                    )
                    raw = tuple(
                        (
                            p.purchase_number,
                            p.supplier.company_name or p.supplier.name,
                            p.total,
                            p.paid_amount,
                            p.remaining_amount,
                            p.payment_status.value,
                            p.purchase_date,
                        )
                        for p in purchases
                    )
                    return self._payload_for(
                        "Purchase Report",
                        ("Purchase", "Supplier", "Total", "Paid", "Remaining", "Status", "Date"),
                        raw,
                        frozenset({3, 4, 5}),
                        frozenset({7}),
                    )
                if report_type == "Payments":
                    payments = session.scalars(
                        select(Payment)
                        .where(Payment.created_at >= period.start, Payment.created_at < period.end)
                        .order_by(Payment.created_at.desc())
                    )
                    raw = tuple(
                        (
                            p.direction.value,
                            p.method.value,
                            p.amount,
                            p.reference or "",
                            p.receiver.full_name,
                            p.created_at,
                        )
                        for p in payments
                    )
                    return self._payload_for(
                        "Payment Report",
                        ("Direction", "Method", "Amount", "Reference", "Recorded By", "Date"),
                        raw,
                        frozenset({3}),
                        frozenset({6}),
                    )
                if report_type == "Dealer Balances":
                    dealers = session.scalars(select(Dealer).order_by(Dealer.balance.desc()))
                    raw = tuple(
                        (
                            d.display_name,
                            d.name,
                            d.phone,
                            d.territory or "",
                            d.balance,
                            d.credit_limit,
                            d.email or "",
                        )
                        for d in dealers
                    )
                    return self._payload_for(
                        "Dealer Balance Report",
                        (
                            "Dealer",
                            "Contact",
                            "Phone",
                            "Territory",
                            "Balance",
                            "Credit Limit",
                            "Email",
                        ),
                        raw,
                        frozenset({5, 6}),
                        frozenset(),
                    )
                rows = session.execute(
                    select(
                        User.full_name,
                        func.count(Sale.id),
                        func.coalesce(func.sum(Sale.total), Decimal("0.00")),
                    )
                    .join(Sale, Sale.created_by == User.id)
                    .where(
                        Sale.status == SaleStatus.COMPLETED,
                        Sale.sale_date >= period.start,
                        Sale.sale_date < period.end,
                    )
                    .group_by(User.id, User.full_name)
                    .order_by(func.sum(Sale.total).desc())
                )
                return self._payload_for(
                    "Employee Sales Report",
                    ("Employee", "Sales", "Total"),
                    tuple(tuple(row) for row in rows),
                    frozenset({3}),
                    frozenset(),
                )

        self._worker = start_worker(
            operation, succeeded=self._display, failed=lambda error: show_error(self, error)
        )

    def _payload_for(
        self,
        title: str,
        headers: tuple[str, ...],
        rows: tuple[tuple[object, ...], ...],
        currency_columns: frozenset[int],
        date_columns: frozenset[int],
    ) -> ReportPayload:
        display = tuple(
            tuple(
                f"{self._settings.app_currency} {value:,.2f}"
                if index in currency_columns and isinstance(value, Decimal)
                else value.strftime("%d-%b-%Y %H:%M")
                if index in date_columns and isinstance(value, datetime)
                else value.strftime("%d-%b-%Y")
                if index in date_columns and isinstance(value, date)
                else value
                for index, value in enumerate(row, start=1)
            )
            for row in rows
        )
        return ReportPayload(title, headers, rows, display, currency_columns, date_columns)

    def _display(self, payload: object) -> None:
        assert isinstance(payload, ReportPayload)
        self._payload = payload
        self.model = RowsTableModel(payload.headers, self)
        self.model.set_rows(payload.display_rows)
        self.table.setModel(self.model)
        self.table.resizeColumnsToContents()
        for button in (self.excel, self.pdf, self.print_button):
            button.setEnabled(True)

    def _export_excel(self) -> None:
        if not self._payload:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Excel", "report.xlsx", "Excel workbooks (*.xlsx)"
        )
        if not path:
            return
        payload = self._payload
        self._worker = start_worker(
            lambda: ExcelExporter().export(
                Path(path),
                title=payload.title,
                headers=payload.headers,
                rows=payload.rows,
                currency_columns=payload.currency_columns,
                date_columns=payload.date_columns,
            ),
            succeeded=lambda saved: QMessageBox.information(self, "Export complete", str(saved)),
            failed=lambda error: show_error(self, error),
        )

    def _pdf_bytes(self) -> bytes:
        assert self._payload is not None
        return PDFReportExporter().render(
            title=self._payload.title,
            headers=self._payload.headers,
            rows=self._payload.display_rows,
        )

    def _export_pdf(self) -> None:
        if not self._payload:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export PDF", "report.pdf", "PDF documents (*.pdf)"
        )
        if path:
            self._worker = start_worker(
                lambda: PrinterService().save_pdf(Path(path), self._pdf_bytes()),
                succeeded=lambda saved: QMessageBox.information(
                    self, "Export complete", str(saved)
                ),
                failed=lambda error: show_error(self, error),
            )

    def _print(self) -> None:
        if not self._payload:
            return

        def operation() -> Path:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
                handle.write(self._pdf_bytes())
                return Path(handle.name)

        self._worker = start_worker(
            operation,
            succeeded=lambda path: PrinterService().preview_pdf(path, self),
            failed=lambda error: show_error(self, error),
        )
