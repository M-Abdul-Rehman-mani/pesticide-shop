"""Dealer accounts, credit balances, contact details, and sales history."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
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

from app.models.dealer import Dealer
from app.models.sale import Sale
from app.security.authentication import AuthenticatedUser
from app.services.catalog_service import DealerService
from app.ui.forms import MoneyEdit
from app.ui.widgets import (
    RowsTableModel,
    populate_row_actions,
    show_error,
    show_record_details,
    show_success,
)
from app.ui.workers import FunctionWorker, start_worker
from app.utils.formatting import format_date


@dataclass(frozen=True, slots=True)
class DealerFormData:
    name: str
    business_name: str | None
    phone: str
    email: str | None
    address: str | None
    cnic: str | None
    tax_number: str | None
    territory: str | None
    credit_limit: Decimal
    notes: str | None


class DealerDialog(QDialog):
    def __init__(self, data: DealerFormData | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Dealer" if data else "Add Dealer")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(data.name if data else "")
        self.business = QLineEdit(data.business_name or "" if data else "")
        self.phone = QLineEdit(data.phone if data else "")
        self.email = QLineEdit(data.email or "" if data else "")
        self.address = QTextEdit(data.address or "" if data else "")
        self.cnic = QLineEdit(data.cnic or "" if data else "")
        self.tax = QLineEdit(data.tax_number or "" if data else "")
        self.territory = QLineEdit(data.territory or "" if data else "")
        self.credit = MoneyEdit(f"{data.credit_limit:.2f}" if data else "0.00")
        self.notes = QTextEdit(data.notes or "" if data else "")
        self.address.setMaximumHeight(60)
        self.notes.setMaximumHeight(60)
        for label, widget in (
            ("Contact name", self.name),
            ("Business / shop name", self.business),
            ("Phone", self.phone),
            ("Email", self.email),
            ("Address", self.address),
            ("CNIC / NIC", self.cnic),
            ("Tax number", self.tax),
            ("Territory", self.territory),
            ("Credit limit", self.credit),
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

    def values(self) -> DealerFormData:
        return DealerFormData(
            self.name.text(),
            self.business.text().strip() or None,
            self.phone.text(),
            self.email.text().strip() or None,
            self.address.toPlainText().strip() or None,
            self.cnic.text().strip() or None,
            self.tax.text().strip() or None,
            self.territory.text().strip() or None,
            self.credit.decimal_value("Credit limit"),
            self.notes.toPlainText().strip() or None,
        )


class DealersScreen(QWidget):
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
        self._data: list[DealerFormData] = []
        self._active: list[bool] = []
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("Dealers")
        title.setObjectName("PageTitle")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search dealer, business, phone, or territory")
        self.status_filter = QComboBox()
        self.status_filter.addItems(("Active", "Inactive", "All"))
        history = QPushButton("Sales History")
        history.setProperty("secondary", True)
        edit = QPushButton("Edit")
        edit.setProperty("secondary", True)
        add = QPushButton("Add Dealer")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.search)
        header.addWidget(self.status_filter)
        header.addWidget(history)
        header.addWidget(edit)
        header.addWidget(add)
        self.model = RowsTableModel(
            (
                "Dealer",
                "Contact",
                "Phone",
                "Email",
                "Territory",
                "Balance",
                "Credit Limit",
                "Active",
                "Actions",
            ),
            self,
        )
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addLayout(header)
        layout.addWidget(self.table)
        self.state_label = QLabel("Loading dealers…")
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
        self.state_label.setText("Loading dealers…")

        def operation() -> tuple[
            list[tuple[object, ...]], list[uuid.UUID], list[DealerFormData], list[bool]
        ]:
            with self._session_factory() as session:
                statement = select(Dealer)
                if status != "All":
                    statement = statement.where(Dealer.is_active.is_(status == "Active"))
                if query:
                    pattern = f"%{query}%"
                    statement = statement.where(
                        or_(
                            Dealer.name.ilike(pattern),
                            Dealer.business_name.ilike(pattern),
                            Dealer.phone.ilike(pattern),
                            Dealer.email.ilike(pattern),
                            Dealer.address.ilike(pattern),
                            Dealer.tax_number.ilike(pattern),
                            Dealer.territory.ilike(pattern),
                        )
                    )
                dealers = list(session.scalars(statement.order_by(Dealer.name).limit(500)))
                return (
                    [
                        (
                            dealer.display_name,
                            dealer.name,
                            dealer.phone,
                            dealer.email or "—",
                            dealer.territory or "—",
                            f"{self._currency} {dealer.balance:,.2f}",
                            f"{self._currency} {dealer.credit_limit:,.2f}",
                            "Yes" if dealer.is_active else "No",
                            "",
                        )
                        for dealer in dealers
                    ],
                    [dealer.id for dealer in dealers],
                    [
                        DealerFormData(
                            dealer.name,
                            dealer.business_name,
                            dealer.phone,
                            dealer.email,
                            dealer.address,
                            dealer.cnic,
                            dealer.tax_number,
                            dealer.territory,
                            dealer.credit_limit,
                            dealer.notes,
                        )
                        for dealer in dealers
                    ],
                    [dealer.is_active for dealer in dealers],
                )

        self._worker = start_worker(
            operation, succeeded=self._display, failed=lambda error: show_error(self, error)
        )

    def _display(self, result: object) -> None:
        rows, self._ids, self._data, self._active = cast(
            tuple[list[tuple[object, ...]], list[uuid.UUID], list[DealerFormData], list[bool]],
            result,
        )
        self.model.set_rows(rows)
        self.state_label.setText("No dealers found." if not rows else f"{len(rows)} dealers shown")
        populate_row_actions(
            self.table,
            8,
            len(rows),
            (
                ("View", self._view),
                ("Edit", self._edit_row),
                ("Activate / deactivate", self._toggle_active),
            ),
        )

    def _selected(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def _add(self) -> None:
        dialog = DealerDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._save(None, dialog.values())

    def _edit(self) -> None:
        row = self._selected()
        if row is None:
            QMessageBox.information(self, "Select dealer", "Select a dealer to edit.")
            return
        dialog = DealerDialog(self._data[row], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._save(self._ids[row], dialog.values())

    def _edit_row(self, row: int) -> None:
        if row >= len(self._data):
            return
        dialog = DealerDialog(self._data[row], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._save(self._ids[row], dialog.values())

    def _view(self, row: int) -> None:
        if row >= len(self._data):
            return
        data = self._data[row]
        show_record_details(
            self,
            data.business_name or data.name,
            (
                ("Contact", data.name),
                ("Phone", data.phone),
                ("Email", data.email),
                ("Address", data.address),
                ("CNIC", data.cnic),
                ("Tax number", data.tax_number),
                ("Territory", data.territory),
                ("Credit limit", f"{self._currency} {data.credit_limit:,.2f}"),
                ("Notes", data.notes),
            ),
        )

    def _delete(self, row: int) -> None:
        if row >= len(self._ids):
            return
        if (
            QMessageBox.question(
                self,
                "Delete dealer",
                "Remove this dealer from active lists? Existing sales history is preserved.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        def operation() -> None:
            with self._session_factory.begin() as session:
                DealerService(session).set_active(
                    self._ids[row], actor=self._actor, is_active=False
                )

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: self._saved("Dealer status updated."),
            failed=lambda error: show_error(self, error),
        )

    def _toggle_active(self, row: int) -> None:
        if row >= len(self._ids):
            return
        new_state = not self._active[row]
        action = "restore" if new_state else "remove from active lists"
        if (
            QMessageBox.question(
                self, "Confirm dealer status", f"Do you want to {action} this dealer?"
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        def operation() -> None:
            with self._session_factory.begin() as session:
                DealerService(session).set_active(
                    self._ids[row], actor=self._actor, is_active=new_state
                )

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: self._saved("Dealer status updated."),
            failed=lambda error: show_error(self, error),
        )

    def _saved(self, message: str) -> None:
        show_success(self, message)
        self.refresh()

    def _save(self, dealer_id: uuid.UUID | None, data: DealerFormData) -> None:
        def operation() -> None:
            with self._session_factory.begin() as session:
                service = DealerService(session)
                if dealer_id:
                    service.update(
                        dealer_id,
                        actor=self._actor,
                        name=data.name,
                        business_name=data.business_name,
                        phone=data.phone,
                        email=data.email,
                        address=data.address,
                        cnic=data.cnic,
                        tax_number=data.tax_number,
                        territory=data.territory,
                        credit_limit=data.credit_limit,
                        notes=data.notes,
                    )
                else:
                    service.create(
                        actor=self._actor,
                        name=data.name,
                        business_name=data.business_name,
                        phone=data.phone,
                        email=data.email,
                        address=data.address,
                        cnic=data.cnic,
                        tax_number=data.tax_number,
                        territory=data.territory,
                        credit_limit=data.credit_limit,
                        notes=data.notes,
                    )

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: self._saved("Dealer saved."),
            failed=lambda error: show_error(self, error),
        )

    def _history(self) -> None:
        row = self._selected()
        if row is None:
            QMessageBox.information(self, "Select dealer", "Select a dealer first.")
            return
        dealer_id = self._ids[row]

        def operation() -> list[tuple[object, ...]]:
            with self._session_factory() as session:
                sales = session.scalars(
                    select(Sale).where(Sale.dealer_id == dealer_id).order_by(Sale.sale_date.desc())
                )
                return [
                    (
                        sale.invoice_number,
                        format_date(sale.sale_date),
                        f"{self._currency} {sale.total:,.2f}",
                        f"{self._currency} {sale.remaining_amount:,.2f}",
                        sale.payment_status.value,
                    )
                    for sale in sales
                ]

        def display(rows: object) -> None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Dealer sales history")
            dialog.resize(760, 420)
            layout = QVBoxLayout(dialog)
            model = RowsTableModel(("Invoice", "Date", "Total", "Balance", "Payment"), dialog)
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
