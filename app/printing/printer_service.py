"""Native print, preview, and PDF save operations for generated documents."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QPainter
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrintPreviewDialog
from PySide6.QtWidgets import QDialog, QWidget


class PrinterService:
    def available_printers(self) -> list[str]:
        from PySide6.QtPrintSupport import QPrinterInfo

        return [printer.printerName() for printer in QPrinterInfo.availablePrinters()]

    def save_pdf(self, path: Path, payload: bytes) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def print_pdf(self, path: Path, parent: QWidget | None = None) -> bool:
        document = self._load(path)
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dialog = QPrintDialog(printer, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        self._paint(document, printer)
        return True

    def preview_pdf(self, path: Path, parent: QWidget | None = None) -> None:
        document = self._load(path)
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        preview = QPrintPreviewDialog(printer, parent)
        preview.paintRequested.connect(lambda target: self._paint(document, target))
        preview.exec()

    @staticmethod
    def _load(path: Path) -> QPdfDocument:
        document = QPdfDocument()
        error = document.load(str(path))
        if error is not QPdfDocument.Error.None_:
            raise ValueError(f"Unable to load PDF for printing: {error.name}")
        return document

    @staticmethod
    def _paint(document: QPdfDocument, printer: QPrinter) -> None:
        painter = QPainter(printer)
        try:
            page_rect = printer.pageRect(QPrinter.Unit.DevicePixel).toRect()
            for page in range(document.pageCount()):
                if page and not printer.newPage():
                    raise RuntimeError("The printer could not start a new page.")
                image = document.render(page, QSize(page_rect.width(), page_rect.height()))
                painter.drawImage(page_rect, image)
        finally:
            painter.end()
