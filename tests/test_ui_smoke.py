from __future__ import annotations

from decimal import Decimal

import pytest
from PySide6.QtCore import QModelIndex, Qt, QThreadPool
from PySide6.QtWidgets import QLineEdit, QPushButton
from pytestqt.qtbot import QtBot
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings
from app.security.authentication import AuthenticatedUser
from app.ui.forms import MoneyEdit, PaymentEditor
from app.ui.main_window import MainWindow
from app.ui.reports import screen as reports_module
from app.ui.theme import APPLICATION_STYLESHEET
from app.ui.widgets import PageHeader, RowsTableModel, record_count_text


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
    qtbot.mouseClick(add_payment, Qt.MouseButton.LeftButton)  # type: ignore[no-untyped-call]
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
    monkeypatch: pytest.MonkeyPatch,
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
    available = window.screen().availableGeometry()
    assert window.minimumWidth() == min(900, available.width())
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
    purchases = window._pages["Purchases"]
    assert purchases.tabs.count() == 2  # type: ignore[attr-defined]
    reports = window._pages["Reports"]
    report_names = {
        reports.report_type.itemText(index)  # type: ignore[attr-defined]
        for index in range(reports.report_type.count())  # type: ignore[attr-defined]
    }
    assert {
        "Daily Cash Closing",
        "Customer / Dealer Statements",
        "Product Profitability",
        "Tax & Discounts",
        "Outstanding Payments",
        "Expiry Loss",
    } <= report_names
    assert QThreadPool.globalInstance().waitForDone(10_000)
    report_failures: list[Exception] = []
    monkeypatch.setattr(
        reports_module,
        "show_error",
        lambda _parent, error: report_failures.append(error),
    )
    for report_name in (
        "Daily Cash Closing",
        "Customer / Dealer Statements",
        "Product Profitability",
        "Tax & Discounts",
        "Outstanding Payments",
        "Expiry Loss",
    ):
        reports.report_type.setCurrentText(report_name)  # type: ignore[attr-defined]
        reports.run_report()  # type: ignore[attr-defined]
        assert QThreadPool.globalInstance().waitForDone(10_000)
        qtbot.waitUntil(lambda: reports.run_button.isEnabled(), timeout=5_000)  # type: ignore[attr-defined]
        assert reports._source_payload is not None  # type: ignore[attr-defined]
    assert not report_failures


def test_record_counts_read_correctly_for_one_row_and_many() -> None:
    assert record_count_text(0, "dealer") == "No dealers found."
    assert record_count_text(1, "dealer") == "1 dealer shown"
    assert record_count_text(2, "dealer") == "2 dealers shown"
    assert record_count_text(1, "batch", "batches") == "1 batch shown"
    assert record_count_text(4200, "record") == "4,200 records shown"
