"""Paginated customer management and sales history."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import cast

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import Settings
from app.repositories.customer_repository import CustomerRepository
from app.security.authentication import AuthenticatedUser
from app.services.catalog_service import CustomerService
from app.ui.documents import InvoiceDocumentActions, InvoiceHistoryDialog
from app.ui.widgets import (
    RowsTableModel,
    configure_table,
    populate_row_actions,
    show_error,
    show_record_details,
    show_success,
)
from app.ui.workers import FunctionWorker, start_worker
from app.utils.formatting import format_date, format_money


@dataclass(frozen=True, slots=True)
class CustomerFormData:
    name: str
    phone: str
    email: str | None
    address: str | None
    cnic: str | None
    notes: str | None


class CustomerDialog(QDialog):
    def __init__(self, data: CustomerFormData | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Customer" if data else "Add Customer")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(data.name if data else "")
        self.phone = QLineEdit(data.phone if data else "")
        self.email = QLineEdit(data.email or "" if data else "")
        self.address = QTextEdit(data.address or "" if data else "")
        self.address.setMaximumHeight(70)
        self.cnic = QLineEdit(data.cnic or "" if data else "")
        self.notes = QTextEdit(data.notes or "" if data else "")
        self.notes.setMaximumHeight(70)
        form.addRow("Name", self.name)
        self.phone.setPlaceholderText("Optional")
        form.addRow("Phone", self.phone)
        form.addRow("Email", self.email)
        form.addRow("Address", self.address)
        form.addRow("CNIC (optional)", self.cnic)
        form.addRow("Notes", self.notes)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> CustomerFormData:
        return CustomerFormData(
            name=self.name.text(),
            phone=self.phone.text(),
            email=self.email.text().strip() or None,
            address=self.address.toPlainText().strip() or None,
            cnic=self.cnic.text().strip() or None,
            notes=self.notes.toPlainText().strip() or None,
        )


class CustomersScreen(QWidget):
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
        self._currency = settings.app_currency
        self._documents = InvoiceDocumentActions(self, session_factory, settings)
        self._worker: FunctionWorker | None = None
        self._ids: list[uuid.UUID] = []
        self._data: list[CustomerFormData] = []
        self._page = 1
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("Customers")
        title.setObjectName("PageTitle")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name, phone, email, address, or CNIC")
        add = QPushButton("Add Customer")
        edit = QPushButton("Edit")
        edit.setProperty("secondary", True)
        history = QPushButton("History")
        history.setProperty("secondary", True)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.search)
        header.addWidget(history)
        header.addWidget(edit)
        header.addWidget(add)
        self.model = RowsTableModel(("Name", "Phone", "Email", "CNIC", "Created", "Actions"), self)
        self.table = QTableView()
        self.table.setModel(self.model)
        configure_table(self.table, stretch_column=0)
        pager = QHBoxLayout()
        previous = QPushButton("Previous")
        next_button = QPushButton("Next")
        self.page_label = QLabel("Page 1")
        pager.addStretch()
        pager.addWidget(previous)
        pager.addWidget(self.page_label)
        pager.addWidget(next_button)
        layout.addLayout(header)
        layout.addWidget(self.table, 1)
        layout.addLayout(pager)
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._reset_refresh)
        self.search.textChanged.connect(lambda _text: self._timer.start())
        add.clicked.connect(self._add)
        edit.clicked.connect(self._edit)
        history.clicked.connect(self._history)
        self.table.doubleClicked.connect(lambda _index: self._history())
        previous.clicked.connect(lambda: self._change_page(-1))
        next_button.clicked.connect(lambda: self._change_page(1))
        self.refresh()

    def _reset_refresh(self) -> None:
        self._page = 1
        self.refresh()

    def _change_page(self, delta: int) -> None:
        self._page = max(1, self._page + delta)
        self.refresh()

    def refresh(self) -> None:
        query, page = self.search.text(), self._page
        self.page_label.setText("Loading customers…")

        def operation() -> tuple[
            list[tuple[object, ...]], list[uuid.UUID], list[CustomerFormData], int
        ]:
            with self._session_factory() as session:
                result = CustomerRepository(session).search(query, page, 50)
                return (
                    [
                        (
                            customer.name,
                            customer.phone or "—",
                            customer.email or "—",
                            customer.cnic or "—",
                            format_date(customer.created_at),
                            "",
                        )
                        for customer in result.items
                    ],
                    [customer.id for customer in result.items],
                    [
                        CustomerFormData(
                            customer.name,
                            customer.phone,
                            customer.email,
                            customer.address,
                            customer.cnic,
                            customer.notes,
                        )
                        for customer in result.items
                    ],
                    result.total_pages,
                )

        self._worker = start_worker(
            operation, succeeded=self._display, failed=lambda error: show_error(self, error)
        )

    def _display(self, result: object) -> None:
        rows, self._ids, self._data, pages = cast(
            tuple[
                list[tuple[object, ...]],
                list[uuid.UUID],
                list[CustomerFormData],
                int,
            ],
            result,
        )
        self.model.set_rows(rows)
        populate_row_actions(
            self.table,
            5,
            len(rows),
            (("View", self._view), ("Edit", self._edit_row), ("Delete", self._delete)),
        )
        self.page_label.setText(
            "No customers found." if not rows else f"Page {self._page} of {pages}"
        )

    def _selected(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def _add(self) -> None:
        dialog = CustomerDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._save(None, dialog.values())

    def _edit(self) -> None:
        row = self._selected()
        if row is None:
            QMessageBox.information(self, "Select customer", "Select a customer to edit.")
            return
        dialog = CustomerDialog(self._data[row], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._save(self._ids[row], dialog.values())

    def _edit_row(self, row: int) -> None:
        if row >= len(self._data):
            return
        dialog = CustomerDialog(self._data[row], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._save(self._ids[row], dialog.values())

    def _view(self, row: int) -> None:
        if row >= len(self._data):
            return
        data = self._data[row]
        show_record_details(
            self,
            data.name,
            (
                ("Phone", data.phone),
                ("Email", data.email),
                ("Address", data.address),
                ("CNIC", data.cnic),
                ("Notes", data.notes),
            ),
        )

    def _delete(self, row: int) -> None:
        if row >= len(self._ids):
            return
        if (
            QMessageBox.question(
                self,
                "Delete customer",
                "Delete this customer? Customers with sales history will be kept "
                "for invoice records.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        def operation() -> None:
            with self._session_factory.begin() as session:
                CustomerService(session).delete(self._ids[row], actor=self._actor)

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: self._saved("Customer deleted."),
            failed=lambda error: show_error(self, error),
        )

    def _save(self, customer_id: uuid.UUID | None, data: CustomerFormData) -> None:
        def operation() -> None:
            with self._session_factory.begin() as session:
                service = CustomerService(session)
                if customer_id:
                    service.update(
                        customer_id,
                        actor=self._actor,
                        name=data.name,
                        phone=data.phone,
                        email=data.email,
                        address=data.address,
                        cnic=data.cnic,
                        notes=data.notes,
                    )
                else:
                    service.create(
                        actor=self._actor,
                        name=data.name,
                        phone=data.phone,
                        email=data.email,
                        address=data.address,
                        cnic=data.cnic,
                        notes=data.notes,
                    )

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: self._saved("Customer saved."),
            failed=lambda error: show_error(self, error),
        )

    def _saved(self, message: str) -> None:
        show_success(self, message)
        self.refresh()

    def _history(self) -> None:
        row = self._selected()
        if row is None:
            QMessageBox.information(self, "Select customer", "Select a customer to view history.")
            return
        customer_id = self._ids[row]

        def operation() -> tuple[list[tuple[object, ...]], list[uuid.UUID]]:
            with self._session_factory() as session:
                sales = list(CustomerRepository(session).sales(customer_id))
                return (
                    [
                        (
                            sale.invoice_number,
                            format_date(sale.sale_date),
                            format_money(sale.total, self._currency),
                            format_money(sale.paid_amount, self._currency),
                            format_money(sale.remaining_amount, self._currency),
                            sale.payment_status.value.replace("_", " ").title(),
                        )
                        for sale in sales
                    ],
                    [sale.id for sale in sales],
                )

        def display(result: object) -> None:
            rows, sale_ids = cast(tuple[list[tuple[object, ...]], list[uuid.UUID]], result)
            InvoiceHistoryDialog(
                "Customer purchase history", rows, sale_ids, self._documents, self
            ).exec()

        self._worker = start_worker(
            operation, succeeded=display, failed=lambda error: show_error(self, error)
        )
