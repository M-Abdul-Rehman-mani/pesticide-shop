"""Receipt geometry, printer preferences, and preview behaviour."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QPageSize
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings
from app.models.customer import Customer
from app.models.enums import PaymentMethod, SettingCategory
from app.models.inventory import StockBatch
from app.models.product import Product
from app.models.supplier import Supplier
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
from app.services.dto import (
    CreatePesticideSaleCommand,
    CreateStockPurchaseCommand,
    PaymentInput,
    PesticideSaleLineInput,
    PurchasedBatchInput,
)
from app.services.pesticide_sale_service import PesticideSaleService
from app.services.settings_service import SettingsService
from app.services.stock_purchase_service import StockPurchaseService
from app.utils.exceptions import NotFoundError

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


@pytest.mark.ui
def test_build_sale_document_renders_both_paper_sizes(
    qapp: QApplication,
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    """Any screen can render a saved invoice for A4 or the configured roll."""

    from app.printing.invoice_documents import build_sale_document

    purchase = StockPurchaseService(db_session).create(
        CreateStockPurchaseCommand(
            supplier_id=supplier.id,
            batches=(
                PurchasedBatchInput(
                    product_id=product.id,
                    batch_number="DOC-001",
                    quantity=20,
                    purchase_price=Decimal("100.00"),
                    selling_price=Decimal("150.00"),
                    manufacture_date=date(2025, 1, 1),
                    expiry_date=date(2099, 1, 1),
                ),
            ),
            payments=(),
        ),
        owner,
    )
    db_session.flush()
    batch = db_session.scalar(select(StockBatch).where(StockBatch.purchase_id == purchase.id))
    assert batch is not None
    sale = PesticideSaleService(db_session).create(
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(batch.id, 2),),
            payments=(PaymentInput(PaymentMethod.CASH, Decimal("300.00")),),
            customer_id=customer.id,
        ),
        owner,
    )
    db_session.flush()

    # Bind to the test's own connection so the builder sees this open transaction.
    factory = sessionmaker[Session](
        bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
    )
    settings = get_settings()
    service = PrinterService()

    a4 = build_sale_document(factory, settings, sale.id)
    assert a4.invoice_number == sale.invoice_number
    assert a4.payload.startswith(b"%PDF")
    page = service.load(write_temporary_pdf(a4.payload, "doc-a4")).pagePointSize(0)
    assert page.width() * MILLIMETRES_PER_POINT == pytest.approx(210, abs=1)

    thermal = build_sale_document(factory, settings, sale.id, thermal=True)
    assert thermal.preferences.receipt.width_mm is not None
    page = service.load(write_temporary_pdf(thermal.payload, "doc-roll")).pagePointSize(0)
    assert page.width() * MILLIMETRES_PER_POINT == pytest.approx(
        thermal.preferences.receipt.width_mm, abs=1
    )


@pytest.mark.ui
def test_build_sale_document_reports_a_missing_invoice(
    qapp: QApplication, db_session: Session
) -> None:
    from app.printing.invoice_documents import build_sale_document

    factory = sessionmaker[Session](
        bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
    )
    with pytest.raises(NotFoundError):
        build_sale_document(factory, get_settings(), uuid.uuid4())


@pytest.mark.ui
def test_invoice_history_dialog_offers_the_document_actions(qtbot: QtBot) -> None:
    """A past invoice must be reprintable from a party's history list."""

    from app.ui.documents import InvoiceDocumentActions, InvoiceHistoryDialog

    sale_ids = [uuid.uuid4(), uuid.uuid4()]
    rows = [
        ("INV-2026-000001", "01-Sep-2026", "PKR 100.00", "PKR 100.00", "PKR 0.00", "Paid"),
        ("INV-2026-000002", "02-Sep-2026", "PKR 250.00", "PKR 0.00", "PKR 250.00", "Unpaid"),
    ]
    actions = InvoiceDocumentActions(None, None, None)  # type: ignore[arg-type]
    dialog = InvoiceHistoryDialog("Dealer sales history", rows, sale_ids, actions)
    qtbot.addWidget(dialog)
    assert dialog.model.rowCount() == 2
    labels = [
        dialog.save_button.text(),
        dialog.preview_button.text(),
        dialog.print_button.text(),
    ]
    assert labels == ["Save PDF", "Print Preview", "Print Invoice"]
    # The first row is preselected so the actions are immediately usable.
    assert dialog.selected_sale() == sale_ids[0]
    assert dialog.selected_invoice_number() == "INV-2026-000001"
    assert dialog.preview_button.isEnabled() is True
    dialog.table.selectRow(1)
    assert dialog.selected_sale() == sale_ids[1]


@pytest.mark.ui
def test_invoice_history_dialog_disables_actions_without_invoices(qtbot: QtBot) -> None:
    from app.ui.documents import InvoiceDocumentActions, InvoiceHistoryDialog

    actions = InvoiceDocumentActions(None, None, None)  # type: ignore[arg-type]
    dialog = InvoiceHistoryDialog("Customer purchase history", [], [], actions)
    qtbot.addWidget(dialog)
    assert dialog.selected_sale() is None
    assert dialog.preview_button.isEnabled() is False
    assert dialog.print_button.isEnabled() is False
    assert dialog.save_button.isEnabled() is False
    assert "No invoices found." in dialog.count_label.text()


def test_roll_formats_declare_their_printable_strip() -> None:
    """A thermal head marks less than the paper width; both must be modelled."""

    assert (THERMAL_80_FORMAT.width_mm, THERMAL_80_FORMAT.print_width_mm) == (80, 72)
    assert (THERMAL_58_FORMAT.width_mm, THERMAL_58_FORMAT.print_width_mm) == (58, 48)
    assert THERMAL_80_FORMAT.side_margin_mm == 4.0
    assert THERMAL_58_FORMAT.side_margin_mm == 5.0
    assert A4_FORMAT.print_width_mm is None
    assert A4_FORMAT.side_margin_mm == 0.0


@pytest.mark.ui
@pytest.mark.parametrize(
    ("paper_mm", "printable_mm"),
    [(80, 72), (58, 48), (80, 64), (58, 42)],
)
def test_receipt_ink_stays_inside_the_printable_strip(
    qapp: QApplication, paper_mm: int, printable_mm: int
) -> None:
    """Nothing may be laid out in the dead margin the print head cannot reach."""

    from PySide6.QtCore import QSize
    from PySide6.QtGui import QColor, QImage, QPainter

    payload = ReceiptGenerator().generate_thermal(
        sample_receipt(),
        ShopProfile(name="Green Valley Crop Care", address="Main Bazaar Road, Multan"),
        paper_mm,
        printable_mm,
    )
    document = PrinterService().load(
        write_temporary_pdf(payload, f"strip-{paper_mm}-{printable_mm}")
    )
    points = document.pagePointSize(0)
    assert points.width() * MILLIMETRES_PER_POINT == pytest.approx(paper_mm, abs=0.5)

    dots_per_mm = 8  # 203 dpi thermal head
    width_px = round(points.width() * MILLIMETRES_PER_POINT * dots_per_mm)
    height_px = round(points.height() * MILLIMETRES_PER_POINT * dots_per_mm)
    flattened = QImage(width_px, height_px, QImage.Format.Format_RGB32)
    flattened.fill(QColor("white"))
    painter = QPainter(flattened)
    painter.drawImage(0, 0, document.render(0, QSize(width_px, height_px)))
    painter.end()

    left, right = width_px, -1
    for y in range(height_px):
        for x in range(width_px):
            if QColor(flattened.pixel(x, y)).value() < 160:
                left = min(left, x)
                right = max(right, x)
    assert right >= 0, "the receipt rendered blank"
    margin = (paper_mm - printable_mm) / 2
    assert left / dots_per_mm >= margin - 0.5
    assert (right + 1) / dots_per_mm <= paper_mm - margin + 0.5


def test_thermal_generation_rejects_an_impossible_printable_width() -> None:
    generator = ReceiptGenerator()
    shop = ShopProfile(name="Test")
    with pytest.raises(ValueError, match="fit inside the paper"):
        generator.generate_thermal(sample_receipt(), shop, 80, 90)
    with pytest.raises(ValueError, match="fit inside the paper"):
        generator.generate_thermal(sample_receipt(), shop, 80, 0)
    with pytest.raises(ValueError, match="58 mm or 80 mm"):
        generator.generate_thermal(sample_receipt(), shop, 72)


@pytest.mark.ui
def test_alignment_test_page_matches_the_roll(qapp: QApplication) -> None:
    from app.printing.alignment_test import alignment_test_page

    document = PrinterService().load(write_temporary_pdf(alignment_test_page(80, 72), "align"))
    assert document.pageCount() == 1
    size = document.pagePointSize(0)
    assert size.width() * MILLIMETRES_PER_POINT == pytest.approx(80, abs=0.5)
    with pytest.raises(ValueError):
        alignment_test_page(80, 90)


def test_print_preferences_override_the_standard_printable_width() -> None:
    from app.printing.preferences import PrintPreferences

    default = PrintPreferences(receipt=THERMAL_80_FORMAT)
    assert default.effective_print_width_mm == 72
    narrowed = PrintPreferences(receipt=THERMAL_80_FORMAT, print_width_mm=64)
    assert narrowed.effective_print_width_mm == 64
    assert PrintPreferences(receipt=A4_FORMAT).effective_print_width_mm is None


def test_printable_width_setting_is_validated(
    db_session: Session, owner: AuthenticatedUser
) -> None:
    from app.utils.exceptions import ValidationError

    service = SettingsService(db_session, get_settings().app_secret_key.get_secret_value())
    service.set(actor=owner, category=SettingCategory.PRINTER, key="print_width", value="64")
    db_session.flush()
    assert load_print_preferences(db_session, get_settings()).print_width_mm == 64
    for invalid in ("0", "120", "wide"):
        with pytest.raises(ValidationError):
            service.set(
                actor=owner,
                category=SettingCategory.PRINTER,
                key="print_width",
                value=invalid,
            )


@pytest.mark.ui
def test_a_continuous_roll_page_is_replaced_with_the_receipt_size(qapp: QApplication) -> None:
    """A POS-80 advertises its roll as 72 x 3276 mm; accepting that wastes metres.

    Reported from a Windows 10 counter: the preview showed "Custom (72 x 3276 mm)",
    the receipt shrank to an illegible sliver, and the printer fed a huge length of
    blank paper.
    """

    from PySide6.QtCore import QMarginsF, QSizeF
    from PySide6.QtGui import QPageLayout, QPageSize

    service = PrinterService()
    document = service.load(
        write_temporary_pdf(
            ReceiptGenerator().generate_thermal(sample_receipt(), ShopProfile(name="Shop"), 80),
            "roll-substitution",
        )
    )
    wanted = document.pagePointSize(0)
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setPageSize(
        QPageSize(
            QSizeF(72 * 72 / 25.4, 3276 * 72 / 25.4),
            QPageSize.Unit.Point,
            "roll",
            QPageSize.SizeMatchPolicy.ExactMatch,
        )
    )
    assert printer.pageLayout().fullRect(QPageLayout.Unit.Millimeter).height() == pytest.approx(
        3276, abs=1
    )

    service.prepare(document, printer)

    applied = printer.pageLayout().fullRect(QPageLayout.Unit.Millimeter)
    assert applied.width() == pytest.approx(80, abs=1)
    assert applied.height() == pytest.approx(wanted.height() * MILLIMETRES_PER_POINT, abs=1)
    assert applied.height() < 300, "the receipt must not span a continuous roll"
    assert printer.pageLayout().margins() == QMarginsF(0, 0, 0, 0)


@pytest.mark.ui
def test_render_size_is_capped_for_an_absurd_page(qapp: QApplication) -> None:
    """Even a metres-long page must not allocate an unbounded raster."""

    from PySide6.QtCore import QPoint, QRect, QSize

    from app.printing.printer_service import MAX_RENDER_PIXELS

    huge = QRect(QPoint(0, 0), QSize(850, 38_700))
    rendered = PrinterService._render_size(huge, 203)
    assert rendered.width() * rendered.height() <= MAX_RENDER_PIXELS
    modest = PrinterService._render_size(QRect(QPoint(0, 0), QSize(640, 900)), 203)
    assert (modest.width(), modest.height()) == (640, 900)
