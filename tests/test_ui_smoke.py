from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QModelIndex, QPoint, Qt, QThreadPool
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QPushButton
from pytestqt.qtbot import QtBot
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings
from app.security.authentication import AuthenticatedUser
from app.ui.forms import MoneyEdit, PaymentEditor
from app.ui.main_window import MainWindow
from app.ui.sales.screen import CartEntry, SalesScreen
from app.ui.theme import APPLICATION_STYLESHEET
from app.ui.widgets import RowsTableModel


def test_table_model_and_financial_form_widgets(qtbot: QtBot) -> None:
    model = RowsTableModel(("Name", "Amount"))
    model.set_rows((("Phone", Decimal("125000.00")),))
    assert model.rowCount() == 1
    assert model.columnCount() == 2
    assert model.data(model.index(0, 0)) == "Phone"
    assert model.headerData(1, Qt.Orientation.Horizontal) == "Amount"
    assert model.data(QModelIndex()) is None

    money = MoneyEdit()
    qtbot.addWidget(money)
    money.setText("1250.50")
    assert money.decimal_value("Amount") == Decimal("1250.50")
    payments = PaymentEditor()
    qtbot.addWidget(payments)
    assert payments.values() == ()
    first_amount = payments.findChild(QLineEdit)
    assert first_amount is not None
    add_payment = next(
        button for button in payments.findChildren(QPushButton) if button.text() == "Add payment"
    )
    qtbot.mouseClick(add_payment, Qt.MouseButton.LeftButton)
    assert payments.table.rowCount() == 2
    assert payments.values() == ()


def test_combo_boxes_have_readable_field_and_popup_colors(qtbot: QtBot) -> None:
    application = QApplication.instance()
    assert application is not None
    original_stylesheet = application.styleSheet()
    application.setStyleSheet(APPLICATION_STYLESHEET)
    try:
        combo = QComboBox()
        qtbot.addWidget(combo)
        combo.addItems(("Cash", "Card", "Bank Transfer"))
        combo.show()
        combo.showPopup()
        combo.ensurePolished()
        combo.view().ensurePolished()

        field_palette = combo.palette()
        popup_palette = combo.view().palette()
        for palette in (field_palette, popup_palette):
            assert palette.color(QPalette.ColorRole.Base).name() == "#ffffff"
            assert palette.color(QPalette.ColorRole.Text).name() == "#1f2933"
            assert palette.color(QPalette.ColorRole.Highlight).name() == "#2f80ed"
            assert palette.color(QPalette.ColorRole.HighlightedText).name() == "#ffffff"
        combo.hidePopup()
        qtbot.mouseClick(
            combo,
            Qt.MouseButton.LeftButton,
            pos=QPoint(combo.width() - 12, combo.height() // 2),
        )
        assert combo.view().isVisible()
        combo.hidePopup()
    finally:
        application.setStyleSheet(original_stylesheet)


def test_combo_box_theme_includes_visible_down_arrow() -> None:
    arrow_path = Path(__file__).parents[1] / "app" / "ui" / "assets" / "combo-down-arrow.svg"

    assert arrow_path.is_file()
    assert "QComboBox::down-arrow" in APPLICATION_STYLESHEET
    assert arrow_path.as_posix() in APPLICATION_STYLESHEET


def test_owner_main_window_constructs_and_navigates_offscreen(
    qtbot: QtBot,
    database_engine: Engine,
    owner: AuthenticatedUser,
) -> None:
    factory = sessionmaker[Session](
        bind=database_engine,
        expire_on_commit=False,
        autoflush=False,
    )
    window = MainWindow(factory, owner, get_settings())
    qtbot.addWidget(window)
    assert "Dashboard" in window._pages
    assert "Shop Settings" in window._pages
    assert "Settings" in window._pages
    window.navigate("Inventory")
    assert window.stack.currentWidget() is window._pages["Inventory"]
    assert QThreadPool.globalInstance().waitForDone(10_000)


def test_sale_cart_submits_edited_price(
    qtbot: QtBot,
    database_engine: Engine,
    owner: AuthenticatedUser,
) -> None:
    factory = sessionmaker[Session](
        bind=database_engine,
        expire_on_commit=False,
        autoflush=False,
    )
    screen = SalesScreen(factory, owner, get_settings())
    qtbot.addWidget(screen)
    screen._phone_added(CartEntry("350000000000999", "Test Phone", Decimal("125000.00")))

    price = screen.cart_table.cellWidget(0, 2)
    assert isinstance(price, MoneyEdit)
    price.setText("175000.00")

    assert screen._sale_lines()[0].price == Decimal("175000.00")
    assert screen.subtotal_label.text().endswith("175,000.00")
    assert QThreadPool.globalInstance().waitForDone(10_000)
