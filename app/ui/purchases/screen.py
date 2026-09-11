"""Supplier pesticide purchases with batch, expiry, cartons, and stock creation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import cast

from PySide6.QtCore import QDate, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import Settings
from app.models.enums import PaymentMethod
from app.models.inventory import StockBatch
from app.models.product import Product
from app.models.purchase import Purchase, PurchaseItem
from app.models.supplier import Supplier
from app.security.authentication import AuthenticatedUser
from app.services.dto import CreateStockPurchaseCommand, PurchasedBatchInput
from app.services.payment_service import PaymentService
from app.services.stock_inventory_service import StockInventoryService
from app.services.stock_purchase_service import StockPurchaseService
from app.ui.forms import MoneyEdit, PaymentEditor
from app.ui.widgets import (
    PageHeader,
    RowsTableModel,
    configure_date_edit,
    configure_searchable_combo,
    configure_table,
    populate_enum_combo,
    populate_row_actions,
    record_count_text,
    selected_enum,
    show_error,
    show_record_details,
    show_success,
    wrap_scroll,
)
from app.ui.workers import FunctionWorker, start_worker
from app.utils.formatting import format_date


@dataclass(frozen=True, slots=True)
class PurchaseCartEntry:
    product_id: uuid.UUID
    product: str
    batch_number: str
    quantity: int
    cartons: int
    packs_per_carton: int
    manufacture_date: date
    expiry_date: date
    purchase_price: Decimal
    selling_price: Decimal


@dataclass(frozen=True, slots=True)
class PurchaseBatchSummary:
    id: uuid.UUID
    product: str
    batch_number: str
    received: int
    available: int


@dataclass(frozen=True, slots=True)
class PurchaseHistoryEntry:
    id: uuid.UUID
    number: str
    supplier: str
    date_text: str
    total: Decimal
    paid: Decimal
    remaining: Decimal
    payment_status: str
    status: str
    notes: str | None
    batches: tuple[PurchaseBatchSummary, ...]


class PurchasesScreen(QWidget):
    purchase_completed = Signal(str)

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
        self._cart: list[PurchaseCartEntry] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 10)
        root.addWidget(
            PageHeader("Purchases", "Receive stock or search and manage previous purchases.")
        )
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        entry_page = QWidget()
        layout = QVBoxLayout(entry_page)
        layout.setContentsMargins(8, 8, 8, 8)
        self.tabs.addTab(wrap_scroll(entry_page), "Receive Stock")
        supplier_group, supplier_form = QGroupBox("Supplier"), QFormLayout()
        supplier_group.setLayout(supplier_form)
        self.supplier = QComboBox()
        configure_searchable_combo(self.supplier, "Type to find a supplier")
        supplier_form.addRow("Supplier", self.supplier)
        layout.addWidget(supplier_group)

        entry_group, entry_form = QGroupBox("Batch Details"), QFormLayout()
        entry_group.setLayout(entry_form)
        self.product, self.batch_number = QComboBox(), QLineEdit()
        configure_searchable_combo(self.product, "Type to find a product")
        self.quantity, self.cartons, self.packs = QSpinBox(), QSpinBox(), QSpinBox()
        for field in (self.quantity, self.cartons, self.packs):
            field.setRange(0, 1_000_000)
        self.quantity.setMinimum(1)
        self.manufacture_date = QDateEdit(QDate.currentDate())
        self.expiry_date = QDateEdit(QDate.currentDate().addYears(2))
        configure_date_edit(self.manufacture_date, self.expiry_date)
        self.purchase_price, self.selling_price = MoneyEdit(), MoneyEdit()
        add = QPushButton("Add Batch")
        fields = (
            ("Product", self.product),
            ("Batch number", self.batch_number),
            ("Quantity (packs/units)", self.quantity),
            ("Cartons", self.cartons),
            ("Packs per carton", self.packs),
            ("Manufacture date", self.manufacture_date),
            ("Expiry date", self.expiry_date),
            ("Purchase price / unit", self.purchase_price),
            ("Sale price / unit", self.selling_price),
        )
        for field_label, field_widget in fields:
            entry_form.addRow(field_label, field_widget)
        entry_form.addRow(add)
        layout.addWidget(entry_group)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            (
                "Product",
                "Batch",
                "Qty",
                "Cartons",
                "Packs/Carton",
                "Expiry",
                "Purchase/Unit",
                "Line Total",
                "Action",
            )
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        bottom = QHBoxLayout()
        payments_group, payments_layout = QGroupBox("Payments"), QVBoxLayout()
        payments_group.setLayout(payments_layout)
        self.payments = PaymentEditor()
        payments_layout.addWidget(self.payments)
        totals_group, totals_form = QGroupBox("Totals"), QFormLayout()
        totals_group.setLayout(totals_form)
        self.subtotal, self.total = (
            QLabel(f"{settings.app_currency} 0.00"),
            QLabel(f"{settings.app_currency} 0.00"),
        )
        self.discount, self.tax = MoneyEdit(), MoneyEdit()
        for total_label, total_widget in (
            ("Subtotal", self.subtotal),
            ("Discount", self.discount),
            ("Tax", self.tax),
            ("Total", self.total),
        ):
            totals_form.addRow(total_label, total_widget)
        bottom.addWidget(payments_group, 2)
        bottom.addWidget(totals_group, 1)
        layout.addLayout(bottom)
        action_row = QHBoxLayout()
        action_row.addStretch()
        self.save_button = QPushButton("Complete Purchase && Add Stock")
        action_row.addWidget(self.save_button)
        layout.addLayout(action_row)
        add.clicked.connect(self._add)
        self.save_button.clicked.connect(self.save)
        self.product.currentIndexChanged.connect(self._product_changed)
        self.discount.textChanged.connect(self._calculate)
        self.tax.textChanged.connect(self._calculate)
        self._build_history_tab()
        self._load_choices()

    def _load_choices(self) -> None:
        def operation() -> tuple[
            list[tuple[uuid.UUID, str]], list[tuple[uuid.UUID, str, Decimal, Decimal]]
        ]:
            with self._session_factory() as session:
                suppliers = session.scalars(
                    select(Supplier).where(Supplier.is_active.is_(True)).order_by(Supplier.name)
                )
                products = session.scalars(
                    select(Product)
                    .where(Product.is_active.is_(True))
                    .order_by(Product.manufacturer, Product.name)
                )
                return (
                    [(s.id, s.company_name or s.name) for s in suppliers],
                    [
                        (p.id, p.display_name, p.default_purchase_price, p.default_sale_price)
                        for p in products
                    ],
                )

        self._worker = start_worker(
            operation, succeeded=self._set_choices, failed=lambda error: show_error(self, error)
        )

    def refresh(self) -> None:
        self._load_choices()
        if self.tabs.currentIndex() == 1:
            self._refresh_history()

    def _set_choices(self, result: object) -> None:
        suppliers, products = cast(
            tuple[list[tuple[uuid.UUID, str]], list[tuple[uuid.UUID, str, Decimal, Decimal]]],
            result,
        )
        self.supplier.clear()
        self.product.clear()
        for item_id, label in suppliers:
            self.supplier.addItem(label, item_id)
        for item_id, label, purchase, sale in products:
            self.product.addItem(label, (item_id, purchase, sale))
        self._product_changed()

    def _product_changed(self) -> None:
        if data := self.product.currentData():
            _product_id, purchase, sale = data
            self.purchase_price.setText(f"{purchase:.0f}")
            self.selling_price.setText(f"{sale:.0f}")

    def _add(self) -> None:
        data = self.product.currentData()
        if data is None:
            show_error(self, ValueError("Create a product first."))
            return
        product_id, _purchase, _sale = data
        entry = PurchaseCartEntry(
            product_id,
            self.product.currentText(),
            self.batch_number.text().strip().upper(),
            self.quantity.value(),
            self.cartons.value(),
            self.packs.value(),
            cast(date, self.manufacture_date.date().toPython()),
            cast(date, self.expiry_date.date().toPython()),
            self.purchase_price.decimal_value("Purchase price"),
            self.selling_price.decimal_value("Selling price"),
        )
        self._cart.append(entry)
        row = self.table.rowCount()
        self.table.insertRow(row)
        values = (
            entry.product,
            entry.batch_number,
            entry.quantity,
            entry.cartons,
            entry.packs_per_carton,
            entry.expiry_date.strftime("%d-%b-%Y"),
            f"{entry.purchase_price:,.0f}",
            f"{entry.purchase_price * entry.quantity:,.0f}",
        )
        for column, value in enumerate(values):
            self.table.setItem(row, column, QTableWidgetItem(str(value)))
        remove = QPushButton("Remove")
        remove.setProperty("danger", True)
        remove.clicked.connect(lambda _checked=False, item=entry: self._remove(item))
        self.table.setCellWidget(row, 8, remove)
        self.batch_number.clear()
        self._calculate()

    def _remove(self, entry: PurchaseCartEntry) -> None:
        row = self._cart.index(entry)
        self._cart.pop(row)
        self.table.removeRow(row)
        self._calculate()

    def _calculate(self) -> None:
        try:
            subtotal = sum((e.purchase_price * e.quantity for e in self._cart), Decimal("0.00"))
            total = subtotal - self.discount.decimal_value() + self.tax.decimal_value()
        except Exception:
            return
        self.subtotal.setText(f"{self._settings.app_currency} {subtotal:,.0f}")
        self.total.setText(f"{self._settings.app_currency} {total:,.0f}")

    def save(self) -> None:
        command = CreateStockPurchaseCommand(
            supplier_id=self.supplier.currentData(),
            batches=tuple(
                PurchasedBatchInput(
                    product_id=e.product_id,
                    batch_number=e.batch_number,
                    quantity=e.quantity,
                    purchase_price=e.purchase_price,
                    selling_price=e.selling_price,
                    manufacture_date=e.manufacture_date,
                    expiry_date=e.expiry_date,
                    cartons=e.cartons,
                    packs_per_carton=e.packs_per_carton,
                )
                for e in self._cart
            ),
            payments=self.payments.values(),
            discount=self.discount.decimal_value(),
            tax=self.tax.decimal_value(),
        )
        self.save_button.setEnabled(False)

        def operation() -> str:
            with self._session_factory.begin() as session:
                return StockPurchaseService(session).create(command, self._actor).purchase_number

        self._worker = start_worker(
            operation,
            succeeded=self._saved,
            failed=lambda error: show_error(self, error),
            finished=lambda: self.save_button.setEnabled(True),
        )

    def _saved(self, purchase_number: object) -> None:
        QMessageBox.information(
            self,
            "Purchase completed",
            f"Purchase {purchase_number} and all stock batches were saved.",
        )
        self._cart.clear()
        self.table.setRowCount(0)
        self.payments.clear()
        self.discount.setText("0.00")
        self.tax.setText("0.00")
        self._calculate()
        self.purchase_completed.emit(str(purchase_number))
        self._load_choices()
        self._refresh_history()

    def _build_history_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        controls = QHBoxLayout()
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText(
            "Search purchase, supplier, product, or batch number"
        )
        self.history_search.setClearButtonEnabled(True)
        refresh = QPushButton("Refresh")
        refresh.setProperty("secondary", True)
        controls.addWidget(QLabel("Search"))
        controls.addWidget(self.history_search, 1)
        controls.addWidget(refresh)
        self.history_model = RowsTableModel(
            (
                "Purchase",
                "Date",
                "Supplier",
                "Batches",
                "Total",
                "Paid",
                "Remaining",
                "Payment",
                "Status",
                "Actions",
            ),
            page,
        )
        self.history_table = QTableView()
        self.history_table.setModel(self.history_model)
        configure_table(self.history_table, stretch_column=3, minimum_section_size=74)
        self.history_state = QLabel("Open this tab to load purchases.")
        self.history_state.setObjectName("RecordCount")
        layout.addLayout(controls)
        layout.addWidget(self.history_table, 1)
        layout.addWidget(self.history_state)
        self.tabs.addTab(page, "All Purchases")
        self._history_entries: list[PurchaseHistoryEntry] = []
        self._history_timer = QTimer(self)
        self._history_timer.setSingleShot(True)
        self._history_timer.setInterval(300)
        self._history_timer.timeout.connect(self._refresh_history)
        self.history_search.textChanged.connect(lambda _text: self._history_timer.start())
        self.history_search.returnPressed.connect(self._refresh_history)
        refresh.clicked.connect(self._refresh_history)
        self.tabs.currentChanged.connect(
            lambda index: self._refresh_history() if index == 1 else None
        )

    def _refresh_history(self) -> None:
        query = self.history_search.text().strip()
        self.history_state.setText("Loading purchases…")

        def operation() -> list[PurchaseHistoryEntry]:
            with self._session_factory() as session:
                statement = select(Purchase)
                if query:
                    pattern = f"%{query}%"
                    statement = statement.where(
                        or_(
                            Purchase.purchase_number.ilike(pattern),
                            Purchase.supplier.has(
                                or_(
                                    Supplier.name.ilike(pattern),
                                    Supplier.company_name.ilike(pattern),
                                    Supplier.phone.ilike(pattern),
                                )
                            ),
                            Purchase.items.any(
                                PurchaseItem.product.has(
                                    or_(
                                        Product.name.ilike(pattern),
                                        Product.manufacturer.ilike(pattern),
                                    )
                                )
                            ),
                            Purchase.id.in_(
                                select(StockBatch.purchase_id).where(
                                    StockBatch.batch_number.ilike(pattern)
                                )
                            ),
                        )
                    )
                purchases = list(
                    session.scalars(statement.order_by(Purchase.purchase_date.desc()).limit(500))
                )
                purchase_ids = [purchase.id for purchase in purchases]
                batches = (
                    list(
                        session.scalars(
                            select(StockBatch)
                            .where(StockBatch.purchase_id.in_(purchase_ids))
                            .order_by(StockBatch.batch_number)
                        )
                    )
                    if purchase_ids
                    else []
                )
                by_purchase: dict[uuid.UUID, list[PurchaseBatchSummary]] = {}
                for batch in batches:
                    by_purchase.setdefault(batch.purchase_id, []).append(
                        PurchaseBatchSummary(
                            batch.id,
                            batch.product.display_name,
                            batch.batch_number,
                            batch.quantity_received,
                            batch.quantity_available,
                        )
                    )
                return [
                    PurchaseHistoryEntry(
                        purchase.id,
                        purchase.purchase_number,
                        purchase.supplier.company_name or purchase.supplier.name,
                        format_date(purchase.purchase_date),
                        purchase.total,
                        purchase.paid_amount,
                        purchase.remaining_amount,
                        purchase.payment_status.value,
                        purchase.status.value,
                        purchase.notes,
                        tuple(by_purchase.get(purchase.id, [])),
                    )
                    for purchase in purchases
                ]

        self._worker = start_worker(
            operation,
            succeeded=self._display_history,
            failed=self._history_failed,
        )

    def _history_failed(self, error: Exception) -> None:
        self.history_state.setText("Unable to load purchases")
        show_error(self, error)

    def _display_history(self, result: object) -> None:
        self._history_entries = cast(list[PurchaseHistoryEntry], result)
        rows = [
            (
                entry.number,
                entry.date_text,
                entry.supplier,
                ", ".join(f"{batch.product} [{batch.batch_number}]" for batch in entry.batches),
                f"{self._settings.app_currency} {entry.total:,.0f}",
                f"{self._settings.app_currency} {entry.paid:,.0f}",
                f"{self._settings.app_currency} {entry.remaining:,.0f}",
                entry.payment_status,
                entry.status,
                "",
            )
            for entry in self._history_entries
        ]
        self.history_model.set_rows(rows)
        populate_row_actions(
            self.history_table,
            9,
            len(rows),
            (
                ("View details", self._view_purchase),
                ("Pay supplier", self._pay_purchase),
                ("Correct stock", self._correct_purchase_stock),
                ("Cancel purchase", self._cancel_purchase),
            ),
        )
        self.history_state.setText(record_count_text(len(rows), "purchase"))

    def _view_purchase(self, row: int) -> None:
        if row >= len(self._history_entries):
            return
        entry = self._history_entries[row]
        batches = "\n".join(
            f"{batch.product} | {batch.batch_number} | "
            f"received {batch.received}, available {batch.available}"
            for batch in entry.batches
        )
        show_record_details(
            self,
            f"Purchase {entry.number}",
            (
                ("Date", entry.date_text),
                ("Supplier", entry.supplier),
                ("Batches", batches),
                ("Total", f"{self._settings.app_currency} {entry.total:,.0f}"),
                ("Paid", f"{self._settings.app_currency} {entry.paid:,.0f}"),
                ("Remaining", f"{self._settings.app_currency} {entry.remaining:,.0f}"),
                ("Payment", entry.payment_status),
                ("Status", entry.status),
                ("Notes", entry.notes),
            ),
        )

    def _pay_purchase(self, row: int) -> None:
        if row >= len(self._history_entries):
            return
        entry = self._history_entries[row]
        if entry.remaining <= 0 or entry.status != "COMPLETED":
            QMessageBox.information(self, "Nothing due", "This purchase has no payable balance.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Pay supplier — {entry.number}")
        form = QFormLayout(dialog)
        method = QComboBox()
        populate_enum_combo(method, PaymentMethod)
        amount = MoneyEdit(f"{entry.remaining:.0f}")
        reference = QLineEdit()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow("Outstanding", QLabel(f"{self._settings.app_currency} {entry.remaining:,.0f}"))
        form.addRow("Method", method)
        form.addRow("Amount", amount)
        form.addRow("Reference", reference)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected_method = selected_enum(method, PaymentMethod)
        payment_amount = amount.decimal_value("Payment")
        payment_reference = reference.text()

        def operation() -> None:
            with self._session_factory.begin() as session:
                PaymentService(session).pay_supplier(
                    entry.id,
                    actor=self._actor,
                    method=selected_method,
                    amount=payment_amount,
                    reference=payment_reference,
                )

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: self._operation_succeeded("Supplier payment recorded."),
            failed=lambda error: show_error(self, error),
        )

    def _correct_purchase_stock(self, row: int) -> None:
        if row >= len(self._history_entries):
            return
        entry = self._history_entries[row]
        if entry.status != "COMPLETED":
            QMessageBox.information(
                self, "Purchase closed", "Cancelled purchases cannot be corrected."
            )
            return
        if not entry.batches:
            QMessageBox.information(self, "No batches", "No stock batches belong to this purchase.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Correct stock — {entry.number}")
        form = QFormLayout(dialog)
        batch_choice = QComboBox()
        for batch in entry.batches:
            batch_choice.addItem(
                f"{batch.product} | {batch.batch_number} | available {batch.available}", batch
            )
        quantity = QSpinBox()
        reason = QLineEdit()
        reason.setPlaceholderText("Required correction reason")

        def selected() -> None:
            batch = cast(PurchaseBatchSummary, batch_choice.currentData())
            quantity.setRange(0, batch.received)
            quantity.setValue(batch.available)

        batch_choice.currentIndexChanged.connect(selected)
        selected()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow("Batch", batch_choice)
        form.addRow("Available quantity", quantity)
        form.addRow("Reason", reason)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        batch = cast(PurchaseBatchSummary, batch_choice.currentData())
        corrected_quantity = quantity.value()
        correction_reason = reason.text()

        def operation() -> None:
            with self._session_factory.begin() as session:
                StockInventoryService(session).adjust(
                    batch.id,
                    quantity=corrected_quantity,
                    reason=correction_reason,
                    actor=self._actor,
                )

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: self._operation_succeeded("Stock correction saved."),
            failed=lambda error: show_error(self, error),
        )

    def _cancel_purchase(self, row: int) -> None:
        if row >= len(self._history_entries):
            return
        entry = self._history_entries[row]
        if entry.status != "COMPLETED":
            QMessageBox.information(self, "Already cancelled", "This purchase is already closed.")
            return
        if (
            QMessageBox.question(
                self,
                "Cancel purchase",
                "Cancel this purchase and remove its untouched stock? This cannot be undone.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        def operation() -> None:
            with self._session_factory.begin() as session:
                StockPurchaseService(session).cancel(entry.id, self._actor)

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: self._operation_succeeded("Purchase cancelled."),
            failed=lambda error: show_error(self, error),
        )

    def _operation_succeeded(self, message: str) -> None:
        show_success(self, message)
        self._refresh_history()
        self.purchase_completed.emit(message)
