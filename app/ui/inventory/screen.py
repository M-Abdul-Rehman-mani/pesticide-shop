"""Paginated inventory search with complete movement history."""

from __future__ import annotations

import uuid
from typing import cast

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.orm import Session, sessionmaker

from app.models.enums import PhoneStatus
from app.repositories.inventory_repository import (
    IMEILookupDetails,
    InventoryFilters,
    InventoryRepository,
)
from app.ui.widgets import RowsTableModel, show_error
from app.ui.workers import FunctionWorker, start_worker
from app.utils.formatting import format_date


class InventoryHistoryDialog(QDialog):
    def __init__(self, imei: str, rows: list[tuple[object, ...]], parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Inventory history — {imei}")
        self.resize(850, 450)
        layout = QVBoxLayout(self)
        title = QLabel(f"Complete history for IMEI {imei}")
        title.setObjectName("PageTitle")
        model = RowsTableModel(
            ("Date", "Type", "Previous", "New", "Reference", "Performed By", "Notes"), self
        )
        model.set_rows(rows)
        table = QTableView()
        table.setModel(model)
        table.horizontalHeader().setStretchLastSection(True)
        table.setSortingEnabled(False)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(title)
        layout.addWidget(table)
        layout.addWidget(buttons)


class InventoryScreen(QWidget):
    HEADERS = (
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
        "Purchase Date",
        "Warranty",
    )

    def __init__(
        self, session_factory: sessionmaker[Session], currency: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._session_factory = session_factory
        self._currency = currency
        self._worker: FunctionWorker | None = None
        self._page = 1
        self._phone_ids: list[uuid.UUID] = []
        self._imeis: list[str] = []
        layout = QVBoxLayout(self)
        title_row = QHBoxLayout()
        title = QLabel("Inventory")
        title.setObjectName("PageTitle")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search IMEI, model, or brand")
        self.status = QComboBox()
        self.status.addItem("All statuses", None)
        for status in PhoneStatus:
            self.status.addItem(status.value.replace("_", " ").title(), status)
        self.page_size = QComboBox()
        for size in (20, 50, 100):
            self.page_size.addItem(str(size), size)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(self.search, 1)
        title_row.addWidget(self.status)
        title_row.addWidget(self.page_size)
        title_row.addWidget(refresh)
        self.model = RowsTableModel(self.HEADERS, self)
        self.imei_details = QLabel("")
        self.imei_details.setWordWrap(True)
        self.imei_details.setStyleSheet(
            "background: #e8f1fb; color: #17324d; border-radius: 6px; padding: 9px;"
        )
        self.imei_details.hide()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self._show_history)
        pager = QHBoxLayout()
        self.previous = QPushButton("Previous")
        self.previous.setProperty("secondary", True)
        self.next = QPushButton("Next")
        self.next.setProperty("secondary", True)
        self.page_label = QLabel("Page 1")
        self.previous.clicked.connect(self._previous_page)
        self.next.clicked.connect(self._next_page)
        pager.addStretch()
        pager.addWidget(self.previous)
        pager.addWidget(self.page_label)
        pager.addWidget(self.next)
        layout.addLayout(title_row)
        layout.addWidget(self.imei_details)
        layout.addWidget(self.table, 1)
        layout.addLayout(pager)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._reset_and_refresh)
        self.search.textChanged.connect(lambda _value: self._search_timer.start())
        self.status.currentIndexChanged.connect(self._reset_and_refresh)
        self.page_size.currentIndexChanged.connect(self._reset_and_refresh)

    def showEvent(self, event: object) -> None:
        super().showEvent(event)  # type: ignore[arg-type]
        if not self._phone_ids:
            self.refresh()

    def _reset_and_refresh(self) -> None:
        self._page = 1
        self.refresh()

    def _previous_page(self) -> None:
        if self._page > 1:
            self._page -= 1
            self.refresh()

    def _next_page(self) -> None:
        if self.next.isEnabled():
            self._page += 1
            self.refresh()

    def refresh(self) -> None:
        filters = InventoryFilters(search=self.search.text(), status=self.status.currentData())
        page = self._page
        page_size = int(self.page_size.currentData())

        def operation() -> tuple[
            list[tuple[object, ...]],
            list[uuid.UUID],
            list[str],
            int,
            IMEILookupDetails | None,
        ]:
            with self._session_factory() as session:
                result = InventoryRepository(session).search(filters, page, page_size)
                rows: list[tuple[object, ...]] = [
                    (
                        phone.imei_1,
                        phone.imei_2 or "—",
                        phone.product.brand,
                        phone.product.model,
                        phone.product.storage,
                        phone.product.color,
                        f"{self._currency} {phone.purchase_price:,.2f}",
                        f"{self._currency} {phone.selling_price:,.2f}",
                        phone.status.value,
                        phone.supplier.name,
                        format_date(phone.purchase.purchase_date),
                        format_date(phone.warranty_end),
                    )
                    for phone in result.items
                ]
                details = None
                if len(filters.search.strip()) == 15 and filters.search.strip().isdigit():
                    details = InventoryRepository(session).lookup_details(filters.search.strip())
                return (
                    rows,
                    [phone.id for phone in result.items],
                    [phone.imei_1 for phone in result.items],
                    result.total_pages,
                    details,
                )

        self._worker = start_worker(
            operation,
            succeeded=self._display,
            failed=lambda error: show_error(self, error),
        )

    def _display(self, result: object) -> None:
        rows, self._phone_ids, self._imeis, total_pages, details = cast(
            tuple[
                list[tuple[object, ...]],
                list[uuid.UUID],
                list[str],
                int,
                IMEILookupDetails | None,
            ],
            result,
        )
        self.model.set_rows(rows)
        self.page_label.setText(f"Page {self._page} of {total_pages}")
        self.previous.setEnabled(self._page > 1)
        self.next.setEnabled(self._page < total_pages)
        self.table.resizeColumnsToContents()
        if details:
            sale_context = (
                f"Customer: {details.customer or 'Walk-in'}  •  Invoice: {details.invoice}  •  "
                f"Sale Date: {format_date(details.sale_date)}"
                if details.invoice
                else "No sale recorded"
            )
            self.imei_details.setText(
                f"{details.product}  •  IMEI: {details.imei}  •  "
                f"Status: {details.status.value}\n{sale_context}"
            )
            self.imei_details.show()
        else:
            self.imei_details.hide()

    def _show_history(self, index: object) -> None:
        row = index.row()  # type: ignore[attr-defined]
        phone_id = self._phone_ids[row]
        imei = self._imeis[row]

        def operation() -> list[tuple[object, ...]]:
            with self._session_factory() as session:
                history = InventoryRepository(session).history(phone_id)
                return [
                    (
                        transaction.created_at.strftime("%d-%b-%Y %H:%M"),
                        transaction.transaction_type.value,
                        transaction.previous_status.value if transaction.previous_status else "—",
                        transaction.new_status.value,
                        transaction.reference_type,
                        transaction.performer.full_name,
                        transaction.notes or "",
                    )
                    for transaction in history
                ]

        self._worker = start_worker(
            operation,
            succeeded=lambda rows: InventoryHistoryDialog(imei, rows, self).exec(),
            failed=lambda error: show_error(self, error),
        )
