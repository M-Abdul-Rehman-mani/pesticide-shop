from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import PaymentMethod
from app.models.inventory import StockBatch
from app.models.product import Product
from app.models.supplier import Supplier
from app.printing.receipt_generator import ReceiptGenerator, SaleReceiptData, ShopProfile
from app.reports.excel_exporter import ExcelExporter
from app.reports.pdf_exporter import PDFReportExporter
from app.reports.report_service import DateRange, ReportService
from app.security.authentication import AuthenticatedUser
from app.services.catalog_service import (
    CustomerService,
    DealerService,
    ProductService,
    SupplierService,
)
from app.services.dto import (
    CreatePesticideSaleCommand,
    CreateStockPurchaseCommand,
    PaymentInput,
    PesticideSaleLineInput,
    PurchasedBatchInput,
)
from app.services.payment_service import PaymentService
from app.services.pesticide_sale_service import PesticideSaleService
from app.services.stock_inventory_service import StockInventoryService
from app.services.stock_purchase_service import StockPurchaseService
from app.utils.exceptions import ConflictError, ValidationError


def _create_batch(
    session: Session,
    actor: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    *,
    quantity: int = 10,
) -> tuple[object, StockBatch]:
    purchase = StockPurchaseService(session).create(
        CreateStockPurchaseCommand(
            supplier_id=supplier.id,
            batches=(
                PurchasedBatchInput(
                    product_id=product.id,
                    batch_number="EXT-2608",
                    quantity=quantity,
                    purchase_price=Decimal("100.00"),
                    selling_price=Decimal("150.00"),
                    manufacture_date=date(2026, 1, 1),
                    expiry_date=date(2028, 1, 1),
                    cartons=1,
                    packs_per_carton=quantity,
                ),
            ),
            payments=(PaymentInput(PaymentMethod.CASH, Decimal("200.00")),),
        ),
        actor,
    )
    session.flush()
    batch = session.scalar(select(StockBatch).where(StockBatch.purchase_id == purchase.id))
    assert batch is not None
    return purchase, batch


def test_catalog_crud_and_audited_price_changes(
    db_session: Session, owner: AuthenticatedUser
) -> None:
    customer_service = CustomerService(db_session)
    customer = customer_service.create(
        actor=owner,
        name=" Grower One ",
        phone="0300-2222222",
        email="GROWER@EXAMPLE.INVALID",
        cnic="11111-1111111-1",
    )
    customer_service.update(
        customer.id,
        actor=owner,
        name="Grower Updated",
        phone="0300-2222222",
        email="updated@example.invalid",
    )
    assert customer.name == "Grower Updated"

    dealer_service = DealerService(db_session)
    dealer = dealer_service.create(
        actor=owner,
        name="Dealer Contact",
        business_name="Green Fields Store",
        phone="0300-3333333",
        email="dealer@example.invalid",
        territory="HSP",
        credit_limit=Decimal("5000.00"),
    )
    dealer_service.update(
        dealer.id,
        actor=owner,
        name="Dealer Contact",
        business_name="Green Fields Center",
        phone="0300-3333333",
        territory="Bahawalpur",
        credit_limit=Decimal("7000.00"),
    )
    assert dealer.display_name == "Green Fields Center"

    supplier_service = SupplierService(db_session)
    supplier = supplier_service.create(
        actor=owner,
        name="Supply Contact",
        company_name="Crop Supply Co",
        phone="0300-4444444",
        tax_number="TAX-EXT-1",
    )
    supplier_service.update(
        supplier.id,
        actor=owner,
        name="Supply Manager",
        company_name="Crop Supply Co",
        phone="0300-4444444",
        email="supply@example.invalid",
        tax_number="TAX-EXT-1",
    )
    supplier_service.set_active(supplier.id, actor=owner, is_active=False)
    assert not supplier.is_active

    product_service = ProductService(db_session)
    product = product_service.create(
        actor=owner,
        manufacturer="Crop Sciences",
        name="Weed Control",
        active_ingredient="Active X",
        formulation="20% EC",
        pack_size="1-L",
        category="herbicide",
        registration_number="REG-EXT-1",
        default_purchase_price=Decimal("500.00"),
        default_sale_price=Decimal("650.00"),
        minimum_stock=5,
    )
    product_service.change_prices(
        product.id,
        actor=owner,
        purchase_price=Decimal("525.00"),
        sale_price=Decimal("675.00"),
    )
    assert product.display_name == "Weed Control 20% EC 1-L"
    assert product.default_sale_price == Decimal("675.00")

    with pytest.raises(ValidationError):
        product_service.create(actor=owner, manufacturer="", name="Missing")


def test_payments_inventory_and_reports_cover_full_trade_cycle(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    purchase, batch = _create_batch(db_session, owner, supplier, product)
    sale = PesticideSaleService(db_session).create(
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(batch.id, 2),),
            payments=(PaymentInput(PaymentMethod.CASH, Decimal("100.00")),),
        ),
        owner,
    )
    db_session.flush()

    payments = PaymentService(db_session)
    incoming = payments.collect_sale_payment(
        sale.id,
        actor=owner,
        method=PaymentMethod.BANK_TRANSFER,
        amount=Decimal("200.00"),
        reference="BANK-1",
    )
    outgoing = payments.pay_supplier(
        purchase.id,  # type: ignore[attr-defined]
        actor=owner,
        method=PaymentMethod.BANK_TRANSFER,
        amount=Decimal("800.00"),
    )
    assert incoming.amount == Decimal("200.00")
    assert outgoing.amount == Decimal("800.00")
    assert sale.remaining_amount == Decimal("0.00")
    assert purchase.remaining_amount == Decimal("0.00")  # type: ignore[attr-defined]

    StockInventoryService(db_session).adjust(
        batch.id, quantity=5, reason="Verified physical stock", actor=owner
    )
    db_session.flush()
    assert batch.quantity_available == 5
    with pytest.raises(ConflictError):
        StockInventoryService(db_session).adjust(
            batch.id, quantity=5, reason="No change", actor=owner
        )

    period = DateRange.today("Asia/Karachi")
    reports = ReportService(db_session)
    metrics = reports.dashboard(period)
    assert metrics.sales == Decimal("300.00")
    assert metrics.profit == Decimal("100.00")
    assert metrics.units_sold == 2
    assert reports.sales(period)[0].batch == "EXT-2608"
    assert reports.sales_by_day(period, "Asia/Karachi")
    assert reports.sales_by_month(period, "Asia/Karachi")
    assert reports.financial_by_day(period, "Asia/Karachi")[0].profit == Decimal("100.00")
    assert reports.top_selling_models(period)[0].units == 2
    assert reports.inventory_status()["IN_STOCK"] == 5
    assert reports.payment_breakdown(period)["CASH"] == Decimal("100.00")


def test_invoice_and_report_exports(
    tmp_path: object,
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    from pathlib import Path

    directory = Path(str(tmp_path))
    _purchase, batch = _create_batch(db_session, owner, supplier, product)
    sale = PesticideSaleService(db_session).create(
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(batch.id, 1),),
            payments=(PaymentInput(PaymentMethod.CASH, Decimal("150.00")),),
        ),
        owner,
    )
    db_session.flush()

    receipt = SaleReceiptData.from_sale(sale, "CASH")
    shop = ShopProfile(name="Hanan Spray Center", currency="PKR")
    assert ReceiptGenerator().generate_a4(receipt, shop).startswith(b"%PDF")
    assert ReceiptGenerator().generate_thermal(receipt, shop, 58).startswith(b"%PDF")
    with pytest.raises(ValueError):
        ReceiptGenerator().generate_thermal(receipt, shop, 72)

    excel_path = ExcelExporter().export(
        directory / "sales.xlsx",
        title="Sales",
        headers=("Invoice", "Total"),
        rows=((sale.invoice_number, sale.total),),
        summary=("Grand Total", sale.total),
        currency_columns=frozenset({2}),
    )
    workbook = load_workbook(excel_path)
    sheet = workbook.active
    assert sheet is not None
    assert sheet["A3"].value == sale.invoice_number

    pdf_path = PDFReportExporter().export(
        directory / "sales.pdf",
        title="Sales",
        headers=("Invoice", "Total"),
        rows=((sale.invoice_number, sale.total),),
        subtitle="Daily report",
        landscape_page=False,
    )
    assert pdf_path.read_bytes().startswith(b"%PDF")
