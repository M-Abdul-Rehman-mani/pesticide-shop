"""Pesticide sales, inventory, expiry, purchase, payment, and profit reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

from PySide6.QtCore import QDate, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFileDialog,
    QGridLayout,
    QLabel,
    QLineEdit,
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
from app.models.enums import PurchaseStatus, SaleStatus
from app.models.inventory import StockBatch
from app.models.payment import Payment
from app.models.product import Product
from app.models.purchase import Purchase
from app.models.sale import Sale, SaleItem
from app.models.user import User
from app.printing.preferences import load_print_preferences
from app.printing.print_preview import open_print_preview
from app.printing.printer_service import PrinterService, write_temporary_pdf
from app.reports.excel_exporter import ExcelExporter
from app.reports.pdf_exporter import PDFReportExporter
from app.reports.report_service import DateRange
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, has_permission, require_permission
from app.ui.widgets import (
    RowsTableModel,
    configure_date_edit,
    configure_table,
    record_count_text,
    show_error,
)
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
        self._source_payload: ReportPayload | None = None
        layout, toolbar = QVBoxLayout(self), QGridLayout()
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
            "Daily Cash Closing",
            "Customer / Dealer Statements",
            "Product Profitability",
            "Tax & Discounts",
            "Outstanding Payments",
            "Expiry Loss",
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
        configure_date_edit(self.from_date, self.to_date)
        self.run_button, self.excel, self.pdf, self.print_button = (
            QPushButton("Run Report"),
            QPushButton("Export Excel"),
            QPushButton("Export PDF"),
            QPushButton("Print"),
        )
        for button in (self.excel, self.pdf, self.print_button):
            button.setProperty("secondary", True)
            button.setEnabled(False)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter the displayed report")
        self.search.setClearButtonEnabled(True)
        toolbar.addWidget(title, 0, 0, 1, 2)
        toolbar.addWidget(QLabel("Report"), 1, 0)
        toolbar.addWidget(self.report_type, 1, 1)
        toolbar.addWidget(QLabel("Period"), 1, 2)
        toolbar.addWidget(self.preset, 1, 3)
        toolbar.addWidget(self.from_date, 1, 4)
        toolbar.addWidget(self.to_date, 1, 5)
        toolbar.addWidget(self.run_button, 1, 6)
        toolbar.addWidget(self.search, 2, 0, 1, 4)
        toolbar.addWidget(self.excel, 2, 4)
        toolbar.addWidget(self.pdf, 2, 5)
        toolbar.addWidget(self.print_button, 2, 6)
        toolbar.setColumnStretch(1, 1)
        toolbar.setColumnStretch(3, 1)
        self.model = RowsTableModel((), self)
        self.table = QTableView()
        self.table.setModel(self.model)
        configure_table(self.table, stretch_column=0, minimum_section_size=76)
        self.state_label = QLabel("Choose a report and select Run Report.")
        self.state_label.setObjectName("RecordCount")
        layout.addLayout(toolbar)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.state_label)
        self.preset.currentTextChanged.connect(self._apply_preset)
        self.run_button.clicked.connect(self.run_report)
        self.excel.clicked.connect(self._export_excel)
        self.pdf.clicked.connect(self._export_pdf)
        self.print_button.clicked.connect(self._print)
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(200)
        self._filter_timer.timeout.connect(self._apply_result_filter)
        self.search.textChanged.connect(lambda _text: self._filter_timer.start())
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
        self.run_button.setEnabled(False)
        self.state_label.setText("Loading report…")

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
                if report_type == "Daily Cash Closing":
                    payments = session.scalars(
                        select(Payment).where(
                            Payment.created_at >= period.start, Payment.created_at < period.end
                        )
                    )
                    totals: dict[str, list[Decimal]] = {}
                    for payment in payments:
                        method_totals = totals.setdefault(
                            payment.method.value.replace("_", " ").title(),
                            [Decimal("0.00"), Decimal("0.00")],
                        )
                        method_totals[0 if payment.direction.value == "INCOMING" else 1] += (
                            payment.amount
                        )
                    raw = tuple(
                        (method, amounts[0], amounts[1], amounts[0] - amounts[1])
                        for method, amounts in sorted(totals.items())
                    )
                    return self._payload_for(
                        "Daily Cash Closing",
                        ("Payment Method", "Incoming", "Outgoing", "Net Cash"),
                        raw,
                        frozenset({2, 3, 4}),
                        frozenset(),
                    )
                if report_type == "Customer / Dealer Statements":
                    sales = session.scalars(
                        select(Sale)
                        .where(Sale.sale_date >= period.start, Sale.sale_date < period.end)
                        .order_by(Sale.sale_date.desc())
                    )
                    raw = tuple(
                        (
                            "Dealer" if sale.dealer else "Customer",
                            sale.dealer.display_name
                            if sale.dealer
                            else sale.customer.name
                            if sale.customer
                            else "Walk-in",
                            sale.dealer.phone
                            if sale.dealer
                            else sale.customer.phone
                            if sale.customer
                            else "",
                            sale.invoice_number,
                            sale.total,
                            sale.paid_amount,
                            sale.remaining_amount,
                            sale.payment_status.value,
                            sale.sale_date,
                        )
                        for sale in sales
                    )
                    return self._payload_for(
                        "Customer and Dealer Statement",
                        (
                            "Account Type",
                            "Account",
                            "Phone",
                            "Invoice",
                            "Total",
                            "Paid",
                            "Outstanding",
                            "Payment",
                            "Date",
                        ),
                        raw,
                        frozenset({5, 6, 7}),
                        frozenset({9}),
                    )
                if report_type == "Product Profitability":
                    rows = session.execute(
                        select(
                            Product.name,
                            Product.manufacturer,
                            func.sum(SaleItem.quantity),
                            func.sum(SaleItem.total),
                            func.sum(SaleItem.purchase_cost),
                            func.sum(SaleItem.other_cost),
                            func.sum(SaleItem.total - SaleItem.purchase_cost - SaleItem.other_cost),
                        )
                        .join(SaleItem, SaleItem.product_id == Product.id)
                        .join(Sale, Sale.id == SaleItem.sale_id)
                        .where(
                            Sale.status == SaleStatus.COMPLETED,
                            Sale.sale_date >= period.start,
                            Sale.sale_date < period.end,
                        )
                        .group_by(Product.id, Product.name, Product.manufacturer)
                        .order_by(
                            func.sum(
                                SaleItem.total - SaleItem.purchase_cost - SaleItem.other_cost
                            ).desc()
                        )
                    )
                    return self._payload_for(
                        "Product Profitability",
                        ("Product", "Manufacturer", "Units", "Revenue", "Cost", "Other", "Profit"),
                        tuple(tuple(row) for row in rows),
                        frozenset({4, 5, 6, 7}),
                        frozenset(),
                    )
                if report_type == "Tax & Discounts":
                    sales = session.scalars(
                        select(Sale).where(
                            Sale.status == SaleStatus.COMPLETED,
                            Sale.sale_date >= period.start,
                            Sale.sale_date < period.end,
                        )
                    )
                    purchases = session.scalars(
                        select(Purchase).where(
                            Purchase.status == PurchaseStatus.COMPLETED,
                            Purchase.purchase_date >= period.start,
                            Purchase.purchase_date < period.end,
                        )
                    )
                    tax_rows = [
                        (
                            "Sale",
                            item.invoice_number,
                            item.subtotal,
                            item.discount,
                            item.tax,
                            item.total,
                            item.sale_date,
                        )
                        for item in sales
                    ]
                    tax_rows.extend(
                        (
                            "Purchase",
                            item.purchase_number,
                            item.subtotal,
                            item.discount,
                            item.tax,
                            item.total,
                            item.purchase_date,
                        )
                        for item in purchases
                    )
                    tax_rows.sort(key=lambda row: row[6], reverse=True)
                    return self._payload_for(
                        "Tax and Discount Report",
                        ("Type", "Document", "Subtotal", "Discount", "Tax", "Total", "Date"),
                        tuple(tax_rows),
                        frozenset({3, 4, 5, 6}),
                        frozenset({7}),
                    )
                if report_type == "Outstanding Payments":
                    sales = session.scalars(
                        select(Sale).where(
                            Sale.remaining_amount > 0,
                            Sale.status == SaleStatus.COMPLETED,
                            Sale.sale_date >= period.start,
                            Sale.sale_date < period.end,
                        )
                    )
                    purchases = session.scalars(
                        select(Purchase).where(
                            Purchase.remaining_amount > 0,
                            Purchase.status == PurchaseStatus.COMPLETED,
                            Purchase.purchase_date >= period.start,
                            Purchase.purchase_date < period.end,
                        )
                    )
                    outstanding_rows = [
                        (
                            "Receivable",
                            item.invoice_number,
                            item.dealer.display_name
                            if item.dealer
                            else item.customer.name
                            if item.customer
                            else "Walk-in",
                            item.total,
                            item.paid_amount,
                            item.remaining_amount,
                            item.sale_date,
                        )
                        for item in sales
                    ]
                    outstanding_rows.extend(
                        (
                            "Payable",
                            item.purchase_number,
                            item.supplier.company_name or item.supplier.name,
                            item.total,
                            item.paid_amount,
                            item.remaining_amount,
                            item.purchase_date,
                        )
                        for item in purchases
                    )
                    outstanding_rows.sort(key=lambda row: row[6], reverse=True)
                    return self._payload_for(
                        "Outstanding Payments",
                        ("Type", "Document", "Account", "Total", "Paid", "Outstanding", "Date"),
                        tuple(outstanding_rows),
                        frozenset({4, 5, 6}),
                        frozenset({7}),
                    )
                if report_type == "Expiry Loss":
                    batches = session.scalars(
                        select(StockBatch)
                        .where(
                            StockBatch.expiry_date < date.today(),
                            StockBatch.quantity_available > 0,
                        )
                        .order_by(StockBatch.expiry_date)
                    )
                    raw = tuple(
                        (
                            batch.product.display_name,
                            batch.batch_number,
                            batch.expiry_date,
                            batch.quantity_available,
                            batch.purchase_price,
                            batch.stock_value,
                            batch.supplier.company_name or batch.supplier.name,
                        )
                        for batch in batches
                    )
                    return self._payload_for(
                        "Expired Stock Loss",
                        (
                            "Product",
                            "Batch",
                            "Expired",
                            "Units",
                            "Unit Cost",
                            "Potential Loss",
                            "Supplier",
                        ),
                        raw,
                        frozenset({5, 6}),
                        frozenset({3}),
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
            operation,
            succeeded=self._display,
            failed=lambda error: show_error(self, error),
            finished=lambda: self.run_button.setEnabled(True),
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
                f"{self._settings.app_currency} {value:,.0f}"
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
        self._source_payload = payload
        self._apply_result_filter()
        for button in (self.excel, self.pdf, self.print_button):
            button.setEnabled(True)

    def _apply_result_filter(self) -> None:
        if self._source_payload is None:
            return
        query = self.search.text().strip().casefold()
        pairs = list(zip(self._source_payload.rows, self._source_payload.display_rows, strict=True))
        if query:
            pairs = [
                pair for pair in pairs if any(query in str(value).casefold() for value in pair[1])
            ]
        raw = tuple(pair[0] for pair in pairs)
        display = tuple(pair[1] for pair in pairs)
        source = self._source_payload
        self._payload = ReportPayload(
            source.title,
            source.headers,
            raw,
            display,
            source.currency_columns,
            source.date_columns,
        )
        self.model = RowsTableModel(source.headers, self)
        self.model.set_rows(display)
        self.table.setModel(self.model)
        self.table.resizeColumnsToContents()
        self.state_label.setText(record_count_text(len(display), "record"))

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
        title = self._payload.title

        def operation() -> tuple[Path, str]:
            payload = self._pdf_bytes()
            with self._session_factory() as session:
                preferences = load_print_preferences(session, self._settings)
            return write_temporary_pdf(payload, title), preferences.printer_name

        def preview(result: object) -> None:
            path, printer_name = cast(tuple[Path, str], result)
            open_print_preview(
                path, self, printer_name=printer_name, suggested_filename=f"{title}.pdf"
            )

        self._worker = start_worker(
            operation,
            succeeded=preview,
            failed=lambda error: show_error(self, error),
        )
