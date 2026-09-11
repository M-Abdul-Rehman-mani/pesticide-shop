"""Supplier management, balances, and purchase history."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import cast

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
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
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.purchase import Purchase
from app.models.supplier import Supplier
from app.security.authentication import AuthenticatedUser
from app.services.catalog_service import SupplierService
from app.ui.widgets import (
    RowsTableModel,
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
class SupplierFormData:
    name: str
    company: str | None
    phone: str
    email: str | None
    address: str | None
    tax_number: str | None
    notes: str | None


class SupplierDialog(QDialog):
    def __init__(self, data: SupplierFormData | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Supplier" if data else "Add Supplier")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(data.name if data else "")
        self.company = QLineEdit(data.company or "" if data else "")
        self.phone = QLineEdit(data.phone if data else "")
        self.email = QLineEdit(data.email or "" if data else "")
        self.address = QTextEdit(data.address or "" if data else "")
        self.address.setMaximumHeight(70)
        self.tax = QLineEdit(data.tax_number or "" if data else "")
        self.notes = QTextEdit(data.notes or "" if data else "")
        self.notes.setMaximumHeight(70)
        for label, widget in (
            ("Name", self.name),
            ("Company", self.company),
            ("Phone", self.phone),
            ("Email", self.email),
            ("Address", self.address),
            ("Tax number", self.tax),
            ("Notes", self.notes),
        ):
            form.addRow(label, widget)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> SupplierFormData:
        return SupplierFormData(
            self.name.text(),
            self.company.text().strip() or None,
            self.phone.text(),
            self.email.text().strip() or None,
            self.address.toPlainText().strip() or None,
            self.tax.text().strip() or None,
            self.notes.toPlainText().strip() or None,
        )


class SuppliersScreen(QWidget):
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        actor: AuthenticatedUser,
        currency: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session_factory = session_factory
        self._actor = actor
        self._currency = currency
        self._worker: FunctionWorker | None = None
        self._ids: list[uuid.UUID] = []
        self._data: list[SupplierFormData] = []
        self._active: list[bool] = []
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("Suppliers")
        title.setObjectName("PageTitle")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search supplier")
        self.status_filter = QComboBox()
        self.status_filter.addItems(("Active", "Inactive", "All"))
        add = QPushButton("Add Supplier")
        edit = QPushButton("Edit")
        edit.setProperty("secondary", True)
        history = QPushButton("Purchase History")
        history.setProperty("secondary", True)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.search)
        header.addWidget(self.status_filter)
        header.addWidget(history)
        header.addWidget(edit)
        header.addWidget(add)
        self.model = RowsTableModel(
            ("Name", "Company", "Phone", "Email", "Balance", "Active", "Actions"), self
        )
        self.table = QTableView()
        self.table.setModel(self.model)
        configure_table(self.table, stretch_column=0)
        layout.addLayout(header)
        layout.addWidget(self.table)
        self.state_label = QLabel("Loading suppliers…")
        self.state_label.setObjectName("RecordCount")
        layout.addWidget(self.state_label)
        add.clicked.connect(self._add)
        edit.clicked.connect(self._edit)
        history.clicked.connect(self._history)
        self.search.returnPressed.connect(self.refresh)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda _text: self._search_timer.start())
        self.status_filter.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        query = self.search.text().strip()
        status = self.status_filter.currentText()
        self.state_label.setText("Loading suppliers…")

        def operation() -> tuple[
            list[tuple[object, ...]], list[uuid.UUID], list[SupplierFormData], list[bool]
        ]:
            with self._session_factory() as session:
                statement = select(Supplier)
                if status != "All":
                    statement = statement.where(Supplier.is_active.is_(status == "Active"))
                if query:
                    pattern = f"%{query}%"
                    statement = statement.where(
                        or_(
                            Supplier.name.ilike(pattern),
                            Supplier.company_name.ilike(pattern),
                            Supplier.phone.ilike(pattern),
                            Supplier.email.ilike(pattern),
                            Supplier.address.ilike(pattern),
                            Supplier.tax_number.ilike(pattern),
                        )
                    )
                suppliers = list(session.scalars(statement.order_by(Supplier.name).limit(500)))
                return (
                    [
                        (
                            supplier.name,
                            supplier.company_name or "—",
                            supplier.phone,
                            supplier.email or "—",
                            f"{self._currency} {supplier.balance:,.0f}",
                            "Yes" if supplier.is_active else "No",
                            "",
                        )
                        for supplier in suppliers
                    ],
                    [supplier.id for supplier in suppliers],
                    [
                        SupplierFormData(
                            supplier.name,
                            supplier.company_name,
                            supplier.phone,
                            supplier.email,
                            supplier.address,
                            supplier.tax_number,
                            supplier.notes,
                        )
                        for supplier in suppliers
                    ],
                    [supplier.is_active for supplier in suppliers],
                )

        self._worker = start_worker(
            operation, succeeded=self._display, failed=lambda error: show_error(self, error)
        )

    def _display(self, result: object) -> None:
        rows, self._ids, self._data, self._active = cast(
            tuple[list[tuple[object, ...]], list[uuid.UUID], list[SupplierFormData], list[bool]],
            result,
        )
        self.model.set_rows(rows)
        self.state_label.setText(record_count_text(len(rows), "supplier"))
        populate_row_actions(
            self.table,
            6,
            len(rows),
            (
                ("View", self._view),
                ("Edit", self._edit_row),
                ("Purchase history", self._history_row),
                ("Activate / deactivate", self._toggle_active),
                ("Delete", self._delete_row),
            ),
        )

    def _selected(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def _add(self) -> None:
        dialog = SupplierDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._save(None, dialog.values())

    def _edit(self) -> None:
        row = self._selected()
        if row is None:
            QMessageBox.information(self, "Select supplier", "Select a supplier to edit.")
            return
        dialog = SupplierDialog(self._data[row], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._save(self._ids[row], dialog.values())

    def _edit_row(self, row: int) -> None:
        if row >= len(self._data):
            return
        dialog = SupplierDialog(self._data[row], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._save(self._ids[row], dialog.values())

    def _view(self, row: int) -> None:
        if row >= len(self._data):
            return
        data = self._data[row]
        show_record_details(
            self,
            data.company or data.name,
            (
                ("Contact", data.name),
                ("Phone", data.phone),
                ("Email", data.email),
                ("Address", data.address),
                ("Tax number", data.tax_number),
                ("Notes", data.notes),
            ),
        )

    def _toggle_active(self, row: int) -> None:
        if row >= len(self._ids):
            return
        new_state = not self._active[row]
        action = "restore" if new_state else "remove from active lists"
        if (
            QMessageBox.question(
                self, "Confirm supplier status", f"Do you want to {action} this supplier?"
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        def operation() -> None:
            with self._session_factory.begin() as session:
                SupplierService(session).set_active(
                    self._ids[row], actor=self._actor, is_active=new_state
                )

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: self._saved("Supplier status updated."),
            failed=lambda error: show_error(self, error),
        )

    def _history_row(self, row: int) -> None:
        if row >= len(self._ids):
            return
        self.table.selectRow(row)
        self._history()

    def _save(self, supplier_id: uuid.UUID | None, data: SupplierFormData) -> None:
        def operation() -> None:
            with self._session_factory.begin() as session:
                service = SupplierService(session)
                if supplier_id:
                    service.update(
                        supplier_id,
                        actor=self._actor,
                        name=data.name,
                        company_name=data.company,
                        phone=data.phone,
                        email=data.email,
                        address=data.address,
                        tax_number=data.tax_number,
                        notes=data.notes,
                    )
                else:
                    service.create(
                        actor=self._actor,
                        name=data.name,
                        company_name=data.company,
                        phone=data.phone,
                        email=data.email,
                        address=data.address,
                        tax_number=data.tax_number,
                        notes=data.notes,
                    )

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: self._saved("Supplier saved."),
            failed=lambda error: show_error(self, error),
        )

    def _delete_row(self, row: int) -> None:
        if row >= len(self._ids):
            return
        if (
            QMessageBox.question(
                self,
                "Delete supplier",
                "Delete this supplier permanently?\n\nSuppliers with purchase history or an "
                "outstanding balance cannot be deleted; deactivate them instead so past "
                "purchases stay complete.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        supplier_id = self._ids[row]

        def operation() -> None:
            with self._session_factory.begin() as session:
                SupplierService(session).delete(supplier_id, actor=self._actor)

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: self._saved("Supplier deleted."),
            failed=lambda error: show_error(self, error),
        )

    def _saved(self, message: str) -> None:
        show_success(self, message)
        self.refresh()

    def _history(self) -> None:
        row = self._selected()
        if row is None:
            QMessageBox.information(self, "Select supplier", "Select a supplier first.")
            return
        supplier_id = self._ids[row]

        def operation() -> list[tuple[object, ...]]:
            with self._session_factory() as session:
                purchases = session.scalars(
                    select(Purchase)
                    .where(Purchase.supplier_id == supplier_id)
                    .order_by(Purchase.purchase_date.desc())
                )
                return [
                    (
                        purchase.purchase_number,
                        format_date(purchase.purchase_date),
                        f"{self._currency} {purchase.total:,.0f}",
                        f"{self._currency} {purchase.remaining_amount:,.0f}",
                        purchase.payment_status.value,
                    )
                    for purchase in purchases
                ]

        def display(rows: object) -> None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Supplier purchase history")
            dialog.resize(750, 400)
            layout = QVBoxLayout(dialog)
            model = RowsTableModel(("Purchase", "Date", "Total", "Remaining", "Status"))
            model.set_rows(rows)  # type: ignore[arg-type]
            table = QTableView()
            table.setModel(model)
            layout.addWidget(table)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            dialog.exec()

        self._worker = start_worker(
            operation, succeeded=display, failed=lambda error: show_error(self, error)
        )
