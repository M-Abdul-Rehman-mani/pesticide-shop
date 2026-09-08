"""Native print, preview, and PDF save operations for generated documents."""

from __future__ import annotations

import atexit
import re
import shutil
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QMarginsF, QPoint, QRect, QSize, QSizeF, Qt
from PySide6.QtGui import QPageLayout, QPageSize, QPainter
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo
from PySide6.QtWidgets import QDialog, QWidget

from app.utils.exceptions import InfrastructureError

#: Highest resolution a page is rasterised at before the printer scales it.
#: Rendering an A4 page at a printer's native 1200 dpi would allocate hundreds of
#: megabytes for no visible gain, so raster detail is capped and the printer driver
#: performs the final scale.
MAX_RENDER_DPI = 300

SYSTEM_DEFAULT_PRINTER = ""


@dataclass(frozen=True, slots=True)
class ReceiptFormat:
    """A page geometry the shop can print invoices on."""

    key: str
    label: str
    width_mm: int | None

    @property
    def is_thermal(self) -> bool:
        return self.width_mm is not None


A4_FORMAT = ReceiptFormat("A4", "A4", None)
THERMAL_58_FORMAT = ReceiptFormat("58", "58 mm thermal", 58)
THERMAL_80_FORMAT = ReceiptFormat("80", "80 mm thermal", 80)
RECEIPT_FORMATS: tuple[ReceiptFormat, ...] = (A4_FORMAT, THERMAL_58_FORMAT, THERMAL_80_FORMAT)
DEFAULT_RECEIPT_FORMAT = THERMAL_80_FORMAT


def receipt_format(key: str | None) -> ReceiptFormat:
    """Return the stored receipt format, falling back to the 80 mm default."""

    for candidate in RECEIPT_FORMATS:
        if candidate.key == (key or "").strip().upper():
            return candidate
    return DEFAULT_RECEIPT_FORMAT


@lru_cache(maxsize=1)
def _document_cache() -> Path:
    """Return a temporary directory that is removed when the application exits.

    Preview and direct printing both need the document on disk; keeping them in one
    per-run directory stops generated invoices from accumulating in the user's
    temporary folder.
    """

    directory = Path(tempfile.mkdtemp(prefix="pesticide-shop-print-"))
    atexit.register(shutil.rmtree, directory, True)
    return directory


def write_temporary_pdf(payload: bytes, name: str = "document") -> Path:
    """Write a generated PDF to the per-run cache and return its path."""

    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "document"
    path = _document_cache() / f"{safe}.pdf"
    path.write_bytes(payload)
    return path


class PrinterService:
    def available_printers(self) -> list[str]:
        return [printer.printerName() for printer in QPrinterInfo.availablePrinters()]

    def system_default_printer(self) -> str:
        return QPrinterInfo.defaultPrinterName()

    def resolve_printer_name(self, preferred: str | None) -> str:
        """Return the printer to use, ignoring a saved name that no longer exists."""

        wanted = (preferred or "").strip()
        if wanted and wanted in self.available_printers():
            return wanted
        return self.system_default_printer()

    def save_pdf(self, path: Path, payload: bytes) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def create_printer(self, printer_name: str | None = None) -> QPrinter:
        """Build a high-resolution printer bound to the configured device."""

        name = self.resolve_printer_name(printer_name)
        info = QPrinterInfo.printerInfo(name) if name else QPrinterInfo()
        printer = (
            QPrinter(info, QPrinter.PrinterMode.HighResolution)
            if name and not info.isNull()
            else QPrinter(QPrinter.PrinterMode.HighResolution)
        )
        if name:
            printer.setPrinterName(name)
        return printer

    def prepare(self, document: QPdfDocument, printer: QPrinter) -> QPrinter:
        """Match the printer page to the document so receipts are not rescaled.

        A thermal receipt is generated at its exact roll width, so the printer must
        use that same page size with no margins; otherwise the driver centres an
        80 mm receipt on an A4 sheet.
        """

        if document.pageCount() <= 0:
            return printer
        size_points = document.pagePointSize(0)
        if size_points.isEmpty():
            return printer
        # An empty name lets Qt report the matched standard name ("A4") and fall
        # back to a descriptive custom name for thermal roll widths.
        page_size = QPageSize(
            QSizeF(size_points),
            QPageSize.Unit.Point,
            "",
            QPageSize.SizeMatchPolicy.FuzzyMatch,
        )
        orientation = (
            QPageLayout.Orientation.Landscape
            if size_points.width() > size_points.height()
            else QPageLayout.Orientation.Portrait
        )
        layout = QPageLayout(page_size, orientation, QMarginsF(0, 0, 0, 0))
        layout.setMode(QPageLayout.Mode.FullPageMode)
        printer.setPageLayout(layout)
        printer.setFullPage(True)
        return printer

    def print_pdf(
        self,
        path: Path,
        parent: QWidget | None = None,
        *,
        printer_name: str | None = None,
        prompt: bool = True,
    ) -> bool:
        """Print a generated PDF, optionally straight to the configured printer."""

        document = self.load(path)
        printer = self.prepare(document, self.create_printer(printer_name))
        if prompt:
            dialog = QPrintDialog(printer, parent)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            self.prepare(document, printer)
        elif not printer.printerName():
            raise InfrastructureError(
                "No printer is available. Choose a default printer in Settings > Printer."
            )
        self.paint(document, printer)
        return True

    def preview_pdf(
        self,
        path: Path,
        parent: QWidget | None = None,
        *,
        printer_name: str | None = None,
    ) -> None:
        """Open the document preview with print and save actions."""

        from app.printing.print_preview import PrintPreviewDialog

        dialog = PrintPreviewDialog(path, parent, printer_name=printer_name)
        dialog.exec()

    def load(self, path: Path) -> QPdfDocument:
        return self._load(path)

    def paint(self, document: QPdfDocument, printer: QPrinter) -> None:
        self._paint(document, printer)

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
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            resolution = max(printer.resolution(), 1)
            for page in range(document.pageCount()):
                if page and not printer.newPage():
                    raise RuntimeError("The printer could not start a new page.")
                paint_rect = printer.pageLayout().paintRectPixels(resolution)
                # The painter origin is the top-left of the paintable area, so the
                # target rectangle is always anchored at (0, 0) regardless of margins.
                target = QRect(QPoint(0, 0), paint_rect.size())
                if target.isEmpty():
                    continue
                image = document.render(page, PrinterService._render_size(target, resolution))
                if image.isNull():
                    continue
                painter.drawImage(
                    PrinterService._fitted(target, image.size()),
                    image,
                )
        finally:
            painter.end()

    @staticmethod
    def _render_size(target: QRect, resolution: int) -> QSize:
        """Rasterise at the printer resolution, capped at ``MAX_RENDER_DPI``."""

        scale = min(1.0, MAX_RENDER_DPI / resolution) if resolution else 1.0
        return QSize(
            max(1, int(target.width() * scale)),
            max(1, int(target.height() * scale)),
        )

    @staticmethod
    def _fitted(target: QRect, image_size: QSize) -> QRect:
        """Centre the rendered page inside the printable area without distortion."""

        scaled = image_size.scaled(target.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRect(
            target.x() + (target.width() - scaled.width()) // 2,
            target.y() + (target.height() - scaled.height()) // 2,
            scaled.width(),
            scaled.height(),
        )
