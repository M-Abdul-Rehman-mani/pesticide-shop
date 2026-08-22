"""Searchable pesticide batch inventory, expiry visibility, and stock adjustments."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import cast

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
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
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.inventory import StockBatch, StockMovement
from app.models.product import Product
from app.models.supplier import Supplier
from app.security.authentication import AuthenticatedUser
from app.services.stock_inventory_service import StockInventoryService
from app.ui.widgets import PageHeader, RowsTableModel, configure_table, show_error
from app.ui.workers import FunctionWorker, start_worker
from app.utils.formatting import format_date


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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 20)
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
        self.search.setMinimumWidth(290)
        self.filter = QComboBox()
        self.filter.addItems(
            ("All", "In Stock", "Low Stock", "Expiring in 90 Days", "Expired", "Out of Stock")
        )
        refresh, history, adjust = (
            QPushButton("Refresh"),
            QPushButton("Batch History"),
            QPushButton("Adjust Stock"),
        )
        history.setProperty("secondary", True)
        adjust.setProperty("secondary", True)
        history.setEnabled(False)
        adjust.setEnabled(False)
        self._history_button, self._adjust_button = history, adjust
        header.addWidget(QLabel("Search"))
        header.addWidget(self.search)
        header.addWidget(QLabel("Status"))
        header.addWidget(self.filter)
        header.addStretch()
        header.addWidget(history)
        header.addWidget(adjust)
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
            ),
            self,
        )
        self.table = QTableView()
        self.table.setModel(self.model)
        configure_table(self.table, stretch_column=0)
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

        def operation() -> tuple[list[tuple[object, ...]], list[uuid.UUID], list[int], list[int]]:
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
                    statement = statement.where(StockBatch.quantity_available > 0)
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
                        )
                        for b in batches
                    ],
                    [b.id for b in batches],
                    [b.quantity_received for b in batches],
                    [b.quantity_available for b in batches],
                )

        self._worker = start_worker(
            operation, succeeded=self._display, failed=lambda error: show_error(self, error)
        )

    def _display(self, result: object) -> None:
        rows, self._ids, self._received, self._available = cast(
            tuple[list[tuple[object, ...]], list[uuid.UUID], list[int], list[int]], result
        )
        self.model.set_rows(rows)
        self.record_count.setText(f"{len(rows):,} {'batch' if len(rows) == 1 else 'batches'} shown")
        self._selection_changed()

    def _selection_changed(self) -> None:
        selected = self._selected() is not None
        self._history_button.setEnabled(selected)
        self._adjust_button.setEnabled(selected and self._actor is not None)

    def _selected(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else None

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

        def operation() -> None:
            with self._session_factory.begin() as session:
                StockInventoryService(session).adjust(
                    self._ids[row],
                    quantity=quantity.value(),
                    reason=reason.text(),
                    actor=actor,
                )

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: self.refresh(),
            failed=lambda error: show_error(self, error),
        )

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
