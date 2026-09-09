"""Preview, print, and save invoice PDFs from any screen that lists sales."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import Settings
from app.printing.invoice_documents import SaleDocument, build_sale_document
from app.printing.print_preview import open_print_preview
from app.printing.printer_service import PrinterService, write_temporary_pdf
from app.ui.widgets import RowsTableModel, configure_table, record_count_text, show_error
from app.ui.workers import start_worker


class InvoiceDocumentActions:
    """The three invoice actions, bound to one parent widget.

    Sales, dealer history, and customer history all offer the same operations on a
    saved invoice, so the worker handling and printer wiring live here once.
    """

    def __init__(
        self,
        parent: QWidget,
        session_factory: sessionmaker[Session],
        settings: Settings,
    ) -> None:
        self._parent = parent
        self._session_factory = session_factory
        self._settings = settings

    def preview(self, sale_id: uuid.UUID) -> None:
        """Open the A4 invoice in the preview, laid out for the chosen printer."""

        def display(result: object) -> None:
            document = cast(SaleDocument, result)
            path = write_temporary_pdf(document.payload, f"invoice-{document.invoice_number}")
            open_print_preview(
                path,
                self._parent,
                printer_name=document.preferences.printer_name,
                suggested_filename=f"{document.invoice_number}.pdf",
            )

        self._render(sale_id, thermal=False, succeeded=display)

    def print_receipt(self, sale_id: uuid.UUID) -> None:
        """Send the receipt straight to the configured printer."""

        def send(result: object) -> None:
            document = cast(SaleDocument, result)
            path = write_temporary_pdf(document.payload, f"receipt-{document.invoice_number}")
            service = PrinterService()
            printer = service.resolve_printer_name(document.preferences.printer_name)
            try:
                printed = service.print_pdf(
                    path,
                    self._parent,
                    printer_name=document.preferences.printer_name,
                    prompt=not printer,
                )
            except Exception as error:
                show_error(self._parent, error)
                return
            if printed:
                QMessageBox.information(
                    self._parent,
                    "Invoice sent to printer",
                    f"Invoice {document.invoice_number} was sent to "
                    f"{printer or 'the printer'} on "
                    f"{document.preferences.receipt.label} paper.",
                )

        self._render(sale_id, thermal=True, succeeded=send, record_issue=True)

    def save_pdf(self, sale_id: uuid.UUID, *, suggested_name: str | None = None) -> None:
        """Write the A4 invoice to a file the operator chooses."""

        path, _filter = QFileDialog.getSaveFileName(
            self._parent,
            "Save invoice",
            suggested_name or "delivery-challan-invoice.pdf",
            "PDF documents (*.pdf)",
        )
        if not path:
            return

        def operation() -> Path:
            document = build_sale_document(
                self._session_factory, self._settings, sale_id, record_issue=True
            )
            return PrinterService().save_pdf(Path(path), document.payload)

        start_worker(
            operation,
            succeeded=lambda saved: QMessageBox.information(
                self._parent, "Invoice saved", f"Saved to {saved}"
            ),
            failed=lambda error: show_error(self._parent, error),
        )

    def _render(
        self,
        sale_id: uuid.UUID,
        *,
        thermal: bool,
        succeeded: Callable[[object], object],
        record_issue: bool = False,
    ) -> None:
        start_worker(
            lambda: build_sale_document(
                self._session_factory,
                self._settings,
                sale_id,
                thermal=thermal,
                record_issue=record_issue,
            ),
            succeeded=succeeded,
            failed=lambda error: show_error(self._parent, error),
        )


class InvoiceHistoryDialog(QDialog):
    """A list of past invoices that can be previewed, printed, or saved.

    Dealer and customer screens both show a party's invoices, and an operator
    reaching for one almost always wants to reprint it, so the list carries the
    document actions rather than being read-only.
    """

    HEADERS = ("Invoice", "Date", "Total", "Paid", "Balance", "Payment")

    def __init__(
        self,
        title: str,
        rows: Sequence[Sequence[object]],
        sale_ids: Sequence[uuid.UUID],
        documents: InvoiceDocumentActions,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._sale_ids = list(sale_ids)
        self._documents = documents
        self.setWindowTitle(title)
        self.resize(900, 460)
        layout = QVBoxLayout(self)
        self.model = RowsTableModel(self.HEADERS, self)
        self.model.set_rows(rows)
        self.table = QTableView()
        self.table.setModel(self.model)
        configure_table(self.table, stretch_column=0, minimum_section_size=72)
        self.table.doubleClicked.connect(lambda _index: self._preview())
        self.count_label = QLabel(record_count_text(len(self._sale_ids), "invoice"))
        self.count_label.setObjectName("RecordCount")
        self.preview_button = QPushButton("Print Preview")
        self.print_button = QPushButton("Print Invoice")
        self.save_button = QPushButton("Save PDF")
        for button in (self.preview_button, self.print_button, self.save_button):
            button.setProperty("secondary", True)
            button.setEnabled(False)
        close_button = QPushButton("Close")
        actions = QHBoxLayout()
        actions.addWidget(self.count_label)
        actions.addStretch()
        for button in (self.save_button, self.preview_button, self.print_button, close_button):
            actions.addWidget(button)
        layout.addWidget(self.table, 1)
        layout.addLayout(actions)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.preview_button.clicked.connect(self._preview)
        self.print_button.clicked.connect(self._print)
        self.save_button.clicked.connect(self._save)
        close_button.clicked.connect(self.reject)
        if self._sale_ids:
            self.table.selectRow(0)

    def selected_sale(self) -> uuid.UUID | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows or rows[0].row() >= len(self._sale_ids):
            return None
        return self._sale_ids[rows[0].row()]

    def selected_invoice_number(self) -> str:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return "invoice"
        return str(self.model.row(rows[0].row())[0])

    def _selection_changed(self) -> None:
        enabled = self.selected_sale() is not None
        for button in (self.preview_button, self.print_button, self.save_button):
            button.setEnabled(enabled)

    def _preview(self) -> None:
        if (sale_id := self.selected_sale()) is not None:
            self._documents.preview(sale_id)

    def _print(self) -> None:
        if (sale_id := self.selected_sale()) is not None:
            self._documents.print_receipt(sale_id)

    def _save(self) -> None:
        if (sale_id := self.selected_sale()) is not None:
            self._documents.save_pdf(
                sale_id, suggested_name=f"{self.selected_invoice_number()}.pdf"
            )
