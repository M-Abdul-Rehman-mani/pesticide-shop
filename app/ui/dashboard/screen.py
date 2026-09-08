"""Date-filtered dashboard: an overall summary plus one tab per product."""

from __future__ import annotations

import uuid
from datetime import date
from typing import cast

from matplotlib.axes import Axes
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.orm import Session, sessionmaker

from app.reports.report_service import (
    DailyFinancialPoint,
    DashboardMetrics,
    DateRange,
    ProductBatchRow,
    ProductMetrics,
    ProductSummary,
    ReportService,
    TopSellingModel,
)
from app.ui.widgets import (
    MetricCard,
    PageHeader,
    RowsTableModel,
    configure_date_edit,
    configure_searchable_combo,
    configure_table,
    show_error,
)
from app.ui.workers import FunctionWorker, start_worker
from app.utils.formatting import format_date, format_money

_AXIS_LINE = "#d9e4df"
_AXIS_TEXT = "#60736a"
_TITLE_COLOUR = "#25473a"


def _style_axes(*axes_group: Axes) -> None:
    """Apply the shared chart styling to every axes in a figure."""

    for axes in axes_group:
        axes.set_facecolor("#ffffff")
        axes.spines["top"].set_visible(False)
        axes.spines["right"].set_visible(False)
        axes.spines["left"].set_color(_AXIS_LINE)
        axes.spines["bottom"].set_color(_AXIS_LINE)
        axes.tick_params(colors=_AXIS_TEXT, labelsize=8)
        axes.title.set_color(_TITLE_COLOUR)
        axes.title.set_fontsize(10)


def _empty(axes: Axes, message: str) -> None:
    axes.text(0.5, 0.5, message, ha="center", va="center", color=_AXIS_TEXT, fontsize=9)
    axes.set_axis_off()


def _daily_series(
    axes: Axes, rows: list[DailyFinancialPoint], value: str, colour: str, label: str
) -> None:
    """Plot one daily series, or an explanatory placeholder when there is no data."""

    if not rows:
        _empty(axes, f"No {label.lower()} in this period")
        return
    x_values = [float(row.day.toordinal()) for row in rows]
    axes.plot(
        x_values,
        [float(getattr(row, value)) for row in rows],
        marker="o",
        color=colour,
        label=label,
    )
    axes.set_xticks(x_values, [row.day.strftime("%d-%b") for row in rows], rotation=30, ha="right")
    axes.legend(fontsize=8)
    axes.grid(axis="y", alpha=0.25)
    # Money charts read correctly only from zero; a single point otherwise
    # produces an arbitrary zoomed-in band.
    axes.set_ylim(bottom=min(0.0, axes.get_ylim()[0]))


class MetricCardGrid(QWidget):
    """A responsive grid of metric cards that reflows as the screen narrows."""

    def __init__(
        self,
        specifications: tuple[tuple[str, str, str], ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(8)
        self.cards = {
            name: MetricCard(name, hint=hint, accent=accent)
            for name, hint, accent in specifications
        }
        self._columns = 0
        self._reflow(4)

    def _reflow(self, columns: int) -> None:
        if columns == self._columns:
            return
        self._columns = columns
        for index, card in enumerate(self.cards.values()):
            self._grid.addWidget(card, index // columns, index % columns)

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)  # type: ignore[arg-type]
        width = self.width()
        self._reflow(1 if width < 560 else 2 if width < 900 else 3 if width < 1180 else 4)

    def set_value(self, name: str, value: str) -> None:
        self.cards[name].set_value(value)


class ChartCard(QFrame):
    """A titled card that hosts one matplotlib canvas."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MetricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        layout.addWidget(heading)
        self.figure = Figure(figsize=(6, 2.6), tight_layout=True, facecolor="#ffffff")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.canvas.setMinimumHeight(180)
        layout.addWidget(self.canvas, 1)


class OverviewTab(QWidget):
    """Whole-shop figures across every product."""

    SPECIFICATIONS = (
        ("Sales", "Revenue in selected period", "green"),
        ("Profit", "Gross profit after costs", "blue"),
        ("Units Sold", "Packs and units sold", "purple"),
        ("Current Inventory", "Available sellable units", "green"),
        ("Low Stock Products", "At or below reorder level", "amber"),
        ("Expiring in 90 Days", "Units requiring attention", "red"),
        ("Outstanding Payments", "Uncollected sale balance", "amber"),
    )

    def __init__(self, currency: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._currency = currency
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)
        self.cards = MetricCardGrid(self.SPECIFICATIONS)
        self.chart = ChartCard("Performance and inventory insights")
        layout.addWidget(self.cards)
        layout.addWidget(self.chart, 1)

    def display(
        self,
        metrics: DashboardMetrics,
        chart_rows: list[DailyFinancialPoint],
        top_models: list[TopSellingModel],
        inventory_status: dict[str, int],
    ) -> None:
        self.cards.set_value("Sales", format_money(metrics.sales, self._currency))
        self.cards.set_value("Profit", format_money(metrics.profit, self._currency))
        self.cards.set_value("Units Sold", f"{metrics.units_sold:,}")
        self.cards.set_value("Current Inventory", f"{metrics.current_inventory:,}")
        self.cards.set_value("Low Stock Products", f"{metrics.low_stock_products:,}")
        self.cards.set_value("Expiring in 90 Days", f"{metrics.expiring_units:,}")
        self.cards.set_value(
            "Outstanding Payments", format_money(metrics.outstanding_payments, self._currency)
        )
        figure = self.chart.figure
        figure.clear()
        sales_axes = figure.add_subplot(221)
        profit_axes = figure.add_subplot(222)
        top_axes = figure.add_subplot(223)
        stock_axes = figure.add_subplot(224)
        sales_axes.set_title("Sales by day")
        profit_axes.set_title("Gross profit")
        _daily_series(sales_axes, chart_rows, "sales", "#23865a", "Sales")
        _daily_series(profit_axes, chart_rows, "profit", "#4388d6", "Profit")
        top_axes.set_title("Top-selling pesticide products")
        if top_models:
            top_axes.barh(
                [model.product for model in reversed(top_models)],
                [model.units for model in reversed(top_models)],
                color="#71bf96",
            )
            top_axes.tick_params(axis="y", labelsize=7)
        else:
            _empty(top_axes, "No product sales")
        stock_axes.set_title("Inventory status")
        if any(inventory_status.values()):
            stock_axes.bar(
                [key.replace("_", " ").title() for key in inventory_status],
                list(inventory_status.values()),
                color=("#45a979", "#d65f55", "#d6a037"),
            )
            stock_axes.tick_params(axis="x", labelrotation=35, labelsize=7)
        else:
            _empty(stock_axes, "No inventory")
        _style_axes(sales_axes, profit_axes, top_axes, stock_axes)
        self.chart.canvas.draw_idle()


class ProductTab(QWidget):
    """One product's sales, profit, stock, and live batches."""

    SPECIFICATIONS = (
        ("Units Sold", "Packs sold in selected period", "purple"),
        ("Sales", "Revenue from this product", "green"),
        ("Profit", "Gross profit after costs", "blue"),
        ("In Stock", "Available units across batches", "green"),
        ("Active Batches", "Batches currently held", "blue"),
        ("Expiring in 90 Days", "Units requiring attention", "red"),
        ("Stock Value", "Available stock at cost", "amber"),
    )

    def __init__(
        self, product: ProductSummary, currency: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.product = product
        self._currency = currency
        self.loaded = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)
        # The tab label is only the short product name, so the full name -- with
        # formulation and pack size -- is named here.
        self.heading = QLabel(product.display_name)
        self.heading.setObjectName("SectionTitle")
        self.heading.setWordWrap(True)
        self.summary = QLabel("Loading…")
        self.summary.setObjectName("PageSubtitle")
        self.summary.setWordWrap(True)
        self.cards = MetricCardGrid(self.SPECIFICATIONS)
        self.chart = ChartCard("Sales and profit for this product")
        batch_card = QFrame()
        batch_card.setObjectName("MetricCard")
        batch_layout = QVBoxLayout(batch_card)
        batch_layout.setContentsMargins(12, 12, 12, 12)
        batch_heading = QLabel("Live batches")
        batch_heading.setObjectName("SectionTitle")
        self.batch_model = RowsTableModel(
            ("Batch", "Expiry", "Received", "Available", "Purchase", "Selling")
        )
        self.batch_table = QTableView()
        self.batch_table.setModel(self.batch_model)
        configure_table(
            self.batch_table, stretch_column=0, minimum_section_size=70, fit_columns=True
        )
        self.batch_table.setMinimumHeight(120)
        batch_layout.addWidget(batch_heading)
        batch_layout.addWidget(self.batch_table, 1)
        layout.addWidget(self.heading)
        layout.addWidget(self.summary)
        layout.addWidget(self.cards)
        layout.addWidget(self.chart, 3)
        layout.addWidget(batch_card, 2)

    @staticmethod
    def _stock_note(metrics: ProductMetrics) -> str:
        """Describe stock against the reorder level without nonsense at zero.

        A product with no reorder level set cannot be "at or below" it, so saying
        so for every unstocked product is noise.
        """

        if not metrics.minimum_stock:
            return (
                "No stock on hand; no reorder level set."
                if not metrics.in_stock
                else f"Stock {metrics.in_stock:,}; no reorder level set."
            )
        if metrics.in_stock <= metrics.minimum_stock:
            return (
                f"Stock {metrics.in_stock:,} is at or below the reorder level of "
                f"{metrics.minimum_stock:,} — reorder soon."
            )
        return (
            f"Stock {metrics.in_stock:,} is above the reorder level of {metrics.minimum_stock:,}."
        )

    def set_product(self, product: ProductSummary) -> None:
        """Track a renamed or reactivated product without rebuilding the tab."""

        self.product = product
        self.heading.setText(product.display_name)

    def display(
        self,
        metrics: ProductMetrics,
        chart_rows: list[DailyFinancialPoint],
        batches: list[ProductBatchRow],
    ) -> None:
        self.loaded = True
        self.cards.set_value("Units Sold", f"{metrics.units_sold:,}")
        self.cards.set_value("Sales", format_money(metrics.sales, self._currency))
        self.cards.set_value("Profit", format_money(metrics.profit, self._currency))
        self.cards.set_value("In Stock", f"{metrics.in_stock:,}")
        self.cards.set_value("Active Batches", f"{metrics.active_batches:,}")
        self.cards.set_value("Expiring in 90 Days", f"{metrics.expiring_units:,}")
        self.cards.set_value("Stock Value", format_money(metrics.stock_value, self._currency))
        stock_note = self._stock_note(metrics)
        last_sold = (
            f"Last sold {format_date(metrics.last_sold)}."
            if metrics.last_sold
            else "Not sold in this period."
        )
        inactive = "" if self.product.is_active else " This product is deactivated."
        self.summary.setText(f"{stock_note} {last_sold}{inactive}")
        figure = self.chart.figure
        figure.clear()
        sales_axes = figure.add_subplot(121)
        profit_axes = figure.add_subplot(122)
        sales_axes.set_title("Sales by day")
        profit_axes.set_title("Gross profit")
        _daily_series(sales_axes, chart_rows, "sales", "#23865a", "Sales")
        _daily_series(profit_axes, chart_rows, "profit", "#4388d6", "Profit")
        _style_axes(sales_axes, profit_axes)
        self.chart.canvas.draw_idle()
        self.batch_model.set_rows(
            [
                (
                    row.batch_number,
                    format_date(row.expiry_date),
                    f"{row.received:,}",
                    f"{row.available:,}",
                    format_money(row.purchase_price, self._currency),
                    format_money(row.selling_price, self._currency),
                )
                for row in batches
            ]
        )


class DashboardScreen(QWidget):
    """The business overview plus a tab for every product in the catalogue."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        timezone: str,
        currency: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session_factory = session_factory
        self._timezone = timezone
        self._currency = currency
        self._worker: FunctionWorker | None = None
        self._product_tabs: dict[uuid.UUID, ProductTab] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 10)
        layout.setSpacing(10)
        layout.addWidget(
            PageHeader(
                "Business overview",
                "Track revenue, profit, stock health, and outstanding balances for the whole "
                "shop or for one product at a time.",
            )
        )
        filter_bar = QFrame()
        filter_bar.setObjectName("FilterBar")
        heading = QHBoxLayout(filter_bar)
        heading.setContentsMargins(10, 6, 10, 6)
        self.from_date = QDateEdit(QDate.currentDate())
        self.to_date = QDateEdit(QDate.currentDate())
        configure_date_edit(self.from_date, self.to_date)
        refresh = QPushButton("Refresh")
        refresh.setToolTip("Refresh dashboard figures (Ctrl+R)")
        refresh.clicked.connect(self.refresh)
        period_label = QLabel("Reporting period")
        period_label.setObjectName("SectionTitle")
        heading.addWidget(period_label)
        heading.addStretch()
        heading.addWidget(QLabel("From"))
        heading.addWidget(self.from_date)
        heading.addWidget(QLabel("To"))
        heading.addWidget(self.to_date)
        heading.addWidget(refresh)
        layout.addWidget(filter_bar)
        jump_row = QHBoxLayout()
        jump_row.setContentsMargins(2, 0, 2, 0)
        self.product_jump = QComboBox()
        configure_searchable_combo(self.product_jump, "Type a product name to open its tab")
        self.product_jump.setToolTip("Jump straight to a product instead of scrolling the tabs")
        jump_row.addWidget(QLabel("Go to product"))
        jump_row.addWidget(self.product_jump, 1)
        jump_row.addStretch()
        layout.addLayout(jump_row)
        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        # Eliding squeezes a long catalogue's tabs down to "A...", "SE...", which
        # names nothing. Full labels plus scroll buttons stay readable instead.
        self.tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.setDocumentMode(True)
        self.overview = OverviewTab(currency)
        self.tabs.addTab(self.overview, "All Products")
        layout.addWidget(self.tabs, 1)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.product_jump.currentIndexChanged.connect(self._jump_to_product)
        self.from_date.dateChanged.connect(self._period_changed)
        self.to_date.dateChanged.connect(self._period_changed)

    # -- lifecycle -------------------------------------------------------

    def showEvent(self, event: object) -> None:
        super().showEvent(event)  # type: ignore[arg-type]
        if self.overview.cards.cards["Sales"].value_label.text() == "—":
            self.refresh()

    def refresh(self) -> None:
        """Reload the product tab list, the overview, and the visible product tab."""

        self._invalidate_product_tabs()
        self._load_overview()
        self._load_products()

    def _period_changed(self) -> None:
        self._invalidate_product_tabs()

    def _invalidate_product_tabs(self) -> None:
        for tab in self._product_tabs.values():
            tab.loaded = False

    def _tab_changed(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if isinstance(widget, ProductTab) and not widget.loaded:
            self._load_product(widget)

    def _period(self) -> DateRange:
        start = cast(date, self.from_date.date().toPython())
        end = cast(date, self.to_date.date().toPython())
        return DateRange.local_days(start, end, self._timezone)

    # -- overview --------------------------------------------------------

    def _load_overview(self) -> None:
        def operation() -> tuple[
            DashboardMetrics,
            list[DailyFinancialPoint],
            list[TopSellingModel],
            dict[str, int],
        ]:
            period = self._period()
            with self._session_factory() as session:
                service = ReportService(session)
                return (
                    service.dashboard(period),
                    service.financial_by_day(period, self._timezone),
                    service.top_selling_models(period),
                    service.inventory_status(),
                )

        self._worker = start_worker(
            operation,
            succeeded=lambda result: self.overview.display(
                *cast(
                    tuple[
                        DashboardMetrics,
                        list[DailyFinancialPoint],
                        list[TopSellingModel],
                        dict[str, int],
                    ],
                    result,
                )
            ),
            failed=lambda error: show_error(self, error),
        )

    # -- product tabs ----------------------------------------------------

    def _load_products(self) -> None:
        def operation() -> list[ProductSummary]:
            with self._session_factory() as session:
                return ReportService(session).products()

        self._worker = start_worker(
            operation,
            succeeded=self._rebuild_product_tabs,
            failed=lambda error: show_error(self, error),
        )

    def _rebuild_product_tabs(self, result: object) -> None:
        """Add, remove, and rename product tabs to match the current catalogue."""

        products = cast(list[ProductSummary], result)
        current = self.tabs.currentWidget()
        wanted = {product.id: product for product in products}
        for product_id in list(self._product_tabs):
            if product_id not in wanted:
                tab = self._product_tabs.pop(product_id)
                index = self.tabs.indexOf(tab)
                if index >= 0:
                    self.tabs.removeTab(index)
                tab.deleteLater()
        for position, product in enumerate(products, start=1):
            existing = self._product_tabs.get(product.id)
            if existing is None:
                tab = ProductTab(product, self._currency)
                self._product_tabs[product.id] = tab
                self.tabs.insertTab(position, tab, product.name)
            else:
                existing.set_product(product)
                self.tabs.setTabText(self.tabs.indexOf(existing), product.name)
        for product in products:
            tab = self._product_tabs[product.id]
            self.tabs.setTabToolTip(self.tabs.indexOf(tab), product.display_name)
        self._refill_product_jump(products)
        if current is not None and self.tabs.indexOf(current) >= 0:
            self.tabs.setCurrentWidget(current)
        visible = self.tabs.currentWidget()
        if isinstance(visible, ProductTab) and not visible.loaded:
            self._load_product(visible)

    def _refill_product_jump(self, products: list[ProductSummary]) -> None:
        """Rebuild the jump list without letting it fire a tab change."""

        blocked = self.product_jump.blockSignals(True)
        try:
            self.product_jump.clear()
            self.product_jump.addItem("", None)
            for product in products:
                self.product_jump.addItem(product.display_name, product.id)
            self.product_jump.setCurrentIndex(0)
        finally:
            self.product_jump.blockSignals(blocked)

    def _jump_to_product(self) -> None:
        product_id = self.product_jump.currentData()
        tab = self._product_tabs.get(product_id) if product_id else None
        if tab is not None:
            self.tabs.setCurrentWidget(tab)

    def _load_product(self, tab: ProductTab) -> None:
        product_id = tab.product.id

        def operation() -> tuple[ProductMetrics, list[DailyFinancialPoint], list[ProductBatchRow]]:
            period = self._period()
            with self._session_factory() as session:
                service = ReportService(session)
                return (
                    service.product_dashboard(product_id, period),
                    service.product_financial_by_day(product_id, period, self._timezone),
                    service.product_batches(product_id),
                )

        def display(result: object) -> None:
            # The tab can be replaced by a refresh while this query is running.
            if self.tabs.indexOf(tab) < 0:
                return
            tab.display(
                *cast(
                    tuple[ProductMetrics, list[DailyFinancialPoint], list[ProductBatchRow]], result
                )
            )

        self._worker = start_worker(
            operation, succeeded=display, failed=lambda error: show_error(self, error)
        )
