"""Date-filtered business reports with PDF, Excel, and native print export."""

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
from app.models.enums import SaleStatus
from app.models.inventory import PhoneInventory
from app.models.payment import Payment
from app.models.sale import Sale
from app.models.user import User
from app.printing.printer_service import PrinterService
from app.reports.excel_exporter import ExcelExporter
from app.reports.pdf_exporter import PDFReportExporter
from app.reports.report_service import DateRange, ReportService
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
        self._session_factory = session_factory
        self._actor = actor
        self._settings = settings
        self._worker: FunctionWorker | None = None
        self._payload: ReportPayload | None = None
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        title = QLabel("Reports")
        title.setObjectName("PageTitle")
        self.report_type = QComboBox()
        report_names = ["Sales", "Returns", "Damage", "Inventory", "Payments", "Employee Sales"]
        if has_permission(actor.role, Permission.VIEW_PROFIT):
            report_names.insert(3, "Profit")
        for name in report_names:
            self.report_type.addItem(name)
        self.preset = QComboBox()
        for name in ("Today", "Yesterday", "This Week", "This Month", "Custom"):
            self.preset.addItem(name)
        self.from_date = QDateEdit(QDate.currentDate())
        self.from_date.setCalendarPopup(True)
        self.to_date = QDateEdit(QDate.currentDate())
        self.to_date.setCalendarPopup(True)
        run = QPushButton("Run Report")
        self.excel = QPushButton("Export Excel")
        self.pdf = QPushButton("Export PDF")
        self.print_button = QPushButton("Print")
        for button in (self.excel, self.pdf, self.print_button):
            button.setProperty("secondary", True)
            button.setEnabled(False)
        toolbar.addWidget(title)
        toolbar.addStretch()
        toolbar.addWidget(self.report_type)
        toolbar.addWidget(self.preset)
        toolbar.addWidget(self.from_date)
        toolbar.addWidget(self.to_date)
        toolbar.addWidget(run)
        toolbar.addWidget(self.excel)
        toolbar.addWidget(self.pdf)
        toolbar.addWidget(self.print_button)
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
            with self._session_factory() as session:
                if report_type == "Sales":
                    sales_rows = ReportService(session).sales(period)
                    sales_raw: tuple[tuple[object, ...], ...] = tuple(
                        (
                            row.invoice,
                            row.customer,
                            row.phone,
                            row.imei,
                            row.salesperson,
                            row.amount,
                            row.payment_status,
                            row.sold_at,
                        )
                        for row in sales_rows
                    )
                    return self._payload_for(
                        "Sales Report",
                        (
                            "Invoice",
                            "Customer",
                            "Phone",
                            "IMEI",
                            "Salesperson",
                            "Amount",
                            "Payment",
                            "Date",
                        ),
                        sales_raw,
                        frozenset({6}),
                        frozenset({8}),
                    )
                if report_type == "Returns":
                    return_rows = ReportService(session).returns(period)
                    return_raw: tuple[tuple[object, ...], ...] = tuple(
                        (
                            row.return_number,
                            row.invoice,
                            row.customer,
                            row.imei,
                            row.model,
                            row.reason,
                            row.refund,
                            row.approved_by,
                            row.returned_at,
                        )
                        for row in return_rows
                    )
                    return self._payload_for(
                        "Return Report",
                        (
                            "Return",
                            "Invoice",
                            "Customer",
                            "IMEI",
                            "Model",
                            "Reason",
                            "Refund",
                            "Approved By",
                            "Date",
                        ),
                        return_raw,
                        frozenset({7}),
                        frozenset({9}),
                    )
                if report_type == "Damage":
                    damage_rows = ReportService(session).damages(period)
                    damage_raw: tuple[tuple[object, ...], ...] = tuple(
                        (
                            row.damage_number,
                            row.imei,
                            row.model,
                            row.damage_type,
                            row.estimated_loss,
                            row.repair_cost,
                            row.status,
                            row.reported_by,
                            row.damaged_at,
                        )
                        for row in damage_rows
                    )
                    return self._payload_for(
                        "Damage Report",
                        (
                            "Damage",
                            "IMEI",
                            "Model",
                            "Type",
                            "Estimated Loss",
                            "Repair Cost",
                            "Status",
                            "Reported By",
                            "Date",
                        ),
                        damage_raw,
                        frozenset({5, 6}),
                        frozenset({9}),
                    )
                if report_type == "Profit":
                    value = ReportService(session).profit(period)
                    return self._payload_for(
                        "Profit Report",
                        ("From", "To", "Profit"),
                        ((start, end, value),),
                        frozenset({3}),
                        frozenset({1, 2}),
                    )
                if report_type == "Inventory":
                    phones = session.scalars(
                        select(PhoneInventory)
                        .order_by(PhoneInventory.created_at.desc())
                        .limit(5000)
                    )
                    inventory_raw: tuple[tuple[object, ...], ...] = tuple(
                        (
                            phone.imei_1,
                            phone.imei_2 or "",
                            phone.product.brand,
                            phone.product.model,
                            phone.product.storage,
                            phone.product.color,
                            phone.purchase_price,
                            phone.selling_price,
                            phone.status.value,
                            phone.supplier.name,
                            phone.created_at,
                        )
                        for phone in phones
                    )
                    return self._payload_for(
                        "Inventory Report",
                        (
                            "IMEI",
                            "IMEI2",
                            "Brand",
                            "Model",
                            "Storage",
                            "Color",
                            "Purchase Price",
                            "Selling Price",
                            "Status",
                            "Supplier",
                            "Added",
                        ),
                        inventory_raw,
                        frozenset({7, 8}),
                        frozenset({11}),
                    )
                if report_type == "Payments":
                    payment_rows = session.execute(
                        select(Payment)
                        .where(Payment.created_at >= period.start, Payment.created_at < period.end)
                        .order_by(Payment.created_at.desc())
                    ).scalars()
                    payment_raw: tuple[tuple[object, ...], ...] = tuple(
                        (
                            payment.direction.value,
                            payment.method.value,
                            payment.amount,
                            payment.reference or "",
                            payment.receiver.full_name,
                            payment.created_at,
                        )
                        for payment in payment_rows
                    )
                    return self._payload_for(
                        "Payment Report",
                        ("Direction", "Method", "Amount", "Reference", "Recorded By", "Date"),
                        payment_raw,
                        frozenset({3}),
                        frozenset({6}),
                    )
                employee_rows = session.execute(
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
                    tuple(tuple(row) for row in employee_rows),
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
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export Excel", "report.xlsx", "Excel workbooks (*.xlsx)"
        )
        if not path:
            return
        payload = self._payload

        def operation() -> Path:
            return ExcelExporter().export(
                Path(path),
                title=payload.title,
                headers=payload.headers,
                rows=payload.rows,
                currency_columns=payload.currency_columns,
                date_columns=payload.date_columns,
            )

        self._worker = start_worker(
            operation,
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
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export PDF", "report.pdf", "PDF documents (*.pdf)"
        )
        if not path:
            return
        self._worker = start_worker(
            lambda: PrinterService().save_pdf(Path(path), self._pdf_bytes()),
            succeeded=lambda saved: QMessageBox.information(self, "Export complete", str(saved)),
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
