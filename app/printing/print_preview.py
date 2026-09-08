"""Document preview with print, printer selection, and PDF save actions."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPageSize
from PySide6.QtPrintSupport import QPrinter, QPrintPreviewWidget
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.printing.printer_service import SYSTEM_DEFAULT_PRINTER, PrinterService

_ZOOM_STEPS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)


class PrintPreviewDialog(QDialog):
    """Preview a generated PDF exactly as the selected printer will produce it.

    The printer chosen in Settings drives the initial preview, and switching the
    printer here re-lays the pages out for that device's paper size.
    """

    def __init__(
        self,
        document_path: Path,
        parent: QWidget | None = None,
        *,
        printer_name: str | None = None,
        suggested_filename: str = "document.pdf",
    ) -> None:
        super().__init__(parent)
        self._service = PrinterService()
        self._path = Path(document_path)
        self._suggested_filename = suggested_filename
        self._document = self._service.load(self._path)
        self.setWindowTitle("Print preview")
        self.setMinimumSize(720, 560)

        self.printer_selector = QComboBox()
        self.printer_selector.addItem("System default", SYSTEM_DEFAULT_PRINTER)
        for name in self._service.available_printers():
            self.printer_selector.addItem(name, name)
        self._select_printer(printer_name)

        self._preview_container = QVBoxLayout()
        self._preview_container.setContentsMargins(0, 0, 0, 0)
        self._printer = self._new_printer()
        self.preview = self._new_preview_widget()

        self.page_label = QLabel()
        self.page_label.setObjectName("RecordCount")
        self.print_button = QPushButton("Print PDF")
        self.save_button = QPushButton("Save PDF")
        self.close_button = QPushButton("Close")
        self.save_button.setProperty("secondary", True)
        self.close_button.setProperty("secondary", True)
        self.print_button.setDefault(True)
        zoom_out = QPushButton("-")
        zoom_in = QPushButton("+")
        fit_width = QPushButton("Fit width")
        for compact in (zoom_out, zoom_in, fit_width):
            compact.setProperty("secondary", True)
        zoom_out.setToolTip("Zoom out")
        zoom_in.setToolTip("Zoom in")

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Printer"))
        toolbar.addWidget(self.printer_selector, 1)
        toolbar.addWidget(zoom_out)
        toolbar.addWidget(zoom_in)
        toolbar.addWidget(fit_width)
        actions = QHBoxLayout()
        actions.addWidget(self.page_label)
        actions.addStretch()
        actions.addWidget(self.save_button)
        actions.addWidget(self.print_button)
        actions.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addLayout(toolbar)
        layout.addLayout(self._preview_container, 1)
        layout.addLayout(actions)

        self.printer_selector.currentIndexChanged.connect(self._printer_changed)
        zoom_out.clicked.connect(lambda: self._zoom(-1))
        zoom_in.clicked.connect(lambda: self._zoom(1))
        fit_width.clicked.connect(self.preview.fitToWidth)
        self.print_button.clicked.connect(self._print)
        self.save_button.clicked.connect(self._save)
        self.close_button.clicked.connect(self.reject)
        self._update_page_label()

    def selected_printer(self) -> str:
        data = self.printer_selector.currentData()
        return str(data or SYSTEM_DEFAULT_PRINTER)

    def _select_printer(self, printer_name: str | None) -> None:
        index = self.printer_selector.findData((printer_name or "").strip())
        self.printer_selector.setCurrentIndex(max(0, index))

    def _new_printer(self) -> QPrinter:
        return self._service.prepare(
            self._document, self._service.create_printer(self.selected_printer())
        )

    def _new_preview_widget(self) -> QPrintPreviewWidget:
        """Build a preview bound to the current printer and show it.

        ``QPrintPreviewWidget`` takes its printer at construction, so switching the
        selected device replaces the widget rather than mutating it.
        """

        widget = QPrintPreviewWidget(self._printer, self)
        widget.setViewMode(QPrintPreviewWidget.ViewMode.SinglePageView)
        widget.paintRequested.connect(self._render)
        self._preview_container.addWidget(widget)
        return widget

    def _printer_changed(self) -> None:
        """Re-lay the preview out for the newly selected device."""

        zoom = self.preview.zoomFactor()
        self._preview_container.removeWidget(self.preview)
        self.preview.deleteLater()
        self._printer = self._new_printer()
        self.preview = self._new_preview_widget()
        self.preview.setZoomFactor(zoom)
        self.preview.updatePreview()
        self._update_page_label()

    def _update_page_label(self) -> None:
        pages = self._document.pageCount()
        page_size = self._printer.pageLayout().pageSize()
        millimetres = page_size.size(QPageSize.Unit.Millimeter)
        standard = (
            page_size.name() if page_size.id() is not QPageSize.PageSizeId.Custom else "Custom"
        )
        self.page_label.setText(
            f"{pages} page{'s' if pages != 1 else ''} on {standard} "
            f"({millimetres.width():.0f} x {millimetres.height():.0f} mm)"
        )

    def _zoom(self, direction: int) -> None:
        current = self.preview.zoomFactor()
        candidates = _ZOOM_STEPS if direction > 0 else tuple(reversed(_ZOOM_STEPS))
        for step in candidates:
            if (step > current + 0.01) if direction > 0 else (step < current - 0.01):
                self.preview.setZoomFactor(step)
                return

    def _render(self, printer: QPrinter) -> None:
        self._service.paint(self._document, printer)

    def _print(self) -> None:
        try:
            printer = self._service.prepare(
                self._document, self._service.create_printer(self.selected_printer())
            )
            if not printer.printerName():
                QMessageBox.warning(
                    self,
                    "No printer available",
                    "No printer is installed. Install a printer, or use Save PDF instead.",
                )
                return
            self._service.paint(self._document, printer)
        except Exception as error:
            from app.ui.widgets import show_error

            show_error(self, error)
            return
        self.accept()

    def _save(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save PDF", self._suggested_filename, "PDF documents (*.pdf)"
        )
        if not path:
            return
        try:
            saved = self._service.save_pdf(Path(path), self._path.read_bytes())
        except OSError as error:
            QMessageBox.critical(self, "Unable to save", str(error))
            return
        QMessageBox.information(self, "PDF saved", f"Saved to {saved}")


def open_print_preview(
    document_path: Path,
    parent: QWidget | None = None,
    *,
    printer_name: str | None = None,
    suggested_filename: str = "document.pdf",
) -> None:
    dialog = PrintPreviewDialog(
        document_path,
        parent,
        printer_name=printer_name,
        suggested_filename=suggested_filename,
    )
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    dialog.exec()
