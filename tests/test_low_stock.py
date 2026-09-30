"""One low-stock rule, shown the same way on the dashboard and the product screens."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.product import Product
from app.models.supplier import Supplier
from app.reports.report_service import DateRange, ProductMetrics, ReportService
from app.security.authentication import AuthenticatedUser
from app.ui.dashboard.screen import DashboardScreen, ProductTab
from app.ui.widgets import STOCK_TONES, RowsTableModel, StockCell
from app.utils.stock import StockLevel, stock_level
from tests.test_dashboard import TIMEZONE, _rows, _stock


def _metrics(in_stock: int, minimum: int) -> ProductMetrics:
    return ProductMetrics(
        units_sold=0,
        sales=Decimal("0.00"),
        profit=Decimal("0.00"),
        in_stock=in_stock,
        active_batches=1,
        expiring_units=0,
        minimum_stock=minimum,
        stock_value=Decimal("0.00"),
        last_sold=None,
    )


def test_stock_level_rule() -> None:
    assert stock_level(0, 0) is StockLevel.OUT
    assert stock_level(0, 10) is StockLevel.OUT
    assert stock_level(10, 10) is StockLevel.LOW, "at the reorder level is low"
    assert stock_level(3, 10) is StockLevel.LOW
    assert stock_level(11, 10) is StockLevel.OK
    assert stock_level(3, 0) is StockLevel.OK, "no reorder level set is never low"


def test_low_stock_count_ignores_products_without_a_reorder_level(
    db_session: Session, owner: AuthenticatedUser, supplier: Supplier, product: Product
) -> None:
    """The dashboard count and the per-product flags must agree."""

    unmanaged = Product(
        manufacturer="Test Crop Sciences",
        name="Unmanaged",
        registration_number="REG-UNMANAGED",
        unit="PACK",
        category="HERBICIDE",
        default_purchase_price=Decimal("1.00"),
        default_sale_price=Decimal("2.00"),
        minimum_stock=0,
        is_active=True,
    )
    db_session.add(unmanaged)
    product.minimum_stock = 50
    db_session.flush()
    _stock(db_session, owner, supplier, product, batch_number="LOW-1", quantity=40)

    reports = ReportService(db_session)
    assert [model.product for model in reports.low_stock_models()] == [product.display_name]
    period = DateRange.today(TIMEZONE)
    rows = {row.name: row for row in reports.product_catalogue(period)}
    assert rows[product.name].stock_level is StockLevel.LOW
    assert rows["Unmanaged"].stock_level is StockLevel.OUT
    assert reports.dashboard(period).low_stock_products == 1


@pytest.mark.ui
def test_stock_cells_are_coloured_and_labelled(qtbot: QtBot) -> None:
    model = RowsTableModel(("Stock",))
    model.set_rows([(StockCell(4, StockLevel.LOW),), (StockCell(0, StockLevel.OUT),), (7,)])
    low, out, plain = (model.index(row, 0) for row in range(3))

    assert model.data(low) == "4  ⚠ Low stock"
    assert model.data(out) == "0  ⚠ Out of stock"
    assert model.data(plain) == "7"
    low_text, low_fill = STOCK_TONES[StockLevel.LOW]
    assert model.data(low, Qt.ItemDataRole.ForegroundRole).color().name() == low_text
    assert model.data(low, Qt.ItemDataRole.BackgroundRole).color().name() == low_fill
    assert model.data(plain, Qt.ItemDataRole.BackgroundRole) is None


@pytest.mark.ui
def test_product_dashboard_shows_a_low_stock_badge(qtbot: QtBot, database_engine: Engine) -> None:
    summary = _rows("Alpha")[0].summary
    tab = ProductTab(summary, "PKR")
    qtbot.addWidget(tab)

    tab.display(_metrics(3, 10), [], [])
    assert tab.stock_badge.isVisibleTo(tab)
    assert tab.stock_badge.text() == "⚠ Low stock"

    tab.display(_metrics(0, 10), [], [])
    assert tab.stock_badge.text() == "⚠ Out of stock"

    tab.display(_metrics(50, 10), [], [])
    assert not tab.stock_badge.isVisibleTo(tab)

    tab.display(_metrics(3, 10), [], [])
    tab.reset()
    assert not tab.stock_badge.isVisibleTo(tab), "a reset never leaves the old warning up"


@pytest.mark.ui
def test_product_list_flags_and_counts_low_stock(qtbot: QtBot, database_engine: Engine) -> None:
    factory = sessionmaker[Session](bind=database_engine, expire_on_commit=False, autoflush=False)
    screen = DashboardScreen(factory, TIMEZONE, "PKR")
    qtbot.addWidget(screen)
    healthy, low = _rows("Healthy", "Low")
    low = replace(low, in_stock=2)
    screen._products_loaded([healthy, low])
    products = screen.products

    assert products.model.data(products.model.index(0, 2)) == "12"
    assert products.model.data(products.model.index(1, 2)) == "2  ⚠ Low stock"
    assert "1 low or out of stock" in products.count.text()


def test_inventory_low_stock_filter_compares_product_totals(
    db_session: Session, owner: AuthenticatedUser, supplier: Supplier, product: Product
) -> None:
    """Two batches of 30 against a reorder level of 50 are not low; 60 in all is healthy."""

    product.minimum_stock = 50
    db_session.flush()
    _stock(db_session, owner, supplier, product, batch_number="A", quantity=30)
    _stock(db_session, owner, supplier, product, batch_number="B", quantity=30)
    query = ReportService.low_stock_product_ids()
    assert list(db_session.scalars(query)) == []

    product.minimum_stock = 60
    db_session.flush()
    assert list(db_session.scalars(query)) == [product.id]

    product.is_active = False
    db_session.flush()
    assert list(db_session.scalars(query)) == [], "a deactivated product is never flagged"
