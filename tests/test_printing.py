"""Receipt geometry, printer preferences, and preview behaviour."""

from __future__ import annotations

import uuid
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QPageSize
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings
from app.models.enums import SettingCategory
from app.printing.preferences import load_print_preferences
from app.printing.print_preview import PrintPreviewDialog
from app.printing.printer_service import (
    A4_FORMAT,
    DEFAULT_RECEIPT_FORMAT,
    THERMAL_58_FORMAT,
    THERMAL_80_FORMAT,
    PrinterService,
    receipt_format,
    write_temporary_pdf,
)
from app.printing.receipt_generator import ReceiptGenerator, ReceiptLine, ShopProfile
from app.printing.sample_receipt import sample_receipt
from app.security.authentication import AuthenticatedUser
from app.services.settings_service import SettingsService

MILLIMETRES_PER_POINT = 25.4 / 72


def _thermal_pdf(width_mm: int) -> bytes:
    return ReceiptGenerator().generate_thermal(
        sample_receipt(), ShopProfile(name="Test Shop"), width_mm
    )


def test_receipt_format_defaults_to_eighty_millimetre_thermal() -> None:
    assert DEFAULT_RECEIPT_FORMAT is THERMAL_80_FORMAT
    assert receipt_format("80") is THERMAL_80_FORMAT
    assert receipt_format("58") is THERMAL_58_FORMAT
    assert receipt_format("A4") is A4_FORMAT
    assert receipt_format("a4") is A4_FORMAT
    assert receipt_format(None) is DEFAULT_RECEIPT_FORMAT
    assert receipt_format("nonsense") is DEFAULT_RECEIPT_FORMAT
    assert A4_FORMAT.is_thermal is False
    assert THERMAL_80_FORMAT.is_thermal is True


@pytest.mark.ui
def test_thermal_receipt_is_generated_at_the_requested_roll_width(tmp_path: Path) -> None:
    path = write_temporary_pdf(_thermal_pdf(80), "eighty-millimetre-receipt")
    document = PrinterService().load(path)
    assert document.pageCount() >= 1
    width_mm = document.pagePointSize(0).width() * MILLIMETRES_PER_POINT
    assert width_mm == pytest.approx(80, abs=0.5)


@pytest.mark.ui
def test_printer_page_layout_matches_the_generated_receipt(qapp: QApplication) -> None:
    """An 80 mm receipt must not be centred on an A4 sheet by the driver."""

    service = PrinterService()
    document = service.load(write_temporary_pdf(_thermal_pdf(80), "layout-check"))
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    service.prepare(document, printer)
    layout = printer.pageLayout()
    size = layout.pageSize().size(QPageSize.Unit.Millimeter)
    assert size.width() == pytest.approx(80, abs=0.5)
    assert layout.margins().left() == 0
    assert printer.fullPage() is True


@pytest.mark.ui
def test_painting_a_receipt_produces_a_printable_document(
    qapp: QApplication, tmp_path: Path
) -> None:
    """Exercise the real paint path through Qt's PDF printer backend."""

    service = PrinterService()
    document = service.load(write_temporary_pdf(_thermal_pdf(80), "paint-check"))
    output = tmp_path / "printed.pdf"
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(output))
    service.prepare(document, printer)
    service.paint(document, printer)
    assert output.is_file()
    assert output.stat().st_size > 0
    printed = QPdfDocument()
    assert printed.load(str(output)) is QPdfDocument.Error.None_
    assert printed.pageCount() == document.pageCount()
    assert printed.pagePointSize(0).width() == pytest.approx(
        document.pagePointSize(0).width(), abs=1.0
    )


def test_resolve_printer_name_ignores_a_printer_that_is_not_installed(
    qapp: QApplication,
) -> None:
    service = PrinterService()
    system_default = service.system_default_printer()
    assert service.resolve_printer_name("no-such-printer-9d3f") == system_default
    assert service.resolve_printer_name(None) == system_default
    assert service.resolve_printer_name("   ") == system_default


def test_temporary_pdfs_share_one_cleaned_up_directory() -> None:
    first = write_temporary_pdf(b"%PDF-1.4 first", "invoice/INV 001")
    second = write_temporary_pdf(b"%PDF-1.4 second", "invoice-002")
    assert first.parent == second.parent
    assert first.name == "invoice-INV-001.pdf"
    assert first.read_bytes() == b"%PDF-1.4 first"


def test_print_preferences_follow_the_saved_printer_settings(
    db_session: Session, owner: AuthenticatedUser
) -> None:
    settings = get_settings()
    service = SettingsService(db_session, settings.app_secret_key.get_secret_value())
    for key, value in (("default_printer", "Counter Thermal"), ("receipt_width", "80")):
        service.set(actor=owner, category=SettingCategory.PRINTER, key=key, value=value)
    db_session.flush()
    preferences = load_print_preferences(db_session, settings)
    assert preferences.printer_name == "Counter Thermal"
    assert preferences.receipt is THERMAL_80_FORMAT


def test_print_preferences_fall_back_when_nothing_is_saved(db_session: Session) -> None:
    preferences = load_print_preferences(db_session, get_settings())
    assert preferences.printer_name == ""
    assert preferences.receipt is DEFAULT_RECEIPT_FORMAT


@pytest.mark.ui
def test_preview_dialog_offers_print_and_save_actions(qtbot: QtBot) -> None:
    path = write_temporary_pdf(_thermal_pdf(80), "preview-actions")
    dialog = PrintPreviewDialog(path, printer_name=None, suggested_filename="INV-1.pdf")
    qtbot.addWidget(dialog)
    assert dialog.print_button.text() == "Print PDF"
    assert dialog.save_button.text() == "Save PDF"
    assert dialog.printer_selector.count() >= 1
    assert dialog.printer_selector.itemData(0) == ""
    assert "1 page" in dialog.page_label.text()
    assert "80 x" in dialog.page_label.text()


@pytest.mark.ui
def test_preview_relays_out_when_the_selected_printer_changes(qtbot: QtBot) -> None:
    """Choosing another printer must rebuild the preview for that device's paper."""

    path = write_temporary_pdf(_thermal_pdf(58), "preview-switch")
    dialog = PrintPreviewDialog(path)
    qtbot.addWidget(dialog)
    original = dialog.preview
    dialog.printer_selector.setCurrentIndex(dialog.printer_selector.count() - 1)
    if dialog.printer_selector.count() > 1:
        assert dialog.preview is not original
    assert dialog.selected_printer() == (dialog.printer_selector.currentData() or "")
    assert "58 x" in dialog.page_label.text()


@pytest.mark.ui
def test_sales_screen_exposes_invoice_document_actions(
    qtbot: QtBot, database_engine: Engine, owner: AuthenticatedUser
) -> None:
    """The counter needs save, preview, and direct thermal printing on one row."""

    from app.ui.sales.screen import SalesScreen

    factory = sessionmaker[Session](bind=database_engine, expire_on_commit=False, autoflush=False)
    screen = SalesScreen(factory, owner, get_settings())
    qtbot.addWidget(screen)
    assert screen.print_invoice.text() == "Print Invoice"
    for button in (screen.save_pdf, screen.preview, screen.print_invoice):
        assert button.isEnabled() is False
    screen._select_invoice(uuid.uuid4(), "INV-2026-0007")
    for button in (screen.save_pdf, screen.preview, screen.print_invoice):
        assert button.isEnabled() is True
    QThreadPool.globalInstance().waitForDone(5000)


@pytest.mark.parametrize("width_mm", [58, 80])
@pytest.mark.parametrize("line_count", [0, 1, 2, 10, 60])
def test_thermal_receipts_always_fit_a_single_roll_page(
    qapp: QApplication, width_mm: int, line_count: int
) -> None:
    """A roll page is sized to its content, so nothing spills onto a second page."""

    lines = tuple(
        ReceiptLine(
            product=f"Sample Product Number {index}",
            batch_number=f"BATCH-{index:04d}",
            quantity=3,
            price=Decimal("1250.00"),
            discount=Decimal("0.00"),
            total=Decimal("3750.00"),
        )
        for index in range(line_count)
    )
    receipt = replace(sample_receipt(), lines=lines)
    shop = ShopProfile(name="Green Valley Crop Care", address="Main Bazaar Road\nMultan")
    payload = ReceiptGenerator().generate_thermal(receipt, shop, width_mm)
    document = PrinterService().load(write_temporary_pdf(payload, f"fit-{width_mm}-{line_count}"))
    assert document.pageCount() == 1
    size = document.pagePointSize(0)
    assert size.width() * MILLIMETRES_PER_POINT == pytest.approx(width_mm, abs=0.5)
    assert size.height() > 0


def test_thermal_receipt_grows_with_its_content(qapp: QApplication) -> None:
    shop = ShopProfile(name="Green Valley Crop Care")
    generator = ReceiptGenerator()
    service = PrinterService()

    def height(line_count: int) -> float:
        lines = tuple(
            ReceiptLine(
                product=f"Product {index}",
                batch_number=f"B{index}",
                quantity=1,
                price=Decimal("10.00"),
                discount=Decimal("0.00"),
                total=Decimal("10.00"),
            )
            for index in range(line_count)
        )
        payload = generator.generate_thermal(replace(sample_receipt(), lines=lines), shop, 80)
        document = service.load(write_temporary_pdf(payload, f"grow-{line_count}"))
        return document.pagePointSize(0).height() * MILLIMETRES_PER_POINT

    short, long = height(1), height(12)
    assert long > short
    # A short receipt must not consume a long strip of roll.
    assert short < 110
