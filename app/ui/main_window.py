"""Permission-aware main application shell and keyboard navigation."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.orm import Session, sessionmaker

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


class MainWindow(QMainWindow):
    logout_requested = Signal()

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
        self.resize(1440, 880)
        self.setMinimumSize(1100, 700)
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
        self.stack = QStackedWidget()
        content.addWidget(sidebar)
        content.addWidget(self.stack, 1)
        root.addLayout(content, 1)
        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        self._build_pages(sidebar_layout)
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
        frame.setFixedHeight(64)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(22, 0, 22, 0)
        title = QLabel("PESTICIDE SHOP MANAGEMENT")
        title.setStyleSheet("font-size: 15pt; font-weight: 700; color: #17324d;")
        user = QLabel(
            f"{self._user.full_name}  •  {self._user.role.value.replace('_', ' ').title()}"
        )
        logout = QPushButton("Logout")
        logout.setProperty("secondary", True)
        logout.clicked.connect(self._logout)
        layout.addWidget(title)
        layout.addStretch()
        layout.addWidget(user)
        layout.addWidget(logout)
        return frame

    @staticmethod
    def _sidebar() -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("Sidebar")
        frame.setFixedWidth(215)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 18, 12, 18)
        layout.setSpacing(4)
        brand = QLabel("CROP  CARE")
        brand.setObjectName("Brand")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(brand)
        layout.addSpacing(18)
        return frame, layout

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
        button = QPushButton(name)
        button.setObjectName("NavButton")
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
        self._add_page(
            sidebar,
            "Products",
            lambda: ProductsScreen(factory, actor, settings.app_currency),
            Permission.MANAGE_INVENTORY,
        )
        self._add_page(
            sidebar,
            "Customers",
            lambda: CustomersScreen(factory, actor, settings.app_currency),
            Permission.MANAGE_CUSTOMERS,
        )
        self._add_page(
            sidebar,
            "Dealers",
            lambda: DealersScreen(factory, actor, settings.app_currency),
            Permission.MANAGE_DEALERS,
        )
        self._add_page(
            sidebar,
            "Suppliers",
            lambda: SuppliersScreen(factory, actor, settings.app_currency),
            Permission.MANAGE_SUPPLIERS,
        )
        self._add_page(
            sidebar,
            "Reports",
            lambda: ReportsScreen(factory, actor, settings),
            Permission.VIEW_REPORTS,
        )
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

    def navigate(self, name: str) -> None:
        page = self._pages.get(name)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        self._nav_buttons[name].setChecked(True)
        self.statusBar().showMessage(f"{name} ready", 2500)

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
