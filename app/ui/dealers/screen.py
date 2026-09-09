"""Dealer accounts, credit balances, contact details, and sales history."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import cast

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
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

from app.config.settings import Settings
from app.models.dealer import Dealer
from app.models.enums import PaymentMethod
from app.models.sale import Sale
from app.printing.preferences import load_print_preferences
from app.printing.print_preview import open_print_preview
from app.printing.printer_service import PrinterService, write_temporary_pdf
from app.reports.pdf_exporter import PDFReportExporter
from app.security.authentication import AuthenticatedUser
from app.services.catalog_service import DealerService
from app.services.dealer_account_service import (
    INVOICE,
    DealerAccountService,
    DealerPaymentResult,
    DealerStatement,
)
from app.ui.documents import InvoiceDocumentActions, InvoiceHistoryDialog
from app.ui.forms import MoneyEdit
from app.ui.widgets import (
    RowsTableModel,
    configure_table,
    populate_enum_combo,
    populate_row_actions,
    record_count_text,
    selected_enum,
    show_error,
    show_record_details,
    show_success,
)
from app.ui.workers import FunctionWorker, start_worker
from app.utils.formatting import format_date, format_money


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


class DealerPaymentDialog(QDialog):
    """Take one instalment against a dealer's running account."""

    def __init__(
        self,
        dealer_name: str,
        outstanding: Decimal,
        currency: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Record Dealer Payment")
        self.setMinimumWidth(430)
        form = QFormLayout(self)
        summary = QLabel(
            f"{dealer_name} currently owes {format_money(outstanding, currency)}.\n"
            "The payment is applied to their oldest unpaid invoices first; anything "
            "left over stays on the account as credit."
        )
        summary.setWordWrap(True)
        self.amount = MoneyEdit()
        self.amount.setPlaceholderText("0.00")
        self.method = QComboBox()
        populate_enum_combo(self.method, PaymentMethod)
        self.reference = QLineEdit()
        self.reference.setPlaceholderText("Cheque number, transfer reference, receipt no.")
        self.notes = QTextEdit()
        self.notes.setMaximumHeight(70)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Record payment")
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        form.addRow(summary)
        form.addRow("Amount *", self.amount)
        form.addRow("Method", self.method)
        form.addRow("Reference", self.reference)
        form.addRow("Notes", self.notes)
        form.addRow(buttons)

    def _accept_if_valid(self) -> None:
        try:
            amount = self.amount.decimal_value("Payment")
        except Exception:
            QMessageBox.information(self, "Enter an amount", "Enter the amount received.")
            self.amount.setFocus()
            return
        if amount <= 0:
            QMessageBox.information(
                self, "Enter an amount", "The payment must be greater than zero."
            )
            self.amount.setFocus()
            return
        self.accept()

    def payment_method(self) -> PaymentMethod:
        return selected_enum(self.method, PaymentMethod)


class DealersScreen(QWidget):
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
        self._worker: FunctionWorker | None = None
        self._ids: list[uuid.UUID] = []
        self._data: list[DealerFormData] = []
        self._active: list[bool] = []
        self._balances: list[Decimal] = []
        self._documents = InvoiceDocumentActions(self, session_factory, settings)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("Dealers")
        title.setObjectName("PageTitle")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search dealer, business, phone, or territory")
        self.status_filter = QComboBox()
        self.status_filter.addItems(("Active", "Inactive", "All"))
        history = QPushButton("Sales History")
        statement = QPushButton("Account Statement")
        payment = QPushButton("Record Payment")
        edit = QPushButton("Edit")
        for secondary in (history, statement, payment, edit):
            secondary.setProperty("secondary", True)
        payment.setToolTip("Record an instalment against the selected dealer's account")
        add = QPushButton("Add Dealer")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.search)
        header.addWidget(self.status_filter)
        header.addWidget(history)
        header.addWidget(statement)
        header.addWidget(payment)
        header.addWidget(edit)
        header.addWidget(add)
        self.model = RowsTableModel(
            (
                "Dealer",
                "Contact",
                "Phone",
                "Email",
                "Territory",
                "Outstanding",
                "Credit Limit",
                "Active",
                "Actions",
            ),
            self,
        )
        self.table = QTableView()
        self.table.setModel(self.model)
        configure_table(self.table, stretch_column=0, minimum_section_size=74)
        layout.addLayout(header)
        layout.addWidget(self.table)
        self.state_label = QLabel("Loading dealers…")
        self.state_label.setObjectName("RecordCount")
        layout.addWidget(self.state_label)
        add.clicked.connect(self._add)
        edit.clicked.connect(self._edit)
        history.clicked.connect(self._history)
        statement.clicked.connect(self._statement)
        payment.clicked.connect(self._record_payment)
        self.search.returnPressed.connect(self.refresh)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda _text: self._search_timer.start())
        self.status_filter.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def _balance_text(self, balance: Decimal) -> str:
        """Show a negative balance as the credit the dealer has paid in advance."""

        if balance < 0:
            return f"{format_money(-balance, self._currency)} credit"
        return format_money(balance, self._currency)

    def refresh(self) -> None:
        query = self.search.text().strip()
        status = self.status_filter.currentText()
        self.state_label.setText("Loading dealers…")

        def operation() -> tuple[
            list[tuple[object, ...]],
            list[uuid.UUID],
            list[DealerFormData],
            list[bool],
            list[Decimal],
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
                            self._balance_text(dealer.balance),
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
                    [dealer.balance for dealer in dealers],
                )

        self._worker = start_worker(
            operation, succeeded=self._display, failed=lambda error: show_error(self, error)
        )

    def _display(self, result: object) -> None:
        rows, self._ids, self._data, self._active, self._balances = cast(
            tuple[
                list[tuple[object, ...]],
                list[uuid.UUID],
                list[DealerFormData],
                list[bool],
                list[Decimal],
            ],
            result,
        )
        self.model.set_rows(rows)
        self.state_label.setText(record_count_text(len(rows), "dealer"))
        populate_row_actions(
            self.table,
            8,
            len(rows),
            (
                ("View", self._view),
                ("Edit", self._edit_row),
                ("Record payment", self._record_payment_row),
                ("Account statement", self._statement_row),
                ("Sales history", self._history_row),
                ("Activate / deactivate", self._toggle_active),
                ("Delete", self._delete_row),
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

    def _delete_row(self, row: int) -> None:
        if row >= len(self._ids):
            return
        if (
            QMessageBox.question(
                self,
                "Delete dealer",
                "Delete this dealer permanently?\n\nDealers with sales history or an "
                "outstanding balance cannot be deleted; deactivate them instead so past "
                "invoices stay complete.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        dealer_id = self._ids[row]

        def operation() -> None:
            with self._session_factory.begin() as session:
                DealerService(session).delete(dealer_id, actor=self._actor)

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: self._saved("Dealer deleted."),
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

    def _record_payment_row(self, row: int) -> None:
        if self._select_row(row):
            self._record_payment()

    def _statement_row(self, row: int) -> None:
        if self._select_row(row):
            self._statement()

    def _history_row(self, row: int) -> None:
        if self._select_row(row):
            self._history()

    def _select_row(self, row: int) -> bool:
        if row >= len(self._ids):
            return False
        self.table.selectRow(row)
        return True

    def _record_payment(self) -> None:
        """Take an instalment and apply it across the dealer's unpaid invoices."""

        row = self._selected()
        if row is None or row >= len(self._ids):
            QMessageBox.information(self, "Select dealer", "Select a dealer first.")
            return
        dealer_id = self._ids[row]
        dialog = DealerPaymentDialog(
            self._data[row].business_name or self._data[row].name,
            self._balances[row],
            self._currency,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        amount = dialog.amount.decimal_value("Payment")
        method = dialog.payment_method()
        reference = dialog.reference.text()
        notes = dialog.notes.toPlainText()

        def operation() -> DealerPaymentResult:
            with self._session_factory.begin() as session:
                return DealerAccountService(session).record_payment(
                    dealer_id,
                    actor=self._actor,
                    method=method,
                    amount=amount,
                    reference=reference,
                    notes=notes,
                )

        def recorded(result: object) -> None:
            outcome = cast(DealerPaymentResult, result)
            lines = [
                f"Received {format_money(outcome.amount, self._currency)}.",
            ]
            if outcome.settled:
                lines.append("")
                lines.append("Applied to:")
                lines.extend(
                    f"  {entry.invoice_number}: "
                    f"{format_money(entry.applied, self._currency)} "
                    f"(balance {format_money(entry.remaining, self._currency)})"
                    for entry in outcome.settled
                )
            if outcome.credited:
                lines.append("")
                lines.append(
                    f"{format_money(outcome.credited, self._currency)} was held as "
                    "account credit against future invoices."
                )
            lines.append("")
            lines.append(f"Account balance is now {format_money(outcome.balance, self._currency)}.")
            QMessageBox.information(self, "Payment recorded", "\n".join(lines))
            self.refresh()

        self._worker = start_worker(
            operation, succeeded=recorded, failed=lambda error: show_error(self, error)
        )

    def _statement(self) -> None:
        """Show every charge and payment for the dealer with a running balance."""

        row = self._selected()
        if row is None or row >= len(self._ids):
            QMessageBox.information(self, "Select dealer", "Select a dealer first.")
            return
        dealer_id = self._ids[row]

        def operation() -> DealerStatement:
            with self._session_factory() as session:
                return DealerAccountService(session).statement(dealer_id)

        def display(result: object) -> None:
            statement = cast(DealerStatement, result)
            dialog = QDialog(self)
            dialog.setWindowTitle(f"Account statement — {statement.dealer_name}")
            dialog.resize(980, 500)
            layout = QVBoxLayout(dialog)
            summary_parts = [
                f"Invoiced {format_money(statement.invoiced, self._currency)}",
                f"Paid {format_money(statement.paid, self._currency)}",
                f"Outstanding {format_money(statement.outstanding, self._currency)}",
            ]
            available = statement.available_credit
            if available is not None:
                summary_parts.append(
                    f"Credit available {format_money(available, self._currency)} "
                    f"of {format_money(statement.credit_limit, self._currency)}"
                )
            summary = QLabel("   ·   ".join(summary_parts))
            summary.setObjectName("SectionTitle")
            summary.setWordWrap(True)
            model = RowsTableModel(
                ("Date", "Type", "Reference", "Detail", "Charge", "Payment", "Balance"), dialog
            )
            model.set_rows(
                [
                    (
                        format_date(entry.occurred_at),
                        "Invoice" if entry.kind == INVOICE else "Payment",
                        entry.reference,
                        entry.detail,
                        format_money(entry.charge, self._currency) if entry.charge else "—",
                        format_money(entry.credit, self._currency) if entry.credit else "—",
                        format_money(entry.balance, self._currency),
                    )
                    for entry in statement.entries
                ]
            )
            table = QTableView()
            table.setModel(model)
            configure_table(table, stretch_column=3, minimum_section_size=70)
            count = QLabel(
                record_count_text(len(statement.entries), "account entry", "account entries")
            )
            count.setObjectName("RecordCount")
            save = QPushButton("Save PDF")
            preview = QPushButton("Print Preview")
            close_button = QPushButton("Close")
            for secondary in (save, preview):
                secondary.setProperty("secondary", True)
            save.clicked.connect(lambda: self._export_statement(statement, save_to_file=True))
            preview.clicked.connect(lambda: self._export_statement(statement, save_to_file=False))
            close_button.clicked.connect(dialog.reject)
            actions = QHBoxLayout()
            actions.addWidget(count)
            actions.addStretch()
            actions.addWidget(save)
            actions.addWidget(preview)
            actions.addWidget(close_button)
            layout.addWidget(summary)
            layout.addWidget(table, 1)
            layout.addLayout(actions)
            dialog.exec()

        self._worker = start_worker(
            operation, succeeded=display, failed=lambda error: show_error(self, error)
        )

    def _statement_document(self, statement: DealerStatement) -> bytes:
        """Render the statement as a PDF the dealer can be handed or emailed."""

        subtitle = (
            f"Invoiced {format_money(statement.invoiced, self._currency)} · "
            f"Paid {format_money(statement.paid, self._currency)} · "
            f"Outstanding {format_money(statement.outstanding, self._currency)}"
        )
        available = statement.available_credit
        if available is not None:
            subtitle += (
                f" · Credit available {format_money(available, self._currency)} "
                f"of {format_money(statement.credit_limit, self._currency)}"
            )
        return PDFReportExporter().render(
            title=f"Account statement — {statement.dealer_name}",
            subtitle=subtitle,
            headers=("Date", "Type", "Reference", "Detail", "Charge", "Payment", "Balance"),
            rows=[
                (
                    format_date(entry.occurred_at),
                    "Invoice" if entry.kind == INVOICE else "Payment",
                    entry.reference,
                    entry.detail,
                    format_money(entry.charge, self._currency) if entry.charge else "—",
                    format_money(entry.credit, self._currency) if entry.credit else "—",
                    format_money(entry.balance, self._currency),
                )
                for entry in statement.entries
            ],
        )

    def _export_statement(self, statement: DealerStatement, *, save_to_file: bool) -> None:
        name = f"statement-{statement.dealer_name}".replace(" ", "-").lower()
        if save_to_file:
            path, _filter = QFileDialog.getSaveFileName(
                self, "Save account statement", f"{name}.pdf", "PDF documents (*.pdf)"
            )
            if not path:
                return
            self._worker = start_worker(
                lambda: PrinterService().save_pdf(Path(path), self._statement_document(statement)),
                succeeded=lambda saved: QMessageBox.information(
                    self, "Statement saved", f"Saved to {saved}"
                ),
                failed=lambda error: show_error(self, error),
            )
            return

        def operation() -> tuple[Path, str]:
            payload = self._statement_document(statement)
            with self._session_factory() as session:
                printer = load_print_preferences(session, self._settings).printer_name
            return write_temporary_pdf(payload, name), printer

        def preview(result: object) -> None:
            document, printer = cast(tuple[Path, str], result)
            open_print_preview(
                document, self, printer_name=printer, suggested_filename=f"{name}.pdf"
            )

        self._worker = start_worker(
            operation, succeeded=preview, failed=lambda error: show_error(self, error)
        )

    def _history(self) -> None:
        row = self._selected()
        if row is None:
            QMessageBox.information(self, "Select dealer", "Select a dealer first.")
            return
        dealer_id = self._ids[row]

        def operation() -> tuple[list[tuple[object, ...]], list[uuid.UUID]]:
            with self._session_factory() as session:
                sales = list(
                    session.scalars(
                        select(Sale)
                        .where(Sale.dealer_id == dealer_id)
                        .order_by(Sale.sale_date.desc())
                    )
                )
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
            dialog = InvoiceHistoryDialog(
                "Dealer sales history",
                rows,
                sale_ids,
                self._documents,
                self,
            )
            dialog.exec()

        self._worker = start_worker(
            operation, succeeded=display, failed=lambda error: show_error(self, error)
        )
