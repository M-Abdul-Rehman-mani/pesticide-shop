"""Product variants, stock thresholds, and audited default pricing."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import cast

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.enums import PhoneStatus
from app.models.inventory import PhoneInventory
from app.models.product import Product
from app.security.authentication import AuthenticatedUser
from app.services.catalog_service import ProductService
from app.ui.forms import MoneyEdit
from app.ui.widgets import RowsTableModel, show_error
from app.ui.workers import FunctionWorker, start_worker


@dataclass(frozen=True, slots=True)
class ProductFormData:
    brand: str
    model: str
    variant: str
    storage: str
    ram: str
    color: str
    purchase_price: Decimal
    sale_price: Decimal
    minimum_stock: int


class ProductDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Product")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.brand = QLineEdit()
        self.model = QLineEdit()
        self.variant = QLineEdit()
        self.storage = QLineEdit()
        self.ram = QLineEdit()
        self.color = QLineEdit()
        self.purchase = MoneyEdit()
        self.sale = MoneyEdit()
        self.minimum = QSpinBox()
        self.minimum.setRange(0, 100000)
        for label, widget in (
            ("Brand", self.brand),
            ("Model", self.model),
            ("Variant", self.variant),
            ("Storage", self.storage),
            ("RAM", self.ram),
            ("Color", self.color),
            ("Default purchase price", self.purchase),
            ("Default sale price", self.sale),
            ("Minimum stock", self.minimum),
        ):
            form.addRow(label, widget)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> ProductFormData:
        return ProductFormData(
            self.brand.text(),
            self.model.text(),
            self.variant.text(),
            self.storage.text(),
            self.ram.text(),
            self.color.text(),
            self.purchase.decimal_value("Purchase price"),
            self.sale.decimal_value("Sale price"),
            self.minimum.value(),
        )


class PriceDialog(QDialog):
    def __init__(
        self, purchase_price: Decimal, sale_price: Decimal, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Change Default Prices")
        layout = QFormLayout(self)
        self.purchase = MoneyEdit(f"{purchase_price:.2f}")
        self.sale = MoneyEdit(f"{sale_price:.2f}")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow("Purchase price", self.purchase)
        layout.addRow("Sale price", self.sale)
        layout.addRow(buttons)


class ProductsScreen(QWidget):
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        actor: AuthenticatedUser,
        currency: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session_factory = session_factory
        self._actor = actor
        self._currency = currency
        self._worker: FunctionWorker | None = None
        self._ids: list[uuid.UUID] = []
        self._prices: list[tuple[Decimal, Decimal]] = []
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("Products")
        title.setObjectName("PageTitle")
        add = QPushButton("Add Product")
        price = QPushButton("Change Prices")
        price.setProperty("secondary", True)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(price)
        header.addWidget(add)
        self.model = RowsTableModel(
            (
                "Brand",
                "Model",
                "Variant",
                "Storage",
                "RAM",
                "Color",
                "Purchase Price",
                "Sale Price",
                "In Stock",
                "Minimum",
            ),
            self,
        )
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        layout.addLayout(header)
        layout.addWidget(self.table)
        add.clicked.connect(self._add)
        price.clicked.connect(self._change_prices)
        self.refresh()

    def refresh(self) -> None:
        def operation() -> tuple[
            list[tuple[object, ...]], list[uuid.UUID], list[tuple[Decimal, Decimal]]
        ]:
            with self._session_factory() as session:
                stock = (
                    select(
                        PhoneInventory.product_id,
                        func.count(PhoneInventory.id).label("stock"),
                    )
                    .where(PhoneInventory.status == PhoneStatus.IN_STOCK)
                    .group_by(PhoneInventory.product_id)
                    .subquery()
                )
                rows = session.execute(
                    select(Product, func.coalesce(stock.c.stock, 0))
                    .outerjoin(stock, stock.c.product_id == Product.id)
                    .order_by(Product.brand, Product.model)
                )
                products = list(rows)
                return (
                    [
                        (
                            product.brand,
                            product.model,
                            product.variant,
                            product.storage,
                            product.ram,
                            product.color,
                            f"{self._currency} {product.default_purchase_price:,.2f}",
                            f"{self._currency} {product.default_sale_price:,.2f}",
                            count,
                            product.minimum_stock,
                        )
                        for product, count in products
                    ],
                    [product.id for product, _count in products],
                    [
                        (product.default_purchase_price, product.default_sale_price)
                        for product, _count in products
                    ],
                )

        self._worker = start_worker(
            operation, succeeded=self._display, failed=lambda error: show_error(self, error)
        )

    def _display(self, result: object) -> None:
        rows, self._ids, self._prices = cast(
            tuple[
                list[tuple[object, ...]],
                list[uuid.UUID],
                list[tuple[Decimal, Decimal]],
            ],
            result,
        )
        self.model.set_rows(rows)

    def _add(self) -> None:
        dialog = ProductDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        data = dialog.values()

        def operation() -> None:
            with self._session_factory.begin() as session:
                ProductService(session).create(
                    actor=self._actor,
                    brand=data.brand,
                    model=data.model,
                    variant=data.variant,
                    storage=data.storage,
                    ram=data.ram,
                    color=data.color,
                    default_purchase_price=data.purchase_price,
                    default_sale_price=data.sale_price,
                    minimum_stock=data.minimum_stock,
                )

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: self.refresh(),
            failed=lambda error: show_error(self, error),
        )

    def _change_prices(self) -> None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            QMessageBox.information(self, "Select product", "Select a product first.")
            return
        row = selected[0].row()
        dialog = PriceDialog(*self._prices[row], parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        def operation() -> None:
            with self._session_factory.begin() as session:
                ProductService(session).change_prices(
                    self._ids[row],
                    actor=self._actor,
                    purchase_price=dialog.purchase.decimal_value(),
                    sale_price=dialog.sale.decimal_value(),
                )

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: self.refresh(),
            failed=lambda error: show_error(self, error),
        )
