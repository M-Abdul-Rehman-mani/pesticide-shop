"""Supplier purchase intake with serialized IMEIs and atomic inventory creation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import cast

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
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
from app.services.dto import CreatePurchaseCommand, PurchasedPhoneInput
from app.services.purchase_service import PurchaseService
from app.ui.forms import MoneyEdit, PaymentEditor
from app.ui.widgets import show_error
from app.ui.workers import FunctionWorker, start_worker


@dataclass(frozen=True, slots=True)
class PurchaseCartEntry:
    product_id: uuid.UUID
    product: str
    imei_1: str
    imei_2: str | None
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
        self._session_factory = session_factory
        self._actor = actor
        self._settings = settings
        self._worker: FunctionWorker | None = None
        self._cart: list[PurchaseCartEntry] = []
        layout = QVBoxLayout(self)
        title = QLabel("Record Purchase")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        header = QGroupBox("Purchase details")
        form = QFormLayout(header)
        self.supplier = QComboBox()
        self.supplier.setMinimumWidth(300)
        form.addRow("Supplier", self.supplier)
        layout.addWidget(header)
        item_group = QGroupBox("Add serialized phone")
        item_form = QFormLayout(item_group)
        self.product = QComboBox()
        self.imei_1 = QLineEdit()
        self.imei_1.setMaxLength(15)
        self.imei_2 = QLineEdit()
        self.imei_2.setMaxLength(15)
        self.purchase_price = MoneyEdit()
        self.selling_price = MoneyEdit()
        add = QPushButton("Add phone")
        item_form.addRow("Product", self.product)
        item_form.addRow("IMEI", self.imei_1)
        item_form.addRow("IMEI2", self.imei_2)
        item_form.addRow("Purchase price", self.purchase_price)
        item_form.addRow("Selling price", self.selling_price)
        item_form.addRow("", add)
        layout.addWidget(item_group)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ("Product", "IMEI", "IMEI2", "Purchase Price", "Selling Price", "Action")
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        bottom = QHBoxLayout()
        payments_group = QGroupBox("Payments")
        payments_layout = QVBoxLayout(payments_group)
        self.payments = PaymentEditor()
        payments_layout.addWidget(self.payments)
        totals_group = QGroupBox("Totals")
        totals_form = QFormLayout(totals_group)
        self.subtotal = QLabel(f"{settings.app_currency} 0.00")
        self.discount = MoneyEdit()
        self.tax = MoneyEdit()
        self.total = QLabel(f"{settings.app_currency} 0.00")
        totals_form.addRow("Subtotal", self.subtotal)
        totals_form.addRow("Discount", self.discount)
        totals_form.addRow("Tax", self.tax)
        totals_form.addRow("Total", self.total)
        bottom.addWidget(payments_group, 2)
        bottom.addWidget(totals_group, 1)
        layout.addLayout(bottom)
        self.save_button = QPushButton("Complete Purchase")
        action_row = QHBoxLayout()
        action_row.addStretch()
        action_row.addWidget(self.save_button)
        layout.addLayout(action_row)
        add.clicked.connect(self._add)
        self.save_button.clicked.connect(self.save)
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
                    .order_by(Product.brand, Product.model)
                )
                return (
                    [(supplier.id, supplier.name) for supplier in suppliers],
                    [
                        (
                            product.id,
                            product.display_name,
                            product.default_purchase_price,
                            product.default_sale_price,
                        )
                        for product in products
                    ],
                )

        self._worker = start_worker(
            operation,
            succeeded=self._set_choices,
            failed=lambda error: show_error(self, error),
        )

    def _set_choices(self, result: object) -> None:
        suppliers, products = cast(
            tuple[
                list[tuple[uuid.UUID, str]],
                list[tuple[uuid.UUID, str, Decimal, Decimal]],
            ],
            result,
        )
        for item_id, label in suppliers:
            self.supplier.addItem(label, item_id)
        for item_id, label, purchase_price, sale_price in products:
            self.product.addItem(label, (item_id, purchase_price, sale_price))
        self.product.currentIndexChanged.connect(self._product_changed)
        self._product_changed()

    def _product_changed(self) -> None:
        data = self.product.currentData()
        if data:
            _product_id, purchase_price, sale_price = data
            self.purchase_price.setText(f"{purchase_price:.2f}")
            self.selling_price.setText(f"{sale_price:.2f}")

    def _add(self) -> None:
        data = self.product.currentData()
        if data is None:
            show_error(self, ValueError("Create a product before recording a purchase."))
            return
        product_id, _default_purchase, _default_sale = data
        entry = PurchaseCartEntry(
            product_id=product_id,
            product=self.product.currentText(),
            imei_1=self.imei_1.text().strip(),
            imei_2=self.imei_2.text().strip() or None,
            purchase_price=self.purchase_price.decimal_value("Purchase price"),
            selling_price=self.selling_price.decimal_value("Selling price"),
        )
        self._cart.append(entry)
        row = self.table.rowCount()
        self.table.insertRow(row)
        for column, value in enumerate(
            (
                entry.product,
                entry.imei_1,
                entry.imei_2 or "—",
                f"{entry.purchase_price:,.2f}",
                f"{entry.selling_price:,.2f}",
            )
        ):
            self.table.setItem(row, column, QTableWidgetItem(str(value)))
        remove = QPushButton("Remove")
        remove.setProperty("danger", True)
        remove.clicked.connect(lambda _checked=False, item=entry: self._remove(item))
        self.table.setCellWidget(row, 5, remove)
        self.imei_1.clear()
        self.imei_2.clear()
        self._calculate()

    def _remove(self, entry: PurchaseCartEntry) -> None:
        row = self._cart.index(entry)
        self._cart.pop(row)
        self.table.removeRow(row)
        self._calculate()

    def _calculate(self) -> None:
        try:
            subtotal = sum((entry.purchase_price for entry in self._cart), Decimal("0.00"))
            total = (
                subtotal - self.discount.decimal_value("Discount") + self.tax.decimal_value("Tax")
            )
        except Exception:
            return
        self.subtotal.setText(f"{self._settings.app_currency} {subtotal:,.2f}")
        self.total.setText(f"{self._settings.app_currency} {total:,.2f}")

    def save(self) -> None:
        command = CreatePurchaseCommand(
            supplier_id=self.supplier.currentData(),
            phones=tuple(
                PurchasedPhoneInput(
                    product_id=entry.product_id,
                    imei_1=entry.imei_1,
                    imei_2=entry.imei_2,
                    purchase_price=entry.purchase_price,
                    selling_price=entry.selling_price,
                )
                for entry in self._cart
            ),
            payments=self.payments.values(),
            discount=self.discount.decimal_value("Discount"),
            tax=self.tax.decimal_value("Tax"),
        )
        self.save_button.setEnabled(False)

        def operation() -> str:
            with self._session_factory.begin() as session:
                return PurchaseService(session).create(command, self._actor).purchase_number

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
            f"Purchase {purchase_number} and all inventory entries were committed.",
        )
        self._cart.clear()
        self.table.setRowCount(0)
        self.payments.clear()
        self.discount.setText("0.00")
        self.tax.setText("0.00")
        self._calculate()
