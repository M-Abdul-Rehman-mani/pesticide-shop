"""Per-product dashboard figures and background worker delivery."""

from __future__ import annotations

import gc
import time
import uuid
from datetime import date
from decimal import Decimal

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.customer import Customer
from app.models.enums import PaymentMethod
from app.models.inventory import StockBatch
from app.models.product import Product
from app.models.supplier import Supplier
from app.reports.report_service import (
    DateRange,
    ProductMetrics,
    ProductSummary,
    ReportService,
)
from app.security.authentication import AuthenticatedUser
from app.services.dto import (
    CreatePesticideSaleCommand,
    CreateStockPurchaseCommand,
    PaymentInput,
    PesticideSaleLineInput,
    PurchasedBatchInput,
)
from app.services.pesticide_sale_service import PesticideSaleService
from app.services.stock_purchase_service import StockPurchaseService
from app.ui.dashboard.screen import DashboardScreen, ProductTab
from app.ui.workers import active_worker_count, start_worker

TIMEZONE = "Asia/Karachi"


def _stock(
    session: Session,
    actor: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    *,
    batch_number: str,
    quantity: int = 40,
) -> StockBatch:
    purchase = StockPurchaseService(session).create(
        CreateStockPurchaseCommand(
            supplier_id=supplier.id,
            batches=(
                PurchasedBatchInput(
                    product_id=product.id,
                    batch_number=batch_number,
                    quantity=quantity,
                    purchase_price=Decimal("100.00"),
                    selling_price=Decimal("150.00"),
                    manufacture_date=date(2025, 1, 1),
                    expiry_date=date(2099, 1, 1),
                ),
            ),
            payments=(),
        ),
        actor,
    )
    session.flush()
    batch = session.scalar(select(StockBatch).where(StockBatch.purchase_id == purchase.id))
    assert batch is not None
    return batch


def test_products_are_listed_for_the_dashboard_tabs(db_session: Session, product: Product) -> None:
    reports = ReportService(db_session)
    listed = reports.products()
    assert [summary.id for summary in listed] == [product.id]
    # The tab label is the short name; the full name is kept for tooltips.
    assert listed[0].name == product.name
    assert listed[0].full_name == product.display_name
    assert listed[0].display_name == product.display_name
    assert listed[0].is_active is True

    product.is_active = False
    db_session.flush()
    assert reports.products() == []
    assert [summary.id for summary in reports.products(include_inactive=True)] == [product.id]


def test_product_dashboard_reports_only_that_product(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    other = Product(
        manufacturer="Other Sciences",
        name="Other Herbicide",
        active_ingredient="Atrazine",
        formulation="50% SC",
        pack_size="1-L",
        registration_number="REG-OTHER-1",
        unit="PACK",
        category="HERBICIDE",
        default_purchase_price=Decimal("100.00"),
        default_sale_price=Decimal("150.00"),
        minimum_stock=5,
        is_active=True,
    )
    db_session.add(other)
    db_session.flush()
    tracked = _stock(db_session, owner, supplier, product, batch_number="TRACKED")
    ignored = _stock(db_session, owner, supplier, other, batch_number="IGNORED")
    PesticideSaleService(db_session).create(
        CreatePesticideSaleCommand(
            lines=(
                PesticideSaleLineInput(tracked.id, 4),
                PesticideSaleLineInput(ignored.id, 9),
            ),
            payments=(PaymentInput(PaymentMethod.CASH, Decimal("1950.00")),),
            customer_id=customer.id,
        ),
        owner,
    )
    db_session.flush()

    reports = ReportService(db_session)
    period = DateRange.today(TIMEZONE)
    metrics = reports.product_dashboard(product.id, period)
    assert metrics.units_sold == 4
    assert metrics.sales == Decimal("600.00")
    assert metrics.profit == Decimal("200.00")
    assert metrics.in_stock == 36
    assert metrics.active_batches == 1
    assert metrics.stock_value == Decimal("3600.00")
    assert metrics.minimum_stock == product.minimum_stock
    assert metrics.last_sold is not None

    # The whole-shop dashboard still counts both products.
    assert reports.dashboard(period).units_sold == 13


def test_product_dashboard_is_empty_for_an_unsold_product(
    db_session: Session, product: Product
) -> None:
    metrics = ReportService(db_session).product_dashboard(product.id, DateRange.today(TIMEZONE))
    assert metrics.units_sold == 0
    assert metrics.sales == Decimal("0.00")
    assert metrics.profit == Decimal("0.00")
    assert metrics.in_stock == 0
    assert metrics.active_batches == 0
    assert metrics.last_sold is None


def test_product_daily_series_and_batches(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    batch = _stock(db_session, owner, supplier, product, batch_number="SERIES")
    PesticideSaleService(db_session).create(
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(batch.id, 3),),
            payments=(PaymentInput(PaymentMethod.CASH, Decimal("450.00")),),
            customer_id=customer.id,
        ),
        owner,
    )
    db_session.flush()
    reports = ReportService(db_session)
    series = reports.product_financial_by_day(product.id, DateRange.today(TIMEZONE), TIMEZONE)
    assert len(series) == 1
    assert series[0].sales == Decimal("450.00")
    assert series[0].profit == Decimal("150.00")

    batches = reports.product_batches(product.id)
    assert len(batches) == 1
    assert batches[0].batch_number == "SERIES"
    assert batches[0].received == 40
    assert batches[0].available == 37
    assert batches[0].selling_price == Decimal("150.00")


@pytest.mark.ui
def test_back_to_back_workers_both_deliver_their_results(qapp: QApplication) -> None:
    """A screen that starts two operations must not lose the first one's result."""

    delivered: list[str] = []

    def slow(value: str) -> str:
        time.sleep(0.05)
        return value

    class Screen:
        def run(self) -> None:
            # Mirrors the app-wide pattern of overwriting a single worker attribute.
            self._worker = start_worker(
                lambda: slow("first"),
                succeeded=lambda result: delivered.append(str(result)),
                failed=lambda _error: None,
            )
            self._worker = start_worker(
                lambda: slow("second"),
                succeeded=lambda result: delivered.append(str(result)),
                failed=lambda _error: None,
            )

    Screen().run()
    gc.collect()
    QThreadPool.globalInstance().waitForDone(5000)
    for _ in range(60):
        qapp.processEvents()
    assert sorted(delivered) == ["first", "second"]
    assert active_worker_count() == 0


@pytest.mark.ui
def test_dashboard_builds_a_tab_for_every_product(
    qtbot: QtBot,
    database_engine: Engine,
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
) -> None:
    """The overview stays first and each catalogue product gets its own tab."""

    factory = sessionmaker[Session](bind=database_engine, expire_on_commit=False, autoflush=False)
    screen = DashboardScreen(factory, TIMEZONE, "PKR")
    qtbot.addWidget(screen)
    fixed = ["All Products", "Customers", "Dealers"]
    assert [screen.tabs.tabText(i) for i in range(screen.tabs.count())] == fixed

    names = ["Alpha Product", "Beta Product"]
    screen._rebuild_product_tabs([ProductSummary(uuid.uuid4(), name, True) for name in names])
    assert [screen.tabs.tabText(i) for i in range(screen.tabs.count())] == [*fixed, *names]
    first = screen._FIXED_TABS
    assert all(isinstance(screen.tabs.widget(i), ProductTab) for i in (first, first + 1))

    # A product removed from the catalogue loses its tab; the rest are reused.
    kept = screen.tabs.widget(first)
    assert isinstance(kept, ProductTab)
    screen._rebuild_product_tabs([kept.product])
    assert [screen.tabs.tabText(i) for i in range(screen.tabs.count())] == [
        *fixed,
        "Alpha Product",
    ]
    assert screen.tabs.widget(first) is kept


@pytest.mark.ui
def test_changing_the_period_marks_product_tabs_stale(
    qtbot: QtBot, database_engine: Engine
) -> None:
    factory = sessionmaker[Session](bind=database_engine, expire_on_commit=False, autoflush=False)
    screen = DashboardScreen(factory, TIMEZONE, "PKR")
    qtbot.addWidget(screen)
    screen._rebuild_product_tabs([ProductSummary(uuid.uuid4(), "Gamma Product", True)])
    tab = screen.tabs.widget(screen._FIXED_TABS)
    assert isinstance(tab, ProductTab)
    tab.loaded = True
    screen.from_date.setDate(screen.from_date.date().addDays(-3))
    assert tab.loaded is False


@pytest.mark.ui
def test_product_tabs_stay_readable_for_a_large_catalogue(
    qtbot: QtBot, database_engine: Engine
) -> None:
    """A long catalogue must not elide every tab down to "A...", "SE...".

    Reported from a shop with 23 products: the tab bar named nothing.
    """

    from PySide6.QtCore import Qt

    factory = sessionmaker[Session](bind=database_engine, expire_on_commit=False, autoflush=False)
    screen = DashboardScreen(factory, TIMEZONE, "PKR")
    qtbot.addWidget(screen)
    products = [
        ProductSummary(uuid.uuid4(), f"PRODUCT {index:02d} EC", True, f"PRODUCT {index:02d} EC 1-L")
        for index in range(23)
    ]
    screen._rebuild_product_tabs(products)

    assert screen.tabs.elideMode() == Qt.TextElideMode.ElideNone
    assert screen.tabs.usesScrollButtons() is True
    assert screen.tabs.count() == len(products) + screen._FIXED_TABS
    # Tabs carry the short name; the full one is available on hover.
    first = screen._FIXED_TABS
    assert screen.tabs.tabText(first) == "PRODUCT 00 EC"
    assert screen.tabs.tabToolTip(first) == "PRODUCT 00 EC 1-L"
    assert all(
        "…" not in screen.tabs.tabText(index) and "..." not in screen.tabs.tabText(index)
        for index in range(screen.tabs.count())
    )


@pytest.mark.ui
def test_the_product_search_opens_that_products_tab(qtbot: QtBot, database_engine: Engine) -> None:
    factory = sessionmaker[Session](bind=database_engine, expire_on_commit=False, autoflush=False)
    screen = DashboardScreen(factory, TIMEZONE, "PKR")
    qtbot.addWidget(screen)
    products = [
        ProductSummary(uuid.uuid4(), f"PRODUCT {index}", True, f"PRODUCT {index} 1-L")
        for index in range(6)
    ]
    screen._rebuild_product_tabs(products)
    assert screen.product_jump.count() == len(products) + 1  # a blank entry leads

    wanted = products[4]
    screen.product_jump.setCurrentIndex(screen.product_jump.findData(wanted.id))
    current = screen.tabs.currentWidget()
    assert isinstance(current, ProductTab)
    assert current.product.id == wanted.id
    assert current.heading.text() == wanted.full_name
    assert screen.tabs.tabText(screen.tabs.currentIndex()) == wanted.name


def test_stock_note_does_not_invent_a_reorder_level() -> None:
    """A product with no reorder level cannot be "at or below" it."""

    def note(in_stock: int, minimum: int) -> str:
        return ProductTab._stock_note(
            ProductMetrics(
                units_sold=0,
                sales=Decimal("0.00"),
                profit=Decimal("0.00"),
                in_stock=in_stock,
                active_batches=0,
                expiring_units=0,
                minimum_stock=minimum,
                stock_value=Decimal("0.00"),
                last_sold=None,
            )
        )

    assert note(0, 0) == "No stock on hand; no reorder level set."
    assert note(40, 0) == "Stock 40; no reorder level set."
    assert "reorder soon" in note(5, 20)
    assert "above the reorder level" in note(90, 20)


def test_customer_and_dealer_dashboards_split_the_two_sides(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    """Retail and trade are separate books and must not be mixed."""

    from app.services.catalog_service import DealerService

    dealer = DealerService(db_session).create(
        actor=owner,
        name="Trade Dealer",
        phone="03001234567",
        credit_limit=Decimal("100000.00"),
    )
    db_session.flush()
    batch = _stock(db_session, owner, supplier, product, batch_number="SPLIT", quantity=100)
    PesticideSaleService(db_session).create(
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(batch.id, 4),),
            payments=(PaymentInput(PaymentMethod.CASH, Decimal("600.00")),),
            customer_id=customer.id,
        ),
        owner,
    )
    PesticideSaleService(db_session).create(
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(batch.id, 10),),
            payments=(),
            dealer_id=dealer.id,
        ),
        owner,
    )
    db_session.flush()

    reports = ReportService(db_session)
    period = DateRange.today(TIMEZONE)
    retail = reports.customer_dashboard(period)
    trade = reports.dealer_dashboard(period)

    assert retail.invoices == 1
    assert retail.sales == Decimal("600.00")
    assert retail.outstanding == Decimal("0.00"), "the counter sale was paid"
    assert retail.buyers_in_period == 1

    assert trade.invoices == 1
    assert trade.sales == Decimal("1500.00")
    assert trade.outstanding == Decimal("1500.00")
    assert trade.active_parties == 1
    assert trade.average_sale == Decimal("1500.00")

    # The whole-shop dashboard still counts both.
    assert reports.dashboard(period).units_sold == 14


def test_party_rankings_report_each_buyer(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
) -> None:
    batch = _stock(db_session, owner, supplier, product, batch_number="RANK", quantity=100)
    PesticideSaleService(db_session).create(
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(batch.id, 6),),
            payments=(),
            customer_id=customer.id,
        ),
        owner,
    )
    db_session.flush()

    reports = ReportService(db_session)
    period = DateRange.today(TIMEZONE)
    rows = reports.top_customers(period)
    assert len(rows) == 1
    assert rows[0].name == customer.name
    assert rows[0].invoices == 1
    assert rows[0].units == 6
    assert rows[0].sales == Decimal("900.00")
    assert rows[0].outstanding == Decimal("900.00")
    assert rows[0].last_sold is not None
    assert reports.top_dealers(period) == []


@pytest.mark.ui
def test_the_dashboard_loads_a_party_tab_when_it_is_opened(
    qtbot: QtBot, database_engine: Engine
) -> None:
    factory = sessionmaker[Session](bind=database_engine, expire_on_commit=False, autoflush=False)
    screen = DashboardScreen(factory, TIMEZONE, "PKR")
    qtbot.addWidget(screen)
    assert screen.tabs.tabText(1) == "Customers"
    assert screen.tabs.tabText(2) == "Dealers"
    # Compared as a pair so the checker cannot narrow either flag to a constant.
    assert (screen.customers.loaded, screen.dealers.loaded) == (False, False)

    # Opening a tab is what triggers its query.
    screen.tabs.setCurrentIndex(2)
    QThreadPool.globalInstance().waitForDone(5000)
    for _ in range(40):
        qtbot.wait(1)
    assert (screen.customers.loaded, screen.dealers.loaded) == (False, True), (
        "only the side that was opened should be queried"
    )
