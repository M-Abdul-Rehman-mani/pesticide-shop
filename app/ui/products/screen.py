"""Pesticide catalog, pack information, stock thresholds, and audited pricing."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import cast

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.inventory import StockBatch
from app.models.product import Product
from app.security.authentication import AuthenticatedUser
from app.services.catalog_service import ProductService
from app.ui.forms import MoneyEdit
from app.ui.widgets import RowsTableModel, populate_row_actions, show_error, show_record_details
from app.ui.workers import FunctionWorker, start_worker


@dataclass(frozen=True, slots=True)
class ProductFormData:
    name: str
    manufacturer: str
    active_ingredient: str
    formulation: str
    pack_size: str
    category: str
    registration_number: str
    unit: str
    description: str | None
    purchase_price: Decimal
    sale_price: Decimal
    minimum_stock: int


class ProductDialog(QDialog):
    def __init__(self, data: ProductFormData | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Pesticide Product" if data else "Add Pesticide Product")
        layout, form = QVBoxLayout(self), QFormLayout()
        self.name = QLineEdit(data.name if data else "")
        self.manufacturer = QLineEdit(data.manufacturer if data else "")
        self.active_ingredient, self.formulation, self.pack_size = (
            QLineEdit(data.active_ingredient if data else ""),
            QLineEdit(data.formulation if data else ""),
            QLineEdit(data.pack_size if data else ""),
        )
        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.addItems(
            (
                "HERBICIDE",
                "INSECTICIDE",
                "FUNGICIDE",
                "FERTILIZER",
                "SEED TREATMENT",
                "GROWTH REGULATOR",
                "OTHER",
            )
        )
        if data:
            self.category.setCurrentText(data.category)
        self.registration = QLineEdit(data.registration_number if data else "")
        self.unit = QComboBox()
        self.unit.setEditable(True)
        self.unit.addItems(("PACK", "BOTTLE", "BAG", "SACHET", "LITRE", "KG"))
        if data:
            self.unit.setCurrentText(data.unit)
        self.description = QTextEdit()
        self.description.setPlainText(data.description or "" if data else "")
        self.description.setMaximumHeight(60)
        self.purchase = MoneyEdit(f"{data.purchase_price:.2f}" if data else "")
        self.sale = MoneyEdit(f"{data.sale_price:.2f}" if data else "")
        self.minimum = QSpinBox()
        self.minimum.setRange(0, 1_000_000)
        self.minimum.setValue(data.minimum_stock if data else 0)
        for label, widget in (
            ("Product name", self.name),
            ("Manufacturer", self.manufacturer),
            ("Active ingredient", self.active_ingredient),
            ("Formulation", self.formulation),
            ("Pack size", self.pack_size),
            ("Category", self.category),
            ("Registration number", self.registration),
            ("Stock unit", self.unit),
            ("Description / usage", self.description),
            ("Default purchase price", self.purchase),
            ("Default sale price", self.sale),
            ("Low-stock threshold", self.minimum),
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
            self.name.text(),
            self.manufacturer.text(),
            self.active_ingredient.text(),
            self.formulation.text(),
            self.pack_size.text(),
            self.category.currentText(),
            self.registration.text(),
            self.unit.currentText(),
            self.description.toPlainText().strip() or None,
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
        self._session_factory, self._actor, self._currency = session_factory, actor, currency
        self._worker: FunctionWorker | None = None
        self._ids: list[uuid.UUID] = []
        self._prices: list[tuple[Decimal, Decimal]] = []
        self._data: list[ProductFormData] = []
        layout, header = QVBoxLayout(self), QHBoxLayout()
        title = QLabel("Pesticide Products")
        title.setObjectName("PageTitle")
        add, price = QPushButton("Add Product"), QPushButton("Change Prices")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search product, manufacturer, ingredient, or category")
        self.search.setClearButtonEnabled(True)
        price.setProperty("secondary", True)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.search, 1)
        header.addWidget(price)
        header.addWidget(add)
        self.model = RowsTableModel(
            (
                "Product",
                "Manufacturer",
                "Active Ingredient",
                "Formulation",
                "Pack",
                "Category",
                "Purchase",
                "Sale",
                "Available",
                "Minimum",
                "Actions",
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
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda _text: self._search_timer.start())
        self.refresh()

    def refresh(self) -> None:
        query = self.search.text().strip()

        def operation() -> tuple[
            list[tuple[object, ...]],
            list[uuid.UUID],
            list[tuple[Decimal, Decimal]],
            list[ProductFormData],
        ]:
            with self._session_factory() as session:
                stock = (
                    select(
                        StockBatch.product_id,
                        func.sum(StockBatch.quantity_available).label("stock"),
                    )
                    .where(StockBatch.is_active.is_(True))
                    .group_by(StockBatch.product_id)
                    .subquery()
                )
                statement = (
                    select(Product, func.coalesce(stock.c.stock, 0))
                    .outerjoin(stock, stock.c.product_id == Product.id)
                    .where(Product.is_active.is_(True))
                )
                if query:
                    pattern = f"%{query}%"
                    statement = statement.where(
                        or_(
                            Product.name.ilike(pattern),
                            Product.manufacturer.ilike(pattern),
                            Product.active_ingredient.ilike(pattern),
                            Product.category.ilike(pattern),
                            Product.formulation.ilike(pattern),
                        )
                    )
                products = list(
                    session.execute(statement.order_by(Product.manufacturer, Product.name))
                )
                return (
                    [
                        (
                            p.product_name,
                            p.manufacturer,
                            p.active_ingredient or "—",
                            p.formulation or "—",
                            p.pack_size or "—",
                            p.category,
                            f"{self._currency} {p.default_purchase_price:,.2f}",
                            f"{self._currency} {p.default_sale_price:,.2f}",
                            count,
                            p.minimum_stock,
                            "",
                        )
                        for p, count in products
                    ],
                    [p.id for p, _ in products],
                    [(p.default_purchase_price, p.default_sale_price) for p, _ in products],
                    [
                        ProductFormData(
                            p.name,
                            p.manufacturer,
                            p.active_ingredient,
                            p.formulation,
                            p.pack_size,
                            p.category,
                            p.registration_number,
                            p.unit,
                            p.description,
                            p.default_purchase_price,
                            p.default_sale_price,
                            p.minimum_stock,
                        )
                        for p, _ in products
                    ],
                )

        self._worker = start_worker(
            operation, succeeded=self._display, failed=lambda error: show_error(self, error)
        )

    def _display(self, result: object) -> None:
        rows, self._ids, self._prices, self._data = cast(
            tuple[
                list[tuple[object, ...]],
                list[uuid.UUID],
                list[tuple[Decimal, Decimal]],
                list[ProductFormData],
            ],
            result,
        )
        self.model.set_rows(rows)
        populate_row_actions(
            self.table,
            10,
            len(rows),
            (("View", self._view), ("Edit", self._edit), ("Delete", self._delete)),
        )

    def _add(self) -> None:
        dialog = ProductDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._save_product(None, dialog.values())

    def _edit(self, row: int) -> None:
        if row >= len(self._data):
            return
        dialog = ProductDialog(self._data[row], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._save_product(self._ids[row], dialog.values())

    def _save_product(self, product_id: uuid.UUID | None, data: ProductFormData) -> None:

        def operation() -> None:
            with self._session_factory.begin() as session:
                service = ProductService(session)
                if product_id:
                    service.update(
                        product_id,
                        actor=self._actor,
                        manufacturer=data.manufacturer,
                        name=data.name,
                        active_ingredient=data.active_ingredient,
                        formulation=data.formulation,
                        pack_size=data.pack_size,
                        category=data.category,
                        registration_number=data.registration_number,
                        unit=data.unit,
                        description=data.description,
                        default_purchase_price=data.purchase_price,
                        default_sale_price=data.sale_price,
                        minimum_stock=data.minimum_stock,
                    )
                else:
                    service.create(
                        actor=self._actor,
                        manufacturer=data.manufacturer,
                        name=data.name,
                        active_ingredient=data.active_ingredient,
                        formulation=data.formulation,
                        pack_size=data.pack_size,
                        category=data.category,
                        registration_number=data.registration_number,
                        unit=data.unit,
                        description=data.description,
                        default_purchase_price=data.purchase_price,
                        default_sale_price=data.sale_price,
                        minimum_stock=data.minimum_stock,
                    )

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: self.refresh(),
            failed=lambda error: show_error(self, error),
        )

    def _view(self, row: int) -> None:
        if row >= len(self._data):
            return
        data = self._data[row]
        show_record_details(
            self,
            data.name,
            (
                ("Manufacturer", data.manufacturer),
                ("Active ingredient", data.active_ingredient),
                ("Formulation", data.formulation),
                ("Pack size", data.pack_size),
                ("Category", data.category),
                ("Registration", data.registration_number),
                ("Unit", data.unit),
                ("Purchase price", f"{self._currency} {data.purchase_price:,.2f}"),
                ("Sale price", f"{self._currency} {data.sale_price:,.2f}"),
                ("Low-stock threshold", data.minimum_stock),
                ("Description", data.description),
            ),
        )

    def _delete(self, row: int) -> None:
        if row >= len(self._ids):
            return
        if (
            QMessageBox.question(
                self,
                "Delete product",
                "Remove this product from active lists? "
                "Existing stock and sales history are preserved.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        def operation() -> None:
            with self._session_factory.begin() as session:
                ProductService(session).set_active(
                    self._ids[row], actor=self._actor, is_active=False
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
