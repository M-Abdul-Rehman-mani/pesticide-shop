"""Permission-aware main application shell and keyboard navigation."""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from PySide6.QtCore import QByteArray, QRect, QSettings, Qt, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QTableView,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.orm import Session, sessionmaker

from app import __version__
from app.config.settings import Settings
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, has_permission
from app.ui.customers.screen import CustomersScreen
from app.ui.dashboard.screen import DashboardScreen
from app.ui.dealers.screen import DealersScreen
from app.ui.inventory.screen import InventoryScreen
from app.ui.products.screen import ProductsScreen
from app.ui.purchases.screen import PurchasesScreen
from app.ui.reports.screen import ReportsScreen
from app.ui.sales.screen import SalesScreen
from app.ui.session_timeout import SessionTimeoutMonitor
from app.ui.settings.screen import SettingsScreen
from app.ui.shop_settings.screen import ShopSettingsScreen
from app.ui.suppliers.screen import SuppliersScreen
from app.ui.users.screen import UsersScreen
from app.ui.widgets import mark_columns_user_sized

#: Bumped when column layouts change meaning, so installs upgrading from an
#: earlier build pick up the new automatic sizing instead of stale widths.
_TABLE_LAYOUT_KEY = "tables/v2"


class MainWindow(QMainWindow):
    logout_requested = Signal()

    _NAV_GLYPHS: ClassVar[dict[str, str]] = {
        "Dashboard": "⌂",
        "Sales": "+",
        "Purchases": "⇣",
        "Inventory": "▦",
        "Products": "◇",
        "Customers": "♙",
        "Dealers": "◎",
        "Suppliers": "⬡",
        "Reports": "▥",
        "Users": "♟",
        "Shop Settings": "⌂",
        "Settings": "⚙",
    }
    _PAGE_CONTEXT: ClassVar[dict[str, str]] = {
        "Dashboard": "Business overview and stock health",
        "Sales": "Create a delivery challan and invoice",
        "Purchases": "Receive supplier stock by batch",
        "Inventory": "Track quantities, expiry, and movements",
        "Products": "Manage the pesticide product catalog",
        "Customers": "Retail customer directory",
        "Dealers": "Trade accounts, credit, and balances",
        "Suppliers": "Supplier accounts and purchase history",
        "Reports": "Sales, profit, inventory, and exports",
        "Users": "Roles and account access",
        "Shop Settings": "Business identity and invoice details",
        "Settings": "Email, printing, backup, and security",
    }

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        user: AuthenticatedUser,
        settings: Settings,
    ) -> None:
        super().__init__()
        self._session_factory = session_factory
        self._user = user
        self._settings = settings
        self._pages: dict[str, QWidget] = {}
        self._nav_buttons: dict[str, QPushButton] = {}
        self.setWindowTitle("Pesticide Shop Management System")
        self.setMinimumSize(900, 560)
        self._compact_shell = False
        self._fit_to_available_screen()
        central = QWidget()
        central.setObjectName("PageBackground")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._top_bar())
        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        sidebar, sidebar_layout = self._sidebar()
        self._sidebar_frame = sidebar
        self.stack = QStackedWidget()
        self.stack.setObjectName("ContentStack")
        content.addWidget(sidebar)
        content.addWidget(self.stack, 1)
        root.addLayout(content, 1)
        self.setCentralWidget(central)
        status = QStatusBar()
        status.setSizeGripEnabled(False)
        shortcuts = QLabel("Ctrl+N  New sale    Ctrl+R  Refresh    Ctrl+F  Search")
        shortcuts.setObjectName("RecordCount")
        status.addPermanentWidget(shortcuts)
        self.setStatusBar(status)
        self._build_pages(sidebar_layout)
        self._connect_data_refreshes()
        self._preferences = QSettings("CropCare", "PesticideShop")
        self._restore_ui_preferences()
        self._install_shortcuts()
        self._session_monitor = SessionTimeoutMonitor(settings.app_session_timeout_minutes, self)
        self._session_monitor.timed_out.connect(self._timed_out)
        application = QApplication.instance()
        if application:
            application.installEventFilter(self._session_monitor)
        self.navigate("Dashboard")

    def _top_bar(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("TopBar")
        frame.setFixedHeight(54)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(16, 0, 14, 0)
        page_copy = QVBoxLayout()
        page_copy.setSpacing(0)
        eyebrow = QLabel("WORKSPACE")
        eyebrow.setObjectName("TopBarEyebrow")
        self._page_label = QLabel("Dashboard")
        self._page_label.setObjectName("TopBarTitle")
        page_copy.addWidget(eyebrow)
        page_copy.addWidget(self._page_label)

        quick_sale = QPushButton("+  New sale")
        quick_sale.setObjectName("QuickSaleButton")
        quick_sale.setToolTip("Start a new sale (Ctrl+N)")
        quick_sale.clicked.connect(lambda: self.navigate("Sales"))
        quick_sale.setVisible(has_permission(self._user.role, Permission.CREATE_SALE))

        initials = "".join(part[0] for part in self._user.full_name.split()[:2]).upper() or "U"
        avatar = QLabel(initials)
        avatar.setObjectName("Avatar")
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFixedSize(36, 36)
        user_copy = QVBoxLayout()
        user_copy.setSpacing(0)
        user_name = QLabel(self._user.full_name)
        user_name.setObjectName("UserName")
        user_role = QLabel(self._user.role.value.replace("_", " ").title())
        user_role.setObjectName("UserRole")
        user_copy.addWidget(user_name)
        user_copy.addWidget(user_role)
        logout = QPushButton("Logout")
        logout.setProperty("quiet", True)
        logout.setToolTip("Sign out of this workstation")
        logout.clicked.connect(self._logout)
        self._user_name_label, self._user_role_label = user_name, user_role
        layout.addLayout(page_copy)
        layout.addStretch()
        layout.addWidget(quick_sale)
        layout.addSpacing(10)
        layout.addWidget(avatar)
        layout.addLayout(user_copy)
        layout.addWidget(logout)
        return frame

    def _sidebar(self) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("Sidebar")
        frame.setFixedWidth(220)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(10, 12, 10, 10)
        layout.setSpacing(2)
        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        mark = QLabel("C")
        mark.setObjectName("BrandMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(36, 36)
        brand_copy = QVBoxLayout()
        brand_copy.setSpacing(0)
        brand = QLabel("CropCare")
        brand.setObjectName("Brand")
        caption = QLabel("PESTICIDE OPERATIONS")
        caption.setObjectName("BrandCaption")
        self._brand_name, self._brand_caption = brand, caption
        brand_copy.addWidget(brand)
        brand_copy.addWidget(caption)
        brand_row.addWidget(mark)
        brand_row.addLayout(brand_copy)
        brand_row.addStretch()
        layout.addLayout(brand_row)
        layout.addSpacing(8)
        scroll = QScrollArea()
        scroll.setObjectName("SidebarScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        wrap = QVBoxLayout(frame)
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.addWidget(scroll)
        return frame, layout

    @staticmethod
    def _add_section(sidebar: QVBoxLayout, name: str) -> None:
        label = QLabel(name.upper())
        label.setObjectName("NavSection")
        sidebar.addWidget(label)

    def _add_page(
        self,
        sidebar: QVBoxLayout,
        name: str,
        factory: Callable[[], QWidget],
        permission: Permission | None = None,
    ) -> None:
        if permission and not has_permission(self._user.role, permission):
            return
        page = factory()
        self._pages[name] = page
        self.stack.addWidget(page)
        button = QPushButton(f"{self._NAV_GLYPHS.get(name, '•')}   {name}")
        button.setObjectName("NavButton")
        button.setAccessibleName(name)
        button.setCheckable(True)
        button.setAutoExclusive(True)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.clicked.connect(lambda _checked=False, page_name=name: self.navigate(page_name))
        sidebar.addWidget(button)
        self._nav_buttons[name] = button

    def _build_pages(self, sidebar: QVBoxLayout) -> None:
        factory = self._session_factory
        settings = self._settings
        actor = self._user
        self._add_section(sidebar, "Operations")
        self._add_page(
            sidebar,
            "Dashboard",
            lambda: DashboardScreen(factory, settings.app_timezone, settings.app_currency),
            Permission.VIEW_DASHBOARD,
        )
        self._add_page(
            sidebar,
            "Sales",
            lambda: SalesScreen(factory, actor, settings),
            Permission.CREATE_SALE,
        )
        self._add_page(
            sidebar,
            "Purchases",
            lambda: PurchasesScreen(factory, actor, settings),
            Permission.RECORD_PURCHASE,
        )
        self._add_page(
            sidebar,
            "Inventory",
            lambda: InventoryScreen(factory, settings.app_currency, actor),
            Permission.VIEW_INVENTORY,
        )
        self._add_section(sidebar, "Directory")
        self._add_page(
            sidebar,
            "Products",
            lambda: ProductsScreen(factory, actor, settings.app_currency),
            Permission.MANAGE_INVENTORY,
        )
        self._add_page(
            sidebar,
            "Customers",
            lambda: CustomersScreen(factory, actor, settings),
            Permission.MANAGE_CUSTOMERS,
        )
        self._add_page(
            sidebar,
            "Dealers",
            lambda: DealersScreen(factory, actor, settings),
            Permission.MANAGE_DEALERS,
        )
        self._add_page(
            sidebar,
            "Suppliers",
            lambda: SuppliersScreen(factory, actor, settings.app_currency),
            Permission.MANAGE_SUPPLIERS,
        )
        self._add_section(sidebar, "Insights")
        self._add_page(
            sidebar,
            "Reports",
            lambda: ReportsScreen(factory, actor, settings),
            Permission.VIEW_REPORTS,
        )
        self._add_section(sidebar, "Administration")
        self._add_page(
            sidebar,
            "Users",
            lambda: UsersScreen(factory, actor),
            Permission.MANAGE_USERS,
        )
        self._add_page(
            sidebar,
            "Shop Settings",
            lambda: ShopSettingsScreen(factory, actor, settings),
            Permission.MANAGE_SETTINGS,
        )
        self._add_page(
            sidebar,
            "Settings",
            lambda: SettingsScreen(factory, actor, settings),
            Permission.MANAGE_SETTINGS,
        )
        sidebar.addStretch()
        hint = QLabel(f"Version {__version__}\nSecure PostgreSQL workspace")
        hint.setObjectName("SidebarHint")
        sidebar.addWidget(hint)

    def _connect_data_refreshes(self) -> None:
        dashboard = self._pages.get("Dashboard")
        sales = self._pages.get("Sales")
        purchases = self._pages.get("Purchases")
        if sales is not None and hasattr(sales, "sale_completed"):
            sales.sale_completed.connect(self._refresh_transaction_pages)
        if purchases is not None and hasattr(purchases, "purchase_completed"):
            purchases.purchase_completed.connect(self._refresh_transaction_pages)
            refresh_sales = getattr(sales, "refresh", None)
            if callable(refresh_sales):
                purchases.purchase_completed.connect(lambda _reference: refresh_sales())
        if dashboard is not None:
            dashboard.setProperty("refreshesAfterTransactions", True)

    def _refresh_transaction_pages(self, _reference: str) -> None:
        for name in ("Dashboard", "Inventory", "Products", "Customers", "Dealers", "Suppliers"):
            page = self._pages.get(name)
            refresh = getattr(page, "refresh", None)
            if callable(refresh):
                refresh()

    def _available_screen(self) -> QRect | None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        return None if screen is None else screen.availableGeometry()

    def _fit_to_available_screen(self) -> None:
        available = self._available_screen()
        if available is None:
            self.resize(1280, 720)
            return
        self.setMinimumSize(min(900, available.width()), min(560, available.height()))
        self.resize(min(1366, available.width()), min(768, available.height()))
        self.move(
            available.x() + max(0, (available.width() - self.width()) // 2),
            available.y() + max(0, (available.height() - self.height()) // 2),
        )

    def _clamp_to_available_screen(self) -> None:
        available = self._available_screen()
        if available is None:
            return
        self.setMinimumSize(min(900, available.width()), min(560, available.height()))
        width = min(max(self.width(), self.minimumWidth()), available.width())
        height = min(max(self.height(), self.minimumHeight()), available.height())
        self.resize(width, height)
        frame = self.frameGeometry()
        x = min(max(frame.x(), available.x()), available.x() + available.width() - frame.width())
        y = min(max(frame.y(), available.y()), available.y() + available.height() - frame.height())
        self.move(x, y)

    def _restore_ui_preferences(self) -> None:
        geometry = self._preferences.value("main/geometry")
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)
        self._clamp_to_available_screen()
        for page_name, page in self._pages.items():
            tables = page.findChildren(QTableView)
            for index, table in enumerate(tables):
                state = self._preferences.value(f"{_TABLE_LAYOUT_KEY}/{page_name}/{index}")
                if isinstance(state, QByteArray) and table.horizontalHeader().restoreState(state):
                    # A remembered layout wins over automatic content sizing.
                    mark_columns_user_sized(table)

    def _save_ui_preferences(self) -> None:
        self._preferences.setValue("main/geometry", self.saveGeometry())
        for page_name, page in self._pages.items():
            tables = page.findChildren(QTableView)
            for index, table in enumerate(tables):
                self._preferences.setValue(
                    f"{_TABLE_LAYOUT_KEY}/{page_name}/{index}",
                    table.horizontalHeader().saveState(),
                )
        self._preferences.sync()

    def closeEvent(self, event: object) -> None:
        self._save_ui_preferences()
        super().closeEvent(event)  # type: ignore[arg-type]

    def navigate(self, name: str) -> None:
        page = self._pages.get(name)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        self._nav_buttons[name].setChecked(True)
        self._page_label.setText(name)
        self.statusBar().showMessage(self._PAGE_CONTEXT.get(name, f"{name} ready"), 3500)
        refresh = getattr(page, "refresh", None)
        if callable(refresh):
            refresh()

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)  # type: ignore[arg-type]
        compact = self.width() < 1120
        if compact == self._compact_shell:
            return
        self._compact_shell = compact
        self._sidebar_frame.setFixedWidth(76 if compact else 220)
        self._brand_name.setVisible(not compact)
        self._brand_caption.setVisible(not compact)
        self._user_name_label.setVisible(not compact)
        self._user_role_label.setVisible(not compact)
        for name, button in self._nav_buttons.items():
            glyph = self._NAV_GLYPHS.get(name, "•")
            button.setText(glyph if compact else f"{glyph}   {name}")
            button.setToolTip(name if compact else "")

    def _install_shortcuts(self) -> None:
        bindings = {
            "Ctrl+N": lambda: self.navigate("Sales"),
            "Ctrl+I": lambda: self.navigate("Inventory"),
            "Ctrl+D": lambda: self.navigate("Dashboard"),
            "Ctrl+R": self._refresh_current,
            "Ctrl+F": self._focus_search,
            "Ctrl+S": self._save_current,
            "Ctrl+P": self._print_current,
        }
        self._shortcuts: list[QShortcut] = []
        for sequence, callback in bindings.items():
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)

    def _refresh_current(self) -> None:
        page = self.stack.currentWidget()
        refresh = getattr(page, "refresh", None)
        if callable(refresh):
            refresh()

    def _focus_search(self) -> None:
        page = self.stack.currentWidget()
        for name in ("search", "invoice"):
            widget = getattr(page, name, None)
            if widget is not None and hasattr(widget, "setFocus"):
                widget.setFocus()
                return

    def _save_current(self) -> None:
        save = getattr(self.stack.currentWidget(), "save", None)
        if callable(save):
            save()

    def _print_current(self) -> None:
        page = self.stack.currentWidget()
        for name in ("_print", "_preview_last"):
            action = getattr(page, name, None)
            if callable(action):
                action()
                return

    def _logout(self) -> None:
        if (
            QMessageBox.question(self, "Logout", "End the current session?")
            == QMessageBox.StandardButton.Yes
        ):
            self.logout_requested.emit()

    def _timed_out(self) -> None:
        QMessageBox.information(
            self,
            "Session expired",
            "Your session expired after a period of inactivity. Please sign in again.",
        )
        self.logout_requested.emit()
