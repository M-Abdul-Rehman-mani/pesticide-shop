"""Date-filtered dashboard: the whole shop, its customers, its dealers, its products."""

from __future__ import annotations

from datetime import date
from typing import cast

from matplotlib.axes import Axes
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QDate, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
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
    PartyMetrics,
    PartyRow,
    ProductBatchRow,
    ProductMetrics,
    ProductRow,
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
    record_count_text,
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


class PartyTab(QWidget):
    """Customer- or dealer-side figures, with a ranking table beneath them."""

    def __init__(
        self,
        title: str,
        specifications: tuple[tuple[str, str, str], ...],
        party_label: str,
        currency: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._currency = currency
        self.loaded = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)
        self.cards = MetricCardGrid(specifications)
        table_card = QFrame()
        table_card.setObjectName("MetricCard")
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(12, 12, 12, 12)
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        self.model = RowsTableModel(
            (party_label, "Contact", "Invoices", "Units", "Sales", "Outstanding", "Last Sale")
        )
        self.table = QTableView()
        self.table.setModel(self.model)
        configure_table(self.table, stretch_column=0, minimum_section_size=74)
        self.count_label = QLabel()
        self.count_label.setObjectName("RecordCount")
        table_layout.addWidget(heading)
        table_layout.addWidget(self.table, 1)
        table_layout.addWidget(self.count_label)
        layout.addWidget(self.cards)
        layout.addWidget(table_card, 1)

    def display(self, metrics: PartyMetrics, rows: list[PartyRow]) -> None:
        self.loaded = True
        self.cards.set_value("Sales", format_money(metrics.sales, self._currency))
        self.cards.set_value("Profit", format_money(metrics.profit, self._currency))
        self.cards.set_value("Invoices", f"{metrics.invoices:,}")
        self.cards.set_value("Average Sale", format_money(metrics.average_sale, self._currency))
        self.cards.set_value("Bought in Period", f"{metrics.buyers_in_period:,}")
        self.cards.set_value("On the Books", f"{metrics.active_parties:,}")
        self.cards.set_value("Outstanding", format_money(metrics.outstanding, self._currency))
        self.model.set_rows(
            [
                (
                    row.name,
                    row.contact or "—",
                    f"{row.invoices:,}",
                    f"{row.units:,}",
                    format_money(row.sales, self._currency),
                    format_money(row.outstanding, self._currency),
                    format_date(row.last_sold),
                )
                for row in rows
            ]
        )
        self.count_label.setText(record_count_text(len(rows), "buyer"))


CUSTOMER_SPECIFICATIONS = (
    ("Sales", "Revenue from customers", "green"),
    ("Profit", "Gross profit after costs", "blue"),
    ("Invoices", "Counter sales in the period", "purple"),
    ("Average Sale", "Revenue per invoice", "blue"),
    ("Bought in Period", "Customers who bought", "green"),
    ("On the Books", "Customers on record", "green"),
    ("Outstanding", "Uncollected customer balance", "amber"),
)

DEALER_SPECIFICATIONS = (
    ("Sales", "Revenue from dealers", "green"),
    ("Profit", "Gross profit after costs", "blue"),
    ("Invoices", "Trade sales in the period", "purple"),
    ("Average Sale", "Revenue per invoice", "blue"),
    ("Bought in Period", "Dealers who bought", "green"),
    ("On the Books", "Active dealer accounts", "green"),
    ("Outstanding", "Owed across dealer accounts", "amber"),
)


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
        """Point the view at another product, clearing the previous one's figures."""

        changed = product.id != self.product.id
        self.product = product
        self.heading.setText(product.display_name)
        if changed:
            self.reset()

    def reset(self) -> None:
        """Blank the view so one product's figures never stand under another's name."""

        self.loaded = False
        for name, *_rest in self.SPECIFICATIONS:
            self.cards.set_value(name, "—")
        self.summary.setText("Loading…")
        self.batch_model.set_rows([])
        self.chart.figure.clear()
        self.chart.canvas.draw_idle()

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


class ProductsTab(QWidget):
    """The catalogue as a list, with one product's dashboard behind it."""

    product_opened = Signal(object)

    COLUMNS = (
        "Product",
        "Manufacturer",
        "In Stock",
        "Batches",
        "Units Sold",
        "Sales",
        "Profit",
        "Last Sold",
        "Status",
    )

    def __init__(self, currency: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._currency = currency
        self._rows: list[ProductRow] = []
        self._visible: list[ProductRow] = []
        self.loaded = False
        self.detail: ProductTab | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_list())
        layout.addWidget(self.stack, 1)

    def _build_list(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search product or manufacturer")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        search_row.addWidget(QLabel("Search"))
        search_row.addWidget(self.search, 1)
        self.model = RowsTableModel(self.COLUMNS, page)
        self.table = QTableView()
        self.table.setModel(self.model)
        configure_table(self.table, stretch_column=0, minimum_section_size=76)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.clicked.connect(self._row_chosen)
        self.count = QLabel("Loading products…")
        self.count.setObjectName("RecordCount")
        hint = QLabel("Click a product to open its own dashboard.")
        hint.setObjectName("FieldHint")
        layout.addLayout(search_row)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.count)
        layout.addWidget(hint)
        return page

    # -- list ------------------------------------------------------------

    def display(self, rows: list[ProductRow]) -> None:
        self.loaded = True
        self._rows = rows
        self._filter()
        current = self.current_product
        if current is not None:
            # Keep the open product's name in step with a rename or a deactivation.
            for row in rows:
                if row.id == current.id:
                    self.set_product(row.summary)
                    break

    def _filter(self) -> None:
        query = self.search.text().strip().lower()
        self._visible = [
            row
            for row in self._rows
            if not query or query in row.name.lower() or query in row.manufacturer.lower()
        ]
        self.model.set_rows(
            [
                (
                    row.full_name,
                    row.manufacturer,
                    f"{row.in_stock:,}" + (" — reorder" if row.needs_reorder else ""),
                    f"{row.active_batches:,}",
                    f"{row.units_sold:,}",
                    format_money(row.sales, self._currency),
                    format_money(row.profit, self._currency),
                    format_date(row.last_sold),
                    "Active" if row.is_active else "Inactive",
                )
                for row in self._visible
            ]
        )
        self.count.setText(record_count_text(len(self._visible), "product"))

    def _row_chosen(self, index: QModelIndex) -> None:
        row = index.row()
        if 0 <= row < len(self._visible):
            self.open_product(self._visible[row].summary)

    # -- one product -----------------------------------------------------

    def open_product(self, product: ProductSummary) -> None:
        """Show one product's dashboard, building the view on first use."""

        if self.detail is None:
            self.detail = ProductTab(product, self._currency)
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)
            back = QPushButton("← All products")
            back.setProperty("secondary", True)
            back.clicked.connect(self.show_list)
            row = QHBoxLayout()
            row.addWidget(back)
            row.addStretch()
            layout.addLayout(row)
            layout.addWidget(self.detail, 1)
            self.stack.addWidget(page)
        else:
            self.detail.set_product(product)
        self.stack.setCurrentIndex(1)
        self.product_opened.emit(product)

    def set_product(self, product: ProductSummary) -> None:
        if self.detail is not None:
            self.detail.set_product(product)

    def show_list(self) -> None:
        self.stack.setCurrentIndex(0)

    @property
    def rows(self) -> list[ProductRow]:
        return self._rows

    @property
    def showing_product(self) -> bool:
        return self.stack.currentIndex() == 1

    @property
    def current_product(self) -> ProductSummary | None:
        return self.detail.product if self.detail is not None and self.showing_product else None


class DashboardScreen(QWidget):
    """Four views of the shop: everything, its customers, its dealers, its products."""

    GENERAL_TAB = 0
    CUSTOMERS_TAB = 1
    DEALERS_TAB = 2
    PRODUCTS_TAB = 3

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
        self.product_jump = QComboBox()
        configure_searchable_combo(self.product_jump, "Type a product name to open its dashboard")
        self.product_jump.setToolTip("Open one product's dashboard by name")
        # Wide enough for a full product name without crowding the dates beside it.
        self.product_jump.setMinimumWidth(240)
        self.product_jump.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        heading.addWidget(period_label)
        heading.addSpacing(16)
        heading.addWidget(QLabel("Go to product"))
        heading.addWidget(self.product_jump, 1)
        heading.addSpacing(16)
        heading.addWidget(QLabel("From"))
        heading.addWidget(self.from_date)
        heading.addWidget(QLabel("To"))
        heading.addWidget(self.to_date)
        heading.addWidget(refresh)
        layout.addWidget(filter_bar)
        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.setDocumentMode(True)
        self.overview = OverviewTab(currency)
        self.customers = PartyTab(
            "Customers by revenue", CUSTOMER_SPECIFICATIONS, "Customer", currency
        )
        self.dealers = PartyTab("Dealers by revenue", DEALER_SPECIFICATIONS, "Dealer", currency)
        self.products = ProductsTab(currency)
        self.tabs.addTab(self.overview, "General")
        self.tabs.addTab(self.customers, "Customers")
        self.tabs.addTab(self.dealers, "Dealers")
        self.tabs.addTab(self.products, "Products")
        self.products.product_opened.connect(self._product_opened)
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
        """Reload the overview, the product list, and whichever view is open."""

        self._invalidate()
        self._load_overview()
        self._load_products()
        visible = self.tabs.currentWidget()
        if visible is self.customers:
            self._load_party(dealers=False)
        elif visible is self.dealers:
            self._load_party(dealers=True)
        elif visible is self.products and (product := self.products.current_product):
            self._load_product(product)

    def _period_changed(self) -> None:
        self._invalidate()

    def _invalidate(self) -> None:
        """Mark every lazily loaded view stale after the period or data changes."""

        self.customers.loaded = False
        self.dealers.loaded = False
        self.products.loaded = False
        if self.products.detail is not None:
            self.products.detail.loaded = False

    def _tab_changed(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if widget is self.customers and not self.customers.loaded:
            self._load_party(dealers=False)
        elif widget is self.dealers and not self.dealers.loaded:
            self._load_party(dealers=True)
        elif widget is self.products:
            if not self.products.loaded:
                self._load_products()
            detail = self.products.detail
            if self.products.showing_product and detail is not None and not detail.loaded:
                self._load_product(detail.product)

    def _product_opened(self, product: object) -> None:
        """Load the figures for the product the list just opened."""

        self._load_product(cast(ProductSummary, product))

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

    def _load_party(self, *, dealers: bool) -> None:
        """Load one side of the trade: customers or dealer accounts."""

        tab = self.dealers if dealers else self.customers

        def operation() -> tuple[PartyMetrics, list[PartyRow]]:
            period = self._period()
            with self._session_factory() as session:
                service = ReportService(session)
                return (
                    service.dealer_dashboard(period)
                    if dealers
                    else service.customer_dashboard(period),
                    service.top_dealers(period) if dealers else service.top_customers(period),
                )

        def display(result: object) -> None:
            metrics, rows = cast(tuple[PartyMetrics, list[PartyRow]], result)
            tab.display(metrics, rows)

        self._worker = start_worker(
            operation, succeeded=display, failed=lambda error: show_error(self, error)
        )

    def _load_products(self) -> None:
        def operation() -> list[ProductRow]:
            period = self._period()
            with self._session_factory() as session:
                return ReportService(session).product_catalogue(period)

        self._worker = start_worker(
            operation,
            succeeded=self._products_loaded,
            failed=lambda error: show_error(self, error),
        )

    def _products_loaded(self, result: object) -> None:
        rows = cast(list[ProductRow], result)
        self.products.display(rows)
        self._refill_product_jump([row.summary for row in rows])

    def _refill_product_jump(self, products: list[ProductSummary]) -> None:
        """Rebuild the jump list without letting it open a product on its own."""

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
        if not product_id:
            return
        for row in self.products.rows:
            if row.id == product_id:
                self.tabs.setCurrentIndex(self.PRODUCTS_TAB)
                self.products.open_product(row.summary)
                return

    def _load_product(self, product: ProductSummary) -> None:
        product_id = product.id

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
            # The list can move to another product while this query is running.
            tab = self.products.detail
            if tab is None or tab.product.id != product_id:
                return
            tab.display(
                *cast(
                    tuple[ProductMetrics, list[DailyFinancialPoint], list[ProductBatchRow]], result
                )
            )

        self._worker = start_worker(
            operation, succeeded=display, failed=lambda error: show_error(self, error)
        )
