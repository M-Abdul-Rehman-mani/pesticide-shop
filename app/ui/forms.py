"""Shared validated form widgets."""

from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.models.enums import PaymentMethod
from app.services.dto import PaymentInput
from app.ui.widgets import configure_table
from app.utils.validators import nonnegative_money


class MoneyEdit(QLineEdit):
    def __init__(self, value: str = "0.00", parent: QWidget | None = None) -> None:
        super().__init__(value, parent)
        self.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"^[0-9]{0,13}(\.[0-9]{0,2})?$"))
        )
        self.setAlignment(Qt.AlignmentFlag.AlignRight)

    def decimal_value(self, field: str = "Amount") -> Decimal:
        return nonnegative_money(self.text() or "0", field=field)


class PaymentEditor(QWidget):
    """Editable split-payment rows that never convert values through float."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(("Method", "Amount", "Reference"))
        self.table.setMinimumHeight(96)
        configure_table(self.table, stretch_column=2, editable=True)
        controls = QHBoxLayout()
        add = QPushButton("Add payment")
        add.setProperty("secondary", True)
        remove = QPushButton("Remove")
        remove.setProperty("quiet", True)
        controls.addWidget(add)
        controls.addWidget(remove)
        controls.addStretch()
        layout.addWidget(self.table)
        layout.addLayout(controls)
        add.clicked.connect(lambda _checked=False: self.add_row())
        remove.clicked.connect(self.remove_selected)
        self.add_row()

    def add_row(self, method: PaymentMethod = PaymentMethod.CASH, amount: str = "0.00") -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        methods = QComboBox()
        for candidate in PaymentMethod:
            methods.addItem(candidate.value.replace("_", " ").title(), candidate)
        methods.setCurrentIndex(list(PaymentMethod).index(method))
        self.table.setCellWidget(row, 0, methods)
        self.table.setCellWidget(row, 1, MoneyEdit(amount))
        self.table.setItem(row, 2, QTableWidgetItem(""))

    def remove_selected(self) -> None:
        row = self.table.currentRow()
        if row >= 0 and self.table.rowCount() > 1:
            self.table.removeRow(row)

    def values(self) -> tuple[PaymentInput, ...]:
        payments: list[PaymentInput] = []
        for row in range(self.table.rowCount()):
            method_widget = self.table.cellWidget(row, 0)
            amount_widget = self.table.cellWidget(row, 1)
            assert isinstance(method_widget, QComboBox)
            assert isinstance(amount_widget, MoneyEdit)
            amount = amount_widget.decimal_value("Payment")
            reference_item = self.table.item(row, 2)
            if amount > 0:
                payments.append(
                    PaymentInput(
                        method=method_widget.currentData(),
                        amount=amount,
                        reference=(reference_item.text().strip() or None)
                        if reference_item is not None
                        else None,
                    )
                )
        return tuple(payments)

    def clear(self) -> None:
        self.table.setRowCount(0)
        self.add_row()
