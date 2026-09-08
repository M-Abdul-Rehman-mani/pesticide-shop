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
from PySide6.QtGui import QImage, QPageLayout, QPageSize, QPainter
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo
from PySide6.QtWidgets import QDialog, QWidget

from app.utils.exceptions import InfrastructureError

#: Pages are rasterised at the printer's own resolution so glyph edges land on
#: device pixels. Rendering below it and letting the driver scale up is what makes
#: thermal receipts look soft and grey.
#:
#: A roll narrower than this is treated as a receipt printer: its output is reduced
#: to pure black and white rather than left as anti-aliased grey, because a thermal
#: head is a one-bit device and dithering grey produces broken, faint strokes.
THERMAL_PAGE_LIMIT_MM = 90.0

#: Ceiling on the rasterised page, so a driver that still reports a metres-long
#: roll cannot make the application allocate gigabytes for one receipt.
MAX_RENDER_PIXELS = 40_000_000

SYSTEM_DEFAULT_PRINTER = ""


@dataclass(frozen=True, slots=True)
class ReceiptFormat:
    """A page geometry the shop can print invoices on.

    ``width_mm`` is the paper the roll is cut to; ``print_width_mm`` is the narrower
    strip the thermal head can actually mark. A 203 dpi head prints 576 dots on an
    80 mm roll and 384 on a 58 mm roll -- 72 mm and 48 mm -- and the rest of the
    paper is a dead margin. Laying content out across the full paper width is what
    makes the right-hand column disappear on a receipt printer.
    """

    key: str
    label: str
    width_mm: int | None
    print_width_mm: int | None = None

    @property
    def is_thermal(self) -> bool:
        return self.width_mm is not None

    @property
    def side_margin_mm(self) -> float:
        """Blank paper on each side of the printable strip."""

        if self.width_mm is None or self.print_width_mm is None:
            return 0.0
        return (self.width_mm - self.print_width_mm) / 2


A4_FORMAT = ReceiptFormat("A4", "A4", None)
THERMAL_58_FORMAT = ReceiptFormat("58", "58 mm thermal", 58, 48)
THERMAL_80_FORMAT = ReceiptFormat("80", "80 mm thermal", 80, 72)
#: Offered in the order a counter is most likely to want them, so the 80 mm roll
#: is both the default and the first choice in the settings list.
RECEIPT_FORMATS: tuple[ReceiptFormat, ...] = (THERMAL_80_FORMAT, THERMAL_58_FORMAT, A4_FORMAT)
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
        self._verify_page_layout(printer, size_points)
        return printer

    @staticmethod
    def _verify_page_layout(printer: QPrinter, wanted_points: QSizeF) -> None:
        """Fall back to a plain custom size if the driver substituted its own roll.

        Receipt drivers advertise a continuous roll as a single enormous page -- a
        POS-80 reports 72 x 3276 mm. Accepting that feeds metres of blank paper and
        shrinks the receipt to an illegible sliver, so a page that does not match the
        document is replaced with an explicit custom size.
        """

        applied = printer.pageLayout().fullRect(QPageLayout.Unit.Point)
        matches = (
            abs(applied.width() - wanted_points.width()) <= 2.0
            and abs(applied.height() - wanted_points.height()) <= 2.0
        )
        if matches:
            return
        printer.setPageSize(
            QPageSize(
                QSizeF(wanted_points),
                QPageSize.Unit.Point,
                "",
                QPageSize.SizeMatchPolicy.ExactMatch,
            )
        )
        printer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Point)

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
            resolution = max(printer.resolution(), 1)
            thermal = PrinterService.is_thermal_page(printer)
            # Smoothing helps a photographic scale-down but only softens a receipt
            # that is already being drawn at one image pixel per printer dot.
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, not thermal)
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
                if thermal:
                    image = PrinterService._to_bitonal(image)
                painter.drawImage(
                    PrinterService._fitted(target, image.size()),
                    image,
                )
        finally:
            painter.end()

    @staticmethod
    def is_thermal_page(printer: QPrinter) -> bool:
        """Report whether the printer is set to a receipt roll rather than a sheet."""

        width = printer.pageLayout().fullRect(QPageLayout.Unit.Millimeter).width()
        return 0 < width <= THERMAL_PAGE_LIMIT_MM

    @staticmethod
    def _render_size(target: QRect, resolution: int) -> QSize:
        """Rasterise one device pixel per printer dot, within the memory ceiling."""

        width = max(1, target.width())
        height = max(1, target.height())
        if width * height > MAX_RENDER_PIXELS:
            shrink = (MAX_RENDER_PIXELS / (width * height)) ** 0.5
            width = max(1, int(width * shrink))
            height = max(1, int(height * shrink))
        return QSize(width, height)

    @staticmethod
    def _to_bitonal(image: QImage) -> QImage:
        """Flatten a rendered page to solid black on white for a thermal head.

        The head can only burn a dot or not. Handing it grey anti-aliased edges lets
        the driver dither them into scattered dots, which reads as faint, ragged
        text; thresholding here keeps every stroke solid.
        """

        flattened = QImage(image.size(), QImage.Format.Format_RGB32)
        flattened.fill(Qt.GlobalColor.white)
        painter = QPainter(flattened)
        try:
            painter.drawImage(0, 0, image)
        finally:
            painter.end()
        return flattened.convertToFormat(
            QImage.Format.Format_Mono,
            Qt.ImageConversionFlag.MonoOnly | Qt.ImageConversionFlag.ThresholdDither,
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
