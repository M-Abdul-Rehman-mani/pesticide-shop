"""Supplier pesticide purchases with batch, expiry, cartons, and stock creation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import cast

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import Settings
from app.models.product import Product
from app.models.supplier import Supplier
from app.security.authentication import AuthenticatedUser
from app.services.dto import CreateStockPurchaseCommand, PurchasedBatchInput
from app.services.stock_purchase_service import StockPurchaseService
from app.ui.forms import MoneyEdit, PaymentEditor
from app.ui.widgets import show_error
from app.ui.workers import FunctionWorker, start_worker


@dataclass(frozen=True, slots=True)
class PurchaseCartEntry:
    product_id: uuid.UUID
    product: str
    batch_number: str
    quantity: int
    cartons: int
    packs_per_carton: int
    manufacture_date: date
    expiry_date: date
    purchase_price: Decimal
    selling_price: Decimal


class PurchasesScreen(QWidget):
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        actor: AuthenticatedUser,
        settings: Settings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session_factory, self._actor, self._settings = session_factory, actor, settings
        self._worker: FunctionWorker | None = None
        self._cart: list[PurchaseCartEntry] = []
        layout = QVBoxLayout(self)
        title = QLabel("Receive Pesticide Stock")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        supplier_group, supplier_form = QGroupBox("Supplier"), QFormLayout()
        supplier_group.setLayout(supplier_form)
        self.supplier = QComboBox()
        self.supplier.setMinimumWidth(350)
        supplier_form.addRow("Supplier", self.supplier)
        layout.addWidget(supplier_group)

        entry_group, entry_form = QGroupBox("Batch Details"), QFormLayout()
        entry_group.setLayout(entry_form)
        self.product, self.batch_number = QComboBox(), QLineEdit()
        self.quantity, self.cartons, self.packs = QSpinBox(), QSpinBox(), QSpinBox()
        for field in (self.quantity, self.cartons, self.packs):
            field.setRange(0, 1_000_000)
        self.quantity.setMinimum(1)
        self.manufacture_date = QDateEdit(QDate.currentDate())
        self.manufacture_date.setCalendarPopup(True)
        self.expiry_date = QDateEdit(QDate.currentDate().addYears(2))
        self.expiry_date.setCalendarPopup(True)
        self.purchase_price, self.selling_price = MoneyEdit(), MoneyEdit()
        add = QPushButton("Add Batch")
        fields = (
            ("Product", self.product),
            ("Batch number", self.batch_number),
            ("Quantity (packs/units)", self.quantity),
            ("Cartons", self.cartons),
            ("Packs per carton", self.packs),
            ("Manufacture date", self.manufacture_date),
            ("Expiry date", self.expiry_date),
            ("Purchase price / unit", self.purchase_price),
            ("Sale price / unit", self.selling_price),
        )
        for field_label, field_widget in fields:
            entry_form.addRow(field_label, field_widget)
        entry_form.addRow(add)
        layout.addWidget(entry_group)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            (
                "Product",
                "Batch",
                "Qty",
                "Cartons",
                "Packs/Carton",
                "Expiry",
                "Purchase/Unit",
                "Line Total",
                "Action",
            )
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        bottom = QHBoxLayout()
        payments_group, payments_layout = QGroupBox("Payments"), QVBoxLayout()
        payments_group.setLayout(payments_layout)
        self.payments = PaymentEditor()
        payments_layout.addWidget(self.payments)
        totals_group, totals_form = QGroupBox("Totals"), QFormLayout()
        totals_group.setLayout(totals_form)
        self.subtotal, self.total = (
            QLabel(f"{settings.app_currency} 0.00"),
            QLabel(f"{settings.app_currency} 0.00"),
        )
        self.discount, self.tax = MoneyEdit(), MoneyEdit()
        for total_label, total_widget in (
            ("Subtotal", self.subtotal),
            ("Discount", self.discount),
            ("Tax", self.tax),
            ("Total", self.total),
        ):
            totals_form.addRow(total_label, total_widget)
        bottom.addWidget(payments_group, 2)
        bottom.addWidget(totals_group, 1)
        layout.addLayout(bottom)
        action_row = QHBoxLayout()
        action_row.addStretch()
        self.save_button = QPushButton("Complete Purchase & Add Stock")
        action_row.addWidget(self.save_button)
        layout.addLayout(action_row)
        add.clicked.connect(self._add)
        self.save_button.clicked.connect(self.save)
        self.product.currentIndexChanged.connect(self._product_changed)
        self.discount.textChanged.connect(self._calculate)
        self.tax.textChanged.connect(self._calculate)
        self._load_choices()

    def _load_choices(self) -> None:
        def operation() -> tuple[
            list[tuple[uuid.UUID, str]], list[tuple[uuid.UUID, str, Decimal, Decimal]]
        ]:
            with self._session_factory() as session:
                suppliers = session.scalars(
                    select(Supplier).where(Supplier.is_active.is_(True)).order_by(Supplier.name)
                )
                products = session.scalars(
                    select(Product)
                    .where(Product.is_active.is_(True))
                    .order_by(Product.manufacturer, Product.name)
                )
                return (
                    [(s.id, s.company_name or s.name) for s in suppliers],
                    [
                        (p.id, p.display_name, p.default_purchase_price, p.default_sale_price)
                        for p in products
                    ],
                )

        self._worker = start_worker(
            operation, succeeded=self._set_choices, failed=lambda error: show_error(self, error)
        )

    def _set_choices(self, result: object) -> None:
        suppliers, products = cast(
            tuple[list[tuple[uuid.UUID, str]], list[tuple[uuid.UUID, str, Decimal, Decimal]]],
            result,
        )
        self.supplier.clear()
        self.product.clear()
        for item_id, label in suppliers:
            self.supplier.addItem(label, item_id)
        for item_id, label, purchase, sale in products:
            self.product.addItem(label, (item_id, purchase, sale))
        self._product_changed()

    def _product_changed(self) -> None:
        if data := self.product.currentData():
            _product_id, purchase, sale = data
            self.purchase_price.setText(f"{purchase:.2f}")
            self.selling_price.setText(f"{sale:.2f}")

    def _add(self) -> None:
        data = self.product.currentData()
        if data is None:
            show_error(self, ValueError("Create a product first."))
            return
        product_id, _purchase, _sale = data
        entry = PurchaseCartEntry(
            product_id,
            self.product.currentText(),
            self.batch_number.text().strip().upper(),
            self.quantity.value(),
            self.cartons.value(),
            self.packs.value(),
            cast(date, self.manufacture_date.date().toPython()),
            cast(date, self.expiry_date.date().toPython()),
            self.purchase_price.decimal_value("Purchase price"),
            self.selling_price.decimal_value("Selling price"),
        )
        self._cart.append(entry)
        row = self.table.rowCount()
        self.table.insertRow(row)
        values = (
            entry.product,
            entry.batch_number,
            entry.quantity,
            entry.cartons,
            entry.packs_per_carton,
            entry.expiry_date.strftime("%d-%b-%Y"),
            f"{entry.purchase_price:,.2f}",
            f"{entry.purchase_price * entry.quantity:,.2f}",
        )
        for column, value in enumerate(values):
            self.table.setItem(row, column, QTableWidgetItem(str(value)))
        remove = QPushButton("Remove")
        remove.setProperty("danger", True)
        remove.clicked.connect(lambda _checked=False, item=entry: self._remove(item))
        self.table.setCellWidget(row, 8, remove)
        self.batch_number.clear()
        self._calculate()

    def _remove(self, entry: PurchaseCartEntry) -> None:
        row = self._cart.index(entry)
        self._cart.pop(row)
        self.table.removeRow(row)
        self._calculate()

    def _calculate(self) -> None:
        try:
            subtotal = sum((e.purchase_price * e.quantity for e in self._cart), Decimal("0.00"))
            total = subtotal - self.discount.decimal_value() + self.tax.decimal_value()
        except Exception:
            return
        self.subtotal.setText(f"{self._settings.app_currency} {subtotal:,.2f}")
        self.total.setText(f"{self._settings.app_currency} {total:,.2f}")

    def save(self) -> None:
        command = CreateStockPurchaseCommand(
            supplier_id=self.supplier.currentData(),
            batches=tuple(
                PurchasedBatchInput(
                    product_id=e.product_id,
                    batch_number=e.batch_number,
                    quantity=e.quantity,
                    purchase_price=e.purchase_price,
                    selling_price=e.selling_price,
                    manufacture_date=e.manufacture_date,
                    expiry_date=e.expiry_date,
                    cartons=e.cartons,
                    packs_per_carton=e.packs_per_carton,
                )
                for e in self._cart
            ),
            payments=self.payments.values(),
            discount=self.discount.decimal_value(),
            tax=self.tax.decimal_value(),
        )
        self.save_button.setEnabled(False)

        def operation() -> str:
            with self._session_factory.begin() as session:
                return StockPurchaseService(session).create(command, self._actor).purchase_number

        self._worker = start_worker(
            operation,
            succeeded=self._saved,
            failed=lambda error: show_error(self, error),
            finished=lambda: self.save_button.setEnabled(True),
        )

    def _saved(self, purchase_number: object) -> None:
        QMessageBox.information(
            self,
            "Purchase completed",
            f"Purchase {purchase_number} and all stock batches were saved.",
        )
        self._cart.clear()
        self.table.setRowCount(0)
        self.payments.clear()
        self.discount.setText("0.00")
        self.tax.setText("0.00")
        self._calculate()
