from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import QModelIndex, Qt, QThreadPool
from PySide6.QtWidgets import QLineEdit, QPushButton
from pytestqt.qtbot import QtBot
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings
from app.security.authentication import AuthenticatedUser
from app.ui.forms import MoneyEdit, PaymentEditor
from app.ui.main_window import MainWindow
from app.ui.theme import APPLICATION_STYLESHEET
from app.ui.widgets import PageHeader, RowsTableModel


def test_table_model_and_financial_widgets(qtbot: QtBot) -> None:
    model = RowsTableModel(("Product", "Amount"))
    model.set_rows((("Gazonner 15% EC", Decimal("150.00")),))
    assert model.rowCount() == 1
    assert model.data(model.index(0, 0)) == "Gazonner 15% EC"
    assert model.headerData(1, Qt.Orientation.Horizontal) == "Amount"
    assert model.data(QModelIndex()) is None

    money = MoneyEdit()
    qtbot.addWidget(money)
    money.setText("1250.50")
    assert money.decimal_value("Amount") == Decimal("1250.50")

    payments = PaymentEditor()
    qtbot.addWidget(payments)
    assert payments.values() == ()
    assert payments.findChild(QLineEdit) is not None
    add_payment = next(
        button for button in payments.findChildren(QPushButton) if button.text() == "Add payment"
    )
    qtbot.mouseClick(add_payment, Qt.MouseButton.LeftButton)
    assert payments.table.rowCount() == 2


def test_modern_ui_components_expose_clear_hierarchy(qtbot: QtBot) -> None:
    header = PageHeader("Batch inventory", "Track stock and expiry risk.")
    qtbot.addWidget(header)

    assert header.title_label.objectName() == "PageTitle"
    assert header.subtitle_label.objectName() == "PageSubtitle"
    assert "QFrame#Sidebar" in APPLICATION_STYLESHEET
    assert "QFrame#FilterBar" in APPLICATION_STYLESHEET
    assert "QFrame#MetricCard" in APPLICATION_STYLESHEET


def test_owner_window_contains_complete_pesticide_workflow(
    qtbot: QtBot,
    database_engine: Engine,
    owner: AuthenticatedUser,
) -> None:
    factory = sessionmaker[Session](bind=database_engine, expire_on_commit=False, autoflush=False)
    window = MainWindow(factory, owner, get_settings())
    qtbot.addWidget(window)

    expected = {
        "Dashboard",
        "Sales",
        "Purchases",
        "Inventory",
        "Products",
        "Customers",
        "Dealers",
        "Suppliers",
        "Reports",
        "Users",
        "Shop Settings",
        "Settings",
    }
    assert expected == set(window._pages)
    assert window.windowTitle() == "Pesticide Shop Management System"
    assert window.minimumWidth() == 900
    window.resize(1000, 700)
    window.show()
    qtbot.wait(20)
    assert window._sidebar_frame.width() == 76
    assert window._pages["Dashboard"].property("refreshesAfterTransactions") is True
    window.navigate("Dealers")
    assert window.stack.currentWidget() is window._pages["Dealers"]
    assert window._page_label.text() == "Dealers"
    assert window._nav_buttons["Dealers"].isChecked()
    assert window._nav_buttons["Dealers"].accessibleName() == "Dealers"
    window.navigate("Sales")
    sales = window._pages["Sales"]
    assert sales.complete.isEnabled() is False  # type: ignore[attr-defined]
    assert sales.cart_count.text() == "0 items in invoice"  # type: ignore[attr-defined]
    assert sales.tabs.count() == 2  # type: ignore[attr-defined]
    assert sales.recipient.isEditable()  # type: ignore[attr-defined]
    assert sales.batch.isEditable()  # type: ignore[attr-defined]
    assert window._pages["Products"].search is not None  # type: ignore[attr-defined]
    assert QThreadPool.globalInstance().waitForDone(10_000)
