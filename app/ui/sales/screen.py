"""IMEI-first sale cart, exact totals, split payments, and receipt actions."""

from __future__ import annotations

import tempfile
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import cast

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
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
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.config.settings import Settings
from app.models.customer import Customer
from app.models.enums import PaymentDirection, SettingCategory
from app.models.payment import Payment
from app.models.sale import Sale
from app.printing.printer_service import PrinterService
from app.printing.receipt_generator import ReceiptGenerator, SaleReceiptData
from app.printing.shop_profile import load_shop_profile
from app.repositories.inventory_repository import InventoryRepository
from app.security.authentication import AuthenticatedUser
from app.services.dto import CreateSaleCommand, SaleLineInput
from app.services.sale_service import SaleService
from app.services.settings_service import SettingsService
from app.ui.forms import MoneyEdit, PaymentEditor
from app.ui.widgets import show_error
from app.ui.workers import FunctionWorker, start_worker
from app.utils.exceptions import ConflictError


@dataclass(frozen=True, slots=True)
class CartEntry:
    imei: str
    product: str
    price: Decimal


class SalesScreen(QWidget):
    sale_completed = Signal(str)

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
        self._cart: list[CartEntry] = []
        self._last_sale_id: uuid.UUID | None = None
        layout = QVBoxLayout(self)
        title = QLabel("New Sale")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        top = QHBoxLayout()
        customer_group = QGroupBox("Customer")
        customer_form = QFormLayout(customer_group)
        self.customer = QComboBox()
        self.customer.setMinimumWidth(280)
        self.customer.addItem("Walk-in Customer", None)
        customer_form.addRow("Customer", self.customer)
        scan_group = QGroupBox("Scan / search phone")
        scan_layout = QHBoxLayout(scan_group)
        self.imei = QLineEdit()
        self.imei.setPlaceholderText("15-digit IMEI")
        add_phone = QPushButton("Add to cart")
        scan_layout.addWidget(self.imei)
        scan_layout.addWidget(add_phone)
        top.addWidget(customer_group)
        top.addWidget(scan_group, 1)
        layout.addLayout(top)
        self.cart_table = QTableWidget(0, 4)
        self.cart_table.setHorizontalHeaderLabels(("Product", "IMEI", "Price", "Action"))
        self.cart_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.cart_table, 1)
        bottom = QHBoxLayout()
        payment_group = QGroupBox("Split payments")
        payment_layout = QVBoxLayout(payment_group)
        self.payments = PaymentEditor()
        payment_layout.addWidget(self.payments)
        totals_group = QGroupBox("Totals")
        totals_form = QFormLayout(totals_group)
        self.subtotal_label = QLabel(f"{settings.app_currency} 0.00")
        self.discount = MoneyEdit()
        self.tax = MoneyEdit()
        self.total_label = QLabel(f"{settings.app_currency} 0.00")
        self.total_label.setStyleSheet("font-size: 16pt; font-weight: 700; color: #17324d;")
        totals_form.addRow("Subtotal", self.subtotal_label)
        totals_form.addRow("Discount", self.discount)
        totals_form.addRow("Tax", self.tax)
        totals_form.addRow("Total", self.total_label)
        bottom.addWidget(payment_group, 2)
        bottom.addWidget(totals_group, 1)
        layout.addLayout(bottom)
        actions = QHBoxLayout()
        self.save_pdf = QPushButton("Save PDF")
        self.save_pdf.setProperty("secondary", True)
        self.preview = QPushButton("Print Preview")
        self.preview.setProperty("secondary", True)
        self.complete = QPushButton("Complete Sale")
        self.save_pdf.setEnabled(False)
        self.preview.setEnabled(False)
        actions.addWidget(self.save_pdf)
        actions.addWidget(self.preview)
        actions.addStretch()
        actions.addWidget(self.complete)
        layout.addLayout(actions)
        add_phone.clicked.connect(self._add_phone)
        self.imei.returnPressed.connect(self._add_phone)
        self.discount.textChanged.connect(self._calculate)
        self.tax.textChanged.connect(self._calculate)
        self.complete.clicked.connect(self.save)
        self.save_pdf.clicked.connect(self._save_last_pdf)
        self.preview.clicked.connect(self._preview_last)
        self._load_customers()

    def _load_customers(self) -> None:
        def operation() -> list[tuple[uuid.UUID, str]]:
            with self._session_factory() as session:
                customers = session.scalars(select(Customer).order_by(Customer.name).limit(500))
                return [
                    (customer.id, f"{customer.name} — {customer.phone}") for customer in customers
                ]

        self._worker = start_worker(
            operation,
            succeeded=self._set_customers,
            failed=lambda error: show_error(self, error),
        )

    def _set_customers(self, rows: object) -> None:
        for customer_id, label in cast(list[tuple[uuid.UUID, str]], rows):
            self.customer.addItem(label, customer_id)

    def _add_phone(self) -> None:
        imei = self.imei.text().strip()
        if any(entry.imei == imei for entry in self._cart):
            show_error(self, ConflictError("That IMEI is already in the cart."))
            return

        def operation() -> CartEntry:
            with self._session_factory() as session:
                phone = InventoryRepository(session).find_by_imei(imei)
                if phone is None:
                    from app.utils.exceptions import NotFoundError

                    raise NotFoundError("No phone was found with that IMEI.")
                return CartEntry(phone.imei_1, phone.product.display_name, phone.selling_price)

        self._worker = start_worker(
            operation,
            succeeded=self._phone_added,
            failed=lambda error: show_error(self, error),
        )

    def _phone_added(self, result: object) -> None:
        assert isinstance(result, CartEntry)
        self._cart.append(result)
        row = self.cart_table.rowCount()
        self.cart_table.insertRow(row)
        self.cart_table.setItem(row, 0, QTableWidgetItem(result.product))
        self.cart_table.setItem(row, 1, QTableWidgetItem(result.imei))
        self.cart_table.setItem(row, 2, QTableWidgetItem(f"{result.price:,.2f}"))
        remove = QPushButton("Remove")
        remove.setProperty("danger", True)
        remove.clicked.connect(lambda _checked=False, item=result: self._remove(item))
        self.cart_table.setCellWidget(row, 3, remove)
        self.imei.clear()
        self.imei.setFocus()
        self._calculate()

    def _remove(self, entry: CartEntry) -> None:
        index = self._cart.index(entry)
        self._cart.pop(index)
        self.cart_table.removeRow(index)
        self._calculate()

    def _calculate(self) -> None:
        try:
            subtotal = sum((entry.price for entry in self._cart), Decimal("0.00"))
            total = (
                subtotal - self.discount.decimal_value("Discount") + self.tax.decimal_value("Tax")
            )
        except Exception:
            return
        self.subtotal_label.setText(f"{self._settings.app_currency} {subtotal:,.2f}")
        self.total_label.setText(f"{self._settings.app_currency} {total:,.2f}")

    def save(self) -> None:
        command = CreateSaleCommand(
            customer_id=self.customer.currentData(),
            lines=tuple(SaleLineInput(imei=entry.imei) for entry in self._cart),
            payments=self.payments.values(),
            order_discount=self.discount.decimal_value("Discount"),
            tax=self.tax.decimal_value("Tax"),
        )
        self.complete.setEnabled(False)

        def operation() -> tuple[uuid.UUID, str]:
            with self._session_factory.begin() as session:
                stored = SettingsService(session, self._settings.app_secret_key.get_secret_value())
                sale = SaleService(session).create(
                    command,
                    self._actor,
                    invoice_prefix=stored.get(SettingCategory.GENERAL, "invoice_prefix", "INV")
                    or "INV",
                    shop_name=stored.get(SettingCategory.SHOP, "name", "Mobile Shop")
                    or "Mobile Shop",
                    owner_email=stored.get(
                        SettingCategory.EMAIL, "owner_email", self._settings.owner_email
                    ),
                )
                return sale.id, sale.invoice_number

        self._worker = start_worker(
            operation,
            succeeded=self._sale_saved,
            failed=lambda error: show_error(self, error),
            finished=lambda: self.complete.setEnabled(True),
        )

    def _sale_saved(self, result: object) -> None:
        sale_id, invoice = cast(tuple[uuid.UUID, str], result)
        self._last_sale_id = sale_id
        self.save_pdf.setEnabled(True)
        self.preview.setEnabled(True)
        QMessageBox.information(
            self,
            "Sale completed",
            f"Sale {invoice} was committed successfully. "
            "Email delivery will continue in the background.",
        )
        self.sale_completed.emit(invoice)
        self._cart.clear()
        self.cart_table.setRowCount(0)
        self.discount.setText("0.00")
        self.tax.setText("0.00")
        self.payments.clear()
        self._calculate()

    def _receipt_payload(self) -> bytes:
        if self._last_sale_id is None:
            raise ConflictError("Complete a sale before generating a receipt.")
        with self._session_factory() as session:
            sale = session.execute(
                select(Sale).options(selectinload(Sale.items)).where(Sale.id == self._last_sale_id)
            ).scalar_one()
            methods = session.scalars(
                select(Payment.method).where(
                    Payment.sale_id == sale.id,
                    Payment.direction == PaymentDirection.INCOMING,
                )
            )
            shop = load_shop_profile(session, self._settings)
            method_text = ", ".join(dict.fromkeys(method.value for method in methods)) or "UNPAID"
            return ReceiptGenerator().generate_a4(
                SaleReceiptData.from_sale(sale, method_text), shop
            )

    def _save_last_pdf(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save invoice", "invoice.pdf", "PDF documents (*.pdf)"
        )
        if not path:
            return

        def operation() -> Path:
            return PrinterService().save_pdf(Path(path), self._receipt_payload())

        self._worker = start_worker(
            operation,
            succeeded=lambda saved: QMessageBox.information(
                self, "Invoice saved", f"Saved to {saved}"
            ),
            failed=lambda error: show_error(self, error),
        )

    def _preview_last(self) -> None:
        def operation() -> str:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
                handle.write(self._receipt_payload())
                return handle.name

        self._worker = start_worker(
            operation,
            succeeded=lambda path: PrinterService().preview_pdf(Path(path), self),
            failed=lambda error: show_error(self, error),
        )
