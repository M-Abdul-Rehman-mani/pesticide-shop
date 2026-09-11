"""Barcode lookup at the counter, and duplicate marking on reissued documents."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from pytestqt.qtbot import QtBot
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings
from app.models.customer import Customer
from app.models.enums import PaymentMethod
from app.models.inventory import StockBatch
from app.models.product import Product
from app.models.supplier import Supplier
from app.printing.invoice_documents import build_sale_document
from app.printing.printer_service import PrinterService, write_temporary_pdf
from app.security.authentication import AuthenticatedUser
from app.services.catalog_service import ProductService
from app.services.dto import (
    CreatePesticideSaleCommand,
    CreateStockPurchaseCommand,
    PaymentInput,
    PesticideSaleLineInput,
    PurchasedBatchInput,
)
from app.services.pesticide_sale_service import PesticideSaleService
from app.services.stock_purchase_service import StockPurchaseService
from app.ui.sales.screen import SalesScreen
from app.utils.exceptions import ConflictError, ValidationError
from app.utils.validators import normalize_barcode


def test_barcodes_are_tidied_before_storage() -> None:
    """Scanners add whitespace, and a blank code must not occupy the unique index."""

    assert normalize_barcode("  8964000123456 \n") == "8964000123456"
    assert normalize_barcode("890 123 456") == "890123456"
    assert normalize_barcode("") is None
    assert normalize_barcode("   ") is None
    assert normalize_barcode(None) is None
    with pytest.raises(ValidationError):
        normalize_barcode("9" * 65)


def test_a_barcode_belongs_to_one_product(
    db_session: Session, owner: AuthenticatedUser, product: Product
) -> None:
    service = ProductService(db_session)
    service.update(
        product.id,
        actor=owner,
        manufacturer=product.manufacturer,
        name=product.name,
        category=product.category,
        active_ingredient=product.active_ingredient,
        formulation=product.formulation,
        pack_size=product.pack_size,
        registration_number=product.registration_number,
        unit=product.unit,
        description=product.description,
        default_purchase_price=product.default_purchase_price,
        default_sale_price=product.default_sale_price,
        minimum_stock=product.minimum_stock,
        barcode=" 8964000123456 ",
    )
    db_session.flush()
    assert product.barcode == "8964000123456"

    with pytest.raises(ConflictError, match="barcode"):
        service.create(
            actor=owner,
            manufacturer="Other Maker",
            name="Other Product",
            registration_number="REG-OTHER",
            barcode="8964000123456",
        )


@pytest.fixture
def captured_errors(monkeypatch: pytest.MonkeyPatch) -> list[Exception]:
    """Collect operator-facing errors instead of opening a modal dialog."""

    from app.ui.sales import screen as sales_screen

    raised: list[Exception] = []
    monkeypatch.setattr(sales_screen, "show_error", lambda _parent, error: raised.append(error))
    return raised


def _sales_screen(qtbot: QtBot, engine: Engine, owner: AuthenticatedUser) -> SalesScreen:
    factory = sessionmaker[Session](bind=engine, expire_on_commit=False, autoflush=False)
    screen = SalesScreen(factory, owner, get_settings())
    qtbot.addWidget(screen)
    return screen


@pytest.mark.ui
def test_scanning_a_barcode_adds_that_batch_to_the_invoice(
    qtbot: QtBot,
    database_engine: Engine,
    owner: AuthenticatedUser,
    captured_errors: list[Exception],
) -> None:
    """One scan should be one invoice line.

    The batch list is injected rather than loaded from the database, so the
    screen's own worker thread never contends with this test's connection.
    """

    screen = _sales_screen(qtbot, database_engine, owner)
    scanned, other = uuid.uuid4(), uuid.uuid4()
    screen._choices_loaded(
        (
            [],
            [],
            [
                (scanned, "Test Herbicide", "SCAN-1", 30, Decimal("150.00"), "8964000123456"),
                (other, "Other Product", "SCAN-2", 10, Decimal("90.00"), ""),
            ],
        )
    )
    assert screen._barcodes == {"8964000123456": scanned}

    # A scanner types the code and presses Enter, often with stray whitespace.
    screen.scan.setText("  8964000123456 ")
    screen.scan.returnPressed.emit()
    assert len(screen._cart) == 1
    assert screen._cart[0].batch_id == scanned
    assert screen._cart[0].batch_number == "SCAN-1"
    assert screen.scan.text() == "", "the field clears itself for the next scan"
    assert captured_errors == []

    # Scanning the same pack again is refused rather than silently duplicating it.
    screen.scan.setText("8964000123456")
    screen.scan.returnPressed.emit()
    assert len(screen._cart) == 1
    assert len(captured_errors) == 1


@pytest.mark.ui
def test_an_unknown_barcode_tells_the_operator(
    qtbot: QtBot,
    database_engine: Engine,
    owner: AuthenticatedUser,
    captured_errors: list[Exception],
) -> None:
    screen = _sales_screen(qtbot, database_engine, owner)
    screen._choices_loaded(([], [], []))

    screen.scan.setText("0000000000000")
    screen.scan.returnPressed.emit()
    assert screen._cart == []
    assert len(captured_errors) == 1
    assert "0000000000000" in str(captured_errors[0])

    # An empty scan is simply ignored, with nothing to report.
    screen.scan.setText("   ")
    screen.scan.returnPressed.emit()
    assert screen._cart == []
    assert len(captured_errors) == 1


@pytest.mark.ui
def test_a_reissued_invoice_is_stamped_duplicate(
    qapp: object,
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    """Two apparent originals must not circulate."""

    purchase = StockPurchaseService(db_session).create(
        CreateStockPurchaseCommand(
            supplier_id=supplier.id,
            batches=(
                PurchasedBatchInput(
                    product_id=product.id,
                    batch_number="COPY-1",
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
    assert sale.print_count == 0

    factory = sessionmaker[Session](
        bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
    )
    settings = get_settings()
    service = PrinterService()

    def text_of(document_bytes: bytes, name: str) -> str:
        return service.load(write_temporary_pdf(document_bytes, name)).getAllText(0).text()

    # A preview never counts as issuing a copy.
    preview = build_sale_document(factory, settings, sale.id)
    assert preview.is_duplicate is False
    assert "DUPLICATE" not in text_of(preview.payload, "copy-preview")

    first = build_sale_document(factory, settings, sale.id, record_issue=True)
    assert first.is_duplicate is False
    assert "DUPLICATE" not in text_of(first.payload, "copy-first")

    second = build_sale_document(factory, settings, sale.id, record_issue=True)
    assert second.is_duplicate is True
    assert "DUPLICATE" in text_of(second.payload, "copy-second")

    roll = build_sale_document(factory, settings, sale.id, thermal=True)
    assert "DUPLICATE" in text_of(roll.payload, "copy-roll")

    # The copy is claimed by the database, so the count cannot be lost between a
    # read and a write when two counters print the same invoice at once.
    db_session.refresh(sale)
    assert sale.print_count == 2
    from app.printing.invoice_documents import _claim_copy

    assert [_claim_copy(factory, sale.id) for _ in range(3)] == [3, 4, 5]


@pytest.mark.ui
def test_the_emailed_copy_carries_amounts(qapp: object) -> None:
    """The printed challan has no money; the emailed one must."""

    from app.printing.invoice_generator import InvoiceGenerator
    from app.printing.receipt_generator import ReceiptGenerator, ShopProfile
    from app.printing.sample_receipt import sample_receipt

    shop = ShopProfile(name="AVENEX CROP SCIENCES", currency="PKR")
    receipt = sample_receipt()
    service = PrinterService()

    printed = (
        service.load(
            write_temporary_pdf(ReceiptGenerator().generate_a4(receipt, shop), "print-copy")
        )
        .getAllText(0)
        .text()
    )
    emailed = (
        service.load(write_temporary_pdf(InvoiceGenerator().generate(receipt, shop), "email-copy"))
        .getAllText(0)
        .text()
    )

    assert "TOTAL" not in printed
    assert "TOTAL" in emailed
    assert "Balance" in emailed
    assert "PRODUCT" in emailed, "the emailed copy is still the challan layout"
