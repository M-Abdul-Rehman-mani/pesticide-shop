"""Searchable pesticide batch inventory, expiry visibility, and stock adjustments."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import cast

from PySide6.QtCore import QDate, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.inventory import StockBatch, StockMovement
from app.models.product import Product
from app.models.supplier import Supplier
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, has_permission
from app.services.stock_inventory_service import StockInventoryService
from app.ui.forms import MoneyEdit
from app.ui.widgets import (
    PageHeader,
    RowsTableModel,
    configure_date_edit,
    configure_table,
    populate_row_actions,
    record_count_text,
    show_error,
    show_record_details,
    show_success,
)
from app.ui.workers import FunctionWorker, start_worker
from app.utils.formatting import format_date


@dataclass(frozen=True, slots=True)
class BatchFormData:
    """The batch fields an operator may correct after receipt."""

    batch_number: str
    manufacture_date: date | None
    expiry_date: date | None
    cartons: int
    packs_per_carton: int
    purchase_price: Decimal
    selling_price: Decimal
    location: str
    notes: str


class BatchDialog(QDialog):
    """Edit a batch's identity, dates, packing, and prices.

    Quantities are absent on purpose: they change only through Adjust Stock, which
    writes to the movement ledger.
    """

    def __init__(self, data: BatchFormData, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Batch")
        self.setMinimumWidth(430)
        form = QFormLayout(self)
        self.batch_number = QLineEdit(data.batch_number)
        self.manufacture_date = QDateEdit()
        self.expiry_date = QDateEdit()
        configure_date_edit(self.manufacture_date, self.expiry_date)
        self.manufacture_known = QCheckBox("Manufacture date known")
        self.expiry_known = QCheckBox("Expiry date known")
        self._bind_date(self.manufacture_known, self.manufacture_date, data.manufacture_date)
        self._bind_date(self.expiry_known, self.expiry_date, data.expiry_date)
        self.cartons, self.packs_per_carton = QSpinBox(), QSpinBox()
        for counter, value in (
            (self.cartons, data.cartons),
            (self.packs_per_carton, data.packs_per_carton),
        ):
            counter.setRange(0, 1_000_000)
            counter.setValue(value)
        self.purchase_price = MoneyEdit(f"{data.purchase_price:.2f}")
        self.selling_price = MoneyEdit(f"{data.selling_price:.2f}")
        self.location = QLineEdit(data.location)
        self.location.setPlaceholderText("Shelf or store location")
        self.notes = QTextEdit(data.notes)
        self.notes.setMaximumHeight(70)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        form.addRow("Batch number *", self.batch_number)
        form.addRow("", self.manufacture_known)
        form.addRow("Manufacture date", self.manufacture_date)
        form.addRow("", self.expiry_known)
        form.addRow("Expiry date", self.expiry_date)
        form.addRow("Cartons", self.cartons)
        form.addRow("Packs per carton", self.packs_per_carton)
        form.addRow("Purchase price", self.purchase_price)
        form.addRow("Sale price", self.selling_price)
        form.addRow("Location", self.location)
        form.addRow("Notes", self.notes)
        form.addRow(buttons)

    @staticmethod
    def _bind_date(toggle: QCheckBox, editor: QDateEdit, value: date | None) -> None:
        toggle.setChecked(value is not None)
        editor.setDate(QDate(value.year, value.month, value.day) if value else QDate.currentDate())
        editor.setEnabled(value is not None)
        toggle.toggled.connect(editor.setEnabled)

    def _accept_if_valid(self) -> None:
        if not self.batch_number.text().strip():
            QMessageBox.information(self, "Batch number required", "Enter the batch number.")
            self.batch_number.setFocus()
            return
        self.accept()

    def values(self) -> BatchFormData:
        return BatchFormData(
            batch_number=self.batch_number.text().strip(),
            manufacture_date=(
                cast(date, self.manufacture_date.date().toPython())
                if self.manufacture_known.isChecked()
                else None
            ),
            expiry_date=(
                cast(date, self.expiry_date.date().toPython())
                if self.expiry_known.isChecked()
                else None
            ),
            cartons=self.cartons.value(),
            packs_per_carton=self.packs_per_carton.value(),
            purchase_price=self.purchase_price.decimal_value("Purchase price"),
            selling_price=self.selling_price.decimal_value("Sale price"),
            location=self.location.text().strip(),
            notes=self.notes.toPlainText().strip(),
        )


class InventoryScreen(QWidget):
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        currency: str,
        actor: AuthenticatedUser | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session_factory, self._currency, self._actor = session_factory, currency, actor
        self._worker: FunctionWorker | None = None
        self._ids: list[uuid.UUID] = []
        self._received: list[int] = []
        self._available: list[int] = []
        self._batch_data: list[BatchFormData] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 10)
        layout.setSpacing(14)
        layout.addWidget(
            PageHeader(
                "Batch inventory",
                "Monitor sellable stock, expiry risk, purchase cost, and batch movements.",
            )
        )
        filter_bar, header = QFrame(), QHBoxLayout()
        filter_bar.setObjectName("FilterBar")
        filter_bar.setLayout(header)
        header.setContentsMargins(14, 10, 14, 10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Product, batch, ingredient, supplier")
        self.search.setProperty("search", True)
        self.search.setClearButtonEnabled(True)
        self.filter = QComboBox()
        self.filter.addItems(
            (
                "All",
                "In Stock",
                "Low Stock",
                "Expiring in 90 Days",
                "Expired",
                "Out of Stock",
                "Inactive",
            )
        )
        refresh, history, adjust, edit = (
            QPushButton("Refresh"),
            QPushButton("Batch History"),
            QPushButton("Adjust Stock"),
            QPushButton("Edit Batch"),
        )
        for secondary in (history, adjust, edit):
            secondary.setProperty("secondary", True)
            secondary.setEnabled(False)
        self._history_button, self._adjust_button, self._edit_button = history, adjust, edit
        header.addWidget(QLabel("Search"))
        header.addWidget(self.search)
        header.addWidget(QLabel("Status"))
        header.addWidget(self.filter)
        header.addStretch()
        header.addWidget(history)
        header.addWidget(adjust)
        header.addWidget(edit)
        header.addWidget(refresh)
        self.model = RowsTableModel(
            (
                "Product",
                "Manufacturer",
                "Batch",
                "Available",
                "Received",
                "Unit",
                "Expiry",
                "Expiry Status",
                "Supplier",
                "Purchase",
                "Sale",
                "Stock Value",
                "Actions",
            ),
            self,
        )
        self.table = QTableView()
        self.table.setModel(self.model)
        # Thirteen columns only fit when the narrow numeric ones may shrink
        # below the usual minimum width.
        configure_table(self.table, stretch_column=0, minimum_section_size=62)
        layout.addWidget(filter_bar)
        layout.addWidget(self.table, 1)
        self.record_count = QLabel("Loading inventory…")
        self.record_count.setObjectName("RecordCount")
        layout.addWidget(self.record_count)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(350)
        self._search_timer.timeout.connect(self.refresh)
        refresh.clicked.connect(self.refresh)
        self.search.textChanged.connect(lambda _text: self._search_timer.start())
        self.search.returnPressed.connect(lambda: self._search_timer.stop())
        self.search.returnPressed.connect(self.refresh)
        self.filter.currentIndexChanged.connect(self.refresh)
        history.clicked.connect(self._history)
        adjust.clicked.connect(self._adjust)
        edit.clicked.connect(self._edit)
        self.table.doubleClicked.connect(lambda _index: self._history())
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.refresh()

    @staticmethod
    def _expiry_status(expiry: date | None) -> str:
        if expiry is None:
            return "Not set"
        days = (expiry - date.today()).days
        if days < 0:
            return f"EXPIRED ({abs(days)} days ago)"
        if days <= 90:
            return f"Expiring in {days} days"
        return "Valid"

    def refresh(self) -> None:
        query, selected_filter = self.search.text().strip(), self.filter.currentText()

        def operation() -> tuple[
            list[tuple[object, ...]],
            list[uuid.UUID],
            list[int],
            list[int],
            list[BatchFormData],
        ]:
            with self._session_factory() as session:
                statement = select(StockBatch).join(StockBatch.product).join(StockBatch.supplier)
                if query:
                    pattern = f"%{query}%"
                    statement = statement.where(
                        or_(
                            Product.name.ilike(pattern),
                            Product.manufacturer.ilike(pattern),
                            Product.active_ingredient.ilike(pattern),
                            StockBatch.batch_number.ilike(pattern),
                            Supplier.name.ilike(pattern),
                            Supplier.company_name.ilike(pattern),
                        )
                    )
                today = date.today()
                if selected_filter == "In Stock":
                    statement = statement.where(
                        StockBatch.quantity_available > 0, StockBatch.is_active.is_(True)
                    )
                elif selected_filter == "Out of Stock":
                    statement = statement.where(StockBatch.quantity_available == 0)
                elif selected_filter == "Expired":
                    statement = statement.where(StockBatch.expiry_date < today)
                elif selected_filter == "Expiring in 90 Days":
                    statement = statement.where(
                        StockBatch.expiry_date >= today,
                        StockBatch.expiry_date <= today + timedelta(days=90),
                    )
                elif selected_filter == "Low Stock":
                    statement = statement.where(
                        StockBatch.quantity_available <= Product.minimum_stock
                    )
                elif selected_filter == "Inactive":
                    statement = statement.where(StockBatch.is_active.is_(False))
                batches = list(
                    session.scalars(
                        statement.order_by(
                            StockBatch.expiry_date.asc().nullslast(), Product.name
                        ).limit(1000)
                    )
                )
                return (
                    [
                        (
                            b.product.display_name,
                            b.product.manufacturer,
                            b.batch_number,
                            b.quantity_available,
                            b.quantity_received,
                            b.product.unit,
                            format_date(b.expiry_date),
                            self._expiry_status(b.expiry_date),
                            b.supplier.company_name or b.supplier.name,
                            f"{self._currency} {b.purchase_price:,.2f}",
                            f"{self._currency} {b.selling_price:,.2f}",
                            f"{self._currency} {b.stock_value:,.2f}",
                            "",
                        )
                        for b in batches
                    ],
                    [b.id for b in batches],
                    [b.quantity_received for b in batches],
                    [b.quantity_available for b in batches],
                    [
                        BatchFormData(
                            batch_number=b.batch_number,
                            manufacture_date=b.manufacture_date,
                            expiry_date=b.expiry_date,
                            cartons=b.cartons,
                            packs_per_carton=b.packs_per_carton,
                            purchase_price=b.purchase_price,
                            selling_price=b.selling_price,
                            location=b.location or "",
                            notes=b.notes or "",
                        )
                        for b in batches
                    ],
                )

        self._worker = start_worker(
            operation, succeeded=self._display, failed=lambda error: show_error(self, error)
        )

    def _display(self, result: object) -> None:
        rows, self._ids, self._received, self._available, self._batch_data = cast(
            tuple[
                list[tuple[object, ...]],
                list[uuid.UUID],
                list[int],
                list[int],
                list[BatchFormData],
            ],
            result,
        )
        self.model.set_rows(rows)
        actions = [
            ("View", self._view_row),
            ("Movement history", self._history_row),
        ]
        if self._can_manage():
            actions.extend(
                (
                    ("Adjust stock", self._adjust_row),
                    ("Edit batch", self._edit_row),
                    ("Delete batch", self._delete_row),
                )
            )
        populate_row_actions(
            self.table,
            12,
            len(rows),
            actions,
        )
        self.record_count.setText(record_count_text(len(rows), "batch", "batches"))
        self._selection_changed()

    def _can_manage(self) -> bool:
        return self._actor is not None and has_permission(
            self._actor.role, Permission.MANAGE_INVENTORY
        )

    def _selection_changed(self) -> None:
        selected = self._selected() is not None
        manage = selected and self._can_manage()
        self._history_button.setEnabled(selected)
        self._adjust_button.setEnabled(manage)
        self._edit_button.setEnabled(manage)

    def _selected(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def _select_row(self, row: int) -> bool:
        if row >= len(self._ids):
            return False
        self.table.selectRow(row)
        return True

    def _view_row(self, row: int) -> None:
        if not self._select_row(row):
            return
        values = self.model.row(row)
        show_record_details(
            self,
            f"Batch {values[2]}",
            tuple(
                zip(
                    (
                        "Product",
                        "Manufacturer",
                        "Batch",
                        "Available",
                        "Received",
                        "Unit",
                        "Expiry",
                        "Expiry status",
                        "Supplier",
                        "Purchase price",
                        "Sale price",
                        "Stock value",
                    ),
                    values[:12],
                    strict=True,
                )
            ),
        )

    def _history_row(self, row: int) -> None:
        if self._select_row(row):
            self._history()

    def _adjust_row(self, row: int) -> None:
        if self._can_manage() and self._select_row(row):
            self._adjust()

    def _edit_row(self, row: int) -> None:
        if self._can_manage() and self._select_row(row):
            self._edit()

    def _delete_row(self, row: int) -> None:
        if self._can_manage() and self._select_row(row):
            self._delete()

    def _edit(self) -> None:
        """Correct a batch's details without touching its quantities."""

        row = self._selected()
        if row is None or row >= len(self._batch_data):
            QMessageBox.information(self, "Select batch", "Select a batch first.")
            return
        if self._actor is None:
            QMessageBox.information(self, "Not available", "Sign in with inventory permission.")
            return
        actor = self._actor
        batch_id = self._ids[row]
        dialog = BatchDialog(self._batch_data[row], self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        data = dialog.values()

        def operation() -> None:
            with self._session_factory.begin() as session:
                StockInventoryService(session).update_batch(
                    batch_id,
                    actor=actor,
                    batch_number=data.batch_number,
                    manufacture_date=data.manufacture_date,
                    expiry_date=data.expiry_date,
                    cartons=data.cartons,
                    packs_per_carton=data.packs_per_carton,
                    purchase_price=data.purchase_price,
                    selling_price=data.selling_price,
                    location=data.location or None,
                    notes=data.notes or None,
                )

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: self._changed("Batch details saved."),
            failed=lambda error: show_error(self, error),
        )

    def _delete(self) -> None:
        """Write a batch's remaining stock off and retire it.

        The batch row itself survives because invoices reference it and the movement
        ledger is append-only, so the confirmation says exactly what will happen.
        """

        row = self._selected()
        if row is None or row >= len(self._ids):
            QMessageBox.information(self, "Select batch", "Select a batch first.")
            return
        if self._actor is None:
            QMessageBox.information(self, "Not available", "Sign in with inventory permission.")
            return
        actor = self._actor
        batch_id = self._ids[row]
        remaining = self._available[row]
        confirmation = QDialog(self)
        confirmation.setWindowTitle("Delete Batch")
        form = QFormLayout(confirmation)
        explanation = QLabel(
            f"This removes the batch from sales and stock figures and writes off its "
            f"remaining {remaining:,} unit{'' if remaining == 1 else 's'}.\n\n"
            "The batch record itself is kept because invoices and the movement ledger "
            "refer to it."
        )
        explanation.setWordWrap(True)
        reason = QLineEdit()
        reason.setPlaceholderText("Damaged, expired, wrongly entered, returned to supplier…")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Delete batch")
        buttons.accepted.connect(confirmation.accept)
        buttons.rejected.connect(confirmation.reject)
        form.addRow(explanation)
        form.addRow("Reason *", reason)
        form.addRow(buttons)
        if confirmation.exec() != QDialog.DialogCode.Accepted:
            return
        removal_reason = reason.text()

        def operation() -> None:
            with self._session_factory.begin() as session:
                StockInventoryService(session).delete_batch(
                    batch_id, actor=actor, reason=removal_reason
                )

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: self._changed("Batch removed from stock."),
            failed=lambda error: show_error(self, error),
        )

    def _changed(self, message: str) -> None:
        show_success(self, message)
        self.refresh()

    def _adjust(self) -> None:
        row = self._selected()
        if row is None:
            QMessageBox.information(self, "Select batch", "Select a batch first.")
            return
        if self._actor is None:
            QMessageBox.information(self, "Not available", "Sign in with inventory permission.")
            return
        actor = self._actor
        dialog = QDialog(self)
        dialog.setWindowTitle("Adjust Available Stock")
        form = QFormLayout(dialog)
        quantity = QSpinBox()
        quantity.setRange(0, self._received[row])
        quantity.setValue(self._available[row])
        reason = QLineEdit()
        reason.setPlaceholderText("Count correction, spillage, expiry, etc.")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow("Available quantity", quantity)
        form.addRow("Reason", reason)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        corrected_quantity = quantity.value()
        correction_reason = reason.text()

        def operation() -> None:
            with self._session_factory.begin() as session:
                StockInventoryService(session).adjust(
                    self._ids[row],
                    quantity=corrected_quantity,
                    reason=correction_reason,
                    actor=actor,
                )

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: self._adjusted(),
            failed=lambda error: show_error(self, error),
        )

    def _adjusted(self) -> None:
        show_success(self, "Stock adjustment saved.")
        self.refresh()

    def _history(self) -> None:
        row = self._selected()
        if row is None:
            QMessageBox.information(self, "Select batch", "Select a batch first.")
            return
        batch_id = self._ids[row]

        def operation() -> list[tuple[object, ...]]:
            with self._session_factory() as session:
                movements = session.scalars(
                    select(StockMovement)
                    .where(StockMovement.batch_id == batch_id)
                    .order_by(StockMovement.created_at.desc())
                )
                return [
                    (
                        format_date(m.created_at),
                        m.transaction_type.value,
                        m.quantity_change,
                        m.balance_after,
                        m.reference_type,
                        m.notes or "—",
                    )
                    for m in movements
                ]

        def display(rows: object) -> None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Stock Movement History")
            dialog.resize(850, 420)
            layout = QVBoxLayout(dialog)
            model = RowsTableModel(
                ("Date", "Type", "Change", "Balance", "Reference", "Notes"), dialog
            )
            model.set_rows(rows)  # type: ignore[arg-type]
            table = QTableView()
            table.setModel(model)
            configure_table(table, stretch_column=5)
            layout.addWidget(table)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            dialog.exec()

        self._worker = start_worker(
            operation, succeeded=display, failed=lambda error: show_error(self, error)
        )
