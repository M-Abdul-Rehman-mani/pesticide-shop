"""Modern crop-care visual system shared by every Qt screen."""

from pathlib import Path

_COMBO_BOX_ARROW = (Path(__file__).parent / "assets" / "combo-down-arrow.svg").as_posix()

APPLICATION_STYLESHEET = """
QWidget {
    color: #1c2b25;
    font-family: "Inter", "Segoe UI Variable", "Segoe UI", "Noto Sans", sans-serif;
    font-size: 9pt;
}
QMainWindow, QDialog, QWidget#PageBackground, QStackedWidget#ContentStack {
    background: #f4f7f5;
}
QToolTip {
    background: #173d30; color: #ffffff; border: 0; border-radius: 6px;
    padding: 7px 9px;
}

/* Application shell */
QFrame#TopBar {
    background: #ffffff;
    border-bottom: 1px solid #dfe8e3;
}
QLabel#TopBarEyebrow {
    color: #71817a; font-size: 8.5pt; font-weight: 700; letter-spacing: 1px;
}
QLabel#TopBarTitle { color: #18372c; font-size: 13pt; font-weight: 750; }
QLabel#UserName { color: #18372c; font-size: 9.5pt; font-weight: 700; }
QLabel#UserRole { color: #71817a; font-size: 8.5pt; }
QLabel#Avatar {
    background: #daf2e5; color: #16734b; border: 1px solid #b9e2cd;
    border-radius: 18px; font-size: 10pt; font-weight: 800;
}
QFrame#Sidebar { background: #12372b; border-right: 1px solid #0e2d23; }
QScrollArea#SidebarScroll { background: #12372b; border: 0; }
QScrollArea#SidebarScroll > QWidget > QWidget { background: #12372b; }
QLabel#BrandMark {
    background: #d9f4e6; color: #14784d; border-radius: 18px;
    font-size: 15pt; font-weight: 900;
}
QLabel#Brand { color: #ffffff; font-size: 13pt; font-weight: 800; }
QLabel#BrandCaption {
    color: #8fb5a5; font-size: 7.5pt; font-weight: 700; letter-spacing: 1.3px;
}
QLabel#NavSection {
    color: #7fa493; font-size: 7.5pt; font-weight: 750; letter-spacing: 1.2px;
    padding: 4px 10px 1px 10px;
}
QPushButton#NavButton {
    color: #cce0d7; background: transparent; border: 0; border-radius: 8px;
    text-align: left; padding: 4px 10px; min-height: 14px; font-weight: 550;
}
QPushButton#NavButton:hover { background: #1a4939; color: #ffffff; }
QPushButton#NavButton:checked {
    background: #226a4d; color: #ffffff; font-weight: 700;
    border-left: 3px solid #8fe0b7; padding-left: 9px;
}
QLabel#SidebarHint { color: #789c8d; font-size: 8pt; padding: 4px 8px; }

/* Page hierarchy */
QLabel#PageTitle { color: #17382c; font-size: 15pt; font-weight: 780; }
QLabel#PageSubtitle { color: #71817a; font-size: 9.5pt; }
QLabel#SectionTitle { color: #26483b; font-size: 11pt; font-weight: 700; }
QLabel#RecordCount { color: #71817a; font-size: 9pt; }
QFrame#FilterBar {
    background: #ffffff; border: 1px solid #e1e9e5; border-radius: 10px;
}
QStatusBar {
    background: #ffffff; color: #65766e; border-top: 1px solid #e1e9e5;
    min-height: 22px;
}

/* Buttons */
QPushButton {
    background: #1e8458; color: #ffffff; border: 1px solid #1e8458;
    border-radius: 8px; padding: 5px 12px; min-height: 16px; font-weight: 650;
}
QPushButton:hover { background: #176f49; border-color: #176f49; }
QPushButton:pressed { background: #125c3c; border-color: #125c3c; }
QPushButton:focus { border: 2px solid #73c99c; padding: 4px 11px; }
QPushButton:disabled {
    background: #dce4e0; color: #8a9992; border-color: #dce4e0;
}
QPushButton[secondary="true"] {
    background: #ffffff; color: #315447; border: 1px solid #cbd9d2;
}
QPushButton[secondary="true"]:hover { background: #edf5f1; border-color: #9cb9ab; }
QPushButton[quiet="true"] {
    background: transparent; color: #47675a; border: 1px solid transparent;
}
QPushButton[quiet="true"]:hover { background: #edf5f1; color: #185f42; }
QPushButton[danger="true"] { background: #fff5f4; color: #b33b32; border: 1px solid #efc8c4; }
QPushButton[danger="true"]:hover { background: #b84238; color: #ffffff; border-color: #b84238; }
QPushButton#QuickSaleButton { padding-left: 18px; padding-right: 18px; }
QToolButton {
    background: transparent; color: #315447; border: 1px solid transparent;
    border-radius: 7px; padding: 4px 10px; font-size: 15pt; font-weight: 700;
}
QToolButton:hover, QToolButton::menu-button:hover { background: #e7f2ec; }
QMenu {
    background: #ffffff; color: #1c2b25; border: 1px solid #cbd9d2;
    padding: 5px; border-radius: 7px;
}
QMenu::item { padding: 7px 24px 7px 12px; border-radius: 5px; }
QMenu::item:selected { background: #dff2e8; color: #17382c; }

/* Inputs */
QLineEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox, QTextEdit {
    background: #ffffff; color: #1c2b25; border: 1px solid #c9d6d0; border-radius: 8px;
    padding: 4px 8px; min-height: 16px; selection-background-color: #2b9367;
    selection-color: #ffffff;
}
QLineEdit:hover, QComboBox:hover, QDateEdit:hover, QSpinBox:hover, QTextEdit:hover {
    border-color: #93ada1;
}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus, QTextEdit:focus {
    border: 2px solid #36a574; padding: 3px 7px;
}
QLineEdit:disabled, QComboBox:disabled, QDateEdit:disabled, QSpinBox:disabled, QTextEdit:disabled {
    background: #eef2f0; color: #8b9892; border-color: #dce4e0;
}
QLineEdit[search="true"] { padding-left: 12px; }
QComboBox { padding-right: 30px; }
QComboBox::drop-down {
    subcontrol-origin: padding; subcontrol-position: top right; width: 27px;
    border: 0; border-left: 1px solid #e1e8e4; background: #f7faf8;
    border-top-right-radius: 7px; border-bottom-right-radius: 7px;
}
QComboBox::drop-down:hover { background: #eaf3ee; }
QComboBox::down-arrow {
    image: url("__COMBO_BOX_ARROW__"); width: 10px; height: 6px;
}
QComboBox QAbstractItemView {
    background: #ffffff; color: #1c2b25; border: 1px solid #a9beb4;
    border-radius: 7px; outline: 0; padding: 4px;
    selection-background-color: #dff2e8; selection-color: #17382c;
}
QComboBox QAbstractItemView::item { min-height: 24px; padding: 3px 8px; }

/* Data tables */
QTableView, QTableWidget {
    background: #ffffff; alternate-background-color: #f8faf9;
    border: 1px solid #dfe7e3; border-radius: 10px; gridline-color: transparent;
    selection-background-color: #ddf2e7; selection-color: #17382c; outline: 0;
}
QTableView::item, QTableWidget::item {
    padding: 4px 8px; border-bottom: 1px solid #edf1ef;
}
QTableView::item:hover, QTableWidget::item:hover { background: #eef7f2; }
QHeaderView::section {
    background: #eef4f1; color: #46665a; padding: 6px 8px; border: 0;
    border-bottom: 1px solid #dbe5e0; font-size: 8.5pt; font-weight: 750;
}
QTableCornerButton::section { background: #eef4f1; border: 0; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #bdcdc5; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #8fa89c; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #bdcdc5; border-radius: 4px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

/* Cards and grouped forms */
QGroupBox {
    background: #ffffff; border: 1px solid #dfe7e3; border-radius: 11px;
    margin-top: 10px; padding: 12px 12px 8px 12px; font-weight: 700; color: #294a3e;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 13px; padding: 0 6px; background: #ffffff;
}
QFrame#MetricCard {
    background: #ffffff; border: 1px solid #e0e8e4; border-radius: 12px;
}
QFrame#MetricCard[accent="green"] { border-top: 3px solid #2a9869; }
QFrame#MetricCard[accent="blue"] { border-top: 3px solid #4388d6; }
QFrame#MetricCard[accent="amber"] { border-top: 3px solid #d79a2e; }
QFrame#MetricCard[accent="red"] { border-top: 3px solid #d35c52; }
QFrame#MetricCard[accent="purple"] { border-top: 3px solid #8269c7; }
QLabel#MetricValue { color: #18382c; font-size: 14pt; font-weight: 800; }
QLabel#MetricTitle { color: #697b73; font-size: 8.5pt; font-weight: 650; }
QLabel#MetricHint { color: #94a29c; font-size: 8pt; }
QFrame#TotalCard { background: #173e30; border-radius: 11px; }
QFrame#TotalCard QLabel { color: #d9e9e1; }
QLabel#GrandTotal { color: #ffffff; font-size: 16pt; font-weight: 850; }
QFrame#LoginCard {
    background: #ffffff; border: 1px solid #dce6e1; border-radius: 16px;
}
QLabel#LoginMark {
    background: #dff4e8; color: #17754c; border-radius: 24px;
    font-size: 21pt; font-weight: 900;
}

/* Tabs and dialogs */
QTabWidget::pane {
    border: 1px solid #dfe7e3; border-radius: 9px; background: #ffffff; top: -1px;
}
QTabBar::tab {
    background: transparent; color: #65786f; padding: 6px 12px;
    border-bottom: 2px solid transparent; font-weight: 600;
}
QTabBar::tab:hover { color: #1f7651; background: #edf5f1; }
QTabBar::tab:selected { color: #176d49; border-bottom-color: #2b9869; font-weight: 750; }
QDialogButtonBox QPushButton { min-width: 84px; }
QMessageBox { background: #f7faf8; }
QCheckBox { spacing: 7px; }
QCheckBox::indicator { width: 16px; height: 16px; }
""".replace("__COMBO_BOX_ARROW__", _COMBO_BOX_ARROW)
