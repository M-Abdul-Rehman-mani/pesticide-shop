"""Application-wide professional Qt stylesheet."""

APPLICATION_STYLESHEET = """
QWidget {
    color: #1f2933;
    font-family: "Inter", "Segoe UI", "Noto Sans", sans-serif;
    font-size: 10pt;
}
QMainWindow, QDialog, QWidget#PageBackground { background: #f4f7fa; }
QFrame#TopBar { background: #ffffff; border-bottom: 1px solid #d9e2ec; }
QFrame#Sidebar { background: #17324d; }
QLabel#Brand { color: #ffffff; font-size: 16pt; font-weight: 700; }
QPushButton#NavButton {
    color: #dbe7f3; background: transparent; border: none; border-radius: 6px;
    text-align: left; padding: 11px 14px;
}
QPushButton#NavButton:hover { background: #244866; color: #ffffff; }
QPushButton#NavButton:checked { background: #2f80ed; color: #ffffff; font-weight: 600; }
QPushButton {
    background: #2f80ed; color: #ffffff; border: none; border-radius: 5px;
    padding: 7px 14px; min-height: 20px;
}
QPushButton:hover { background: #246bc2; }
QPushButton:disabled { background: #b8c2cc; }
QPushButton[secondary="true"] { background: #e8eef4; color: #17324d; }
QPushButton[danger="true"] { background: #c0392b; }
QLineEdit, QComboBox, QDateEdit, QSpinBox, QTextEdit {
    background: #ffffff; border: 1px solid #bcccdc; border-radius: 5px;
    padding: 6px; selection-background-color: #2f80ed;
}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QTextEdit:focus { border: 1px solid #2f80ed; }
QTableView, QTableWidget {
    background: #ffffff; alternate-background-color: #f7f9fb; border: 1px solid #d9e2ec;
    gridline-color: #e8eef4; selection-background-color: #dbeafe;
    selection-color: #17324d;
}
QHeaderView::section {
    background: #e8eef4; color: #17324d; padding: 7px; border: none;
    border-right: 1px solid #d9e2ec; font-weight: 600;
}
QGroupBox {
    background: #ffffff; border: 1px solid #d9e2ec; border-radius: 7px;
    margin-top: 10px; padding-top: 12px; font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
QFrame#MetricCard { background: #ffffff; border: 1px solid #d9e2ec; border-radius: 8px; }
QLabel#MetricValue { color: #17324d; font-size: 18pt; font-weight: 700; }
QLabel#MetricTitle { color: #627d98; font-size: 9pt; }
QLabel#PageTitle { color: #17324d; font-size: 18pt; font-weight: 700; }
QStatusBar { background: #ffffff; color: #486581; }
QTabWidget::pane { border: 1px solid #d9e2ec; background: #ffffff; }
QTabBar::tab { background: #e8eef4; padding: 8px 16px; }
QTabBar::tab:selected { background: #2f80ed; color: #ffffff; }
"""
