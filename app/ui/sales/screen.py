"""Pesticide sales by batch with customer/dealer invoices and receipt actions."""

from __future__ import annotations

import tempfile
import uuid
from dataclasses import dataclass
from datetime import date
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
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.config.settings import Settings
from app.models.customer import Customer
from app.models.dealer import Dealer
from app.models.enums import PaymentDirection, SettingCategory
from app.models.inventory import StockBatch
from app.models.payment import Payment
from app.models.sale import Sale
from app.printing.printer_service import PrinterService
from app.printing.receipt_generator import ReceiptGenerator, SaleReceiptData
from app.printing.shop_profile import load_shop_profile
from app.security.authentication import AuthenticatedUser
from app.services.dto import CreatePesticideSaleCommand, PesticideSaleLineInput
from app.services.pesticide_sale_service import PesticideSaleService
from app.services.settings_service import SettingsService
from app.ui.forms import MoneyEdit, PaymentEditor
from app.ui.widgets import show_error
from app.ui.workers import FunctionWorker, start_worker
from app.utils.exceptions import ConflictError


@dataclass(slots=True)
class CartEntry:
    batch_id: uuid.UUID
    product: str
    batch_number: str
    available: int = 1
    quantity: int = 1
    unit_price: Decimal = Decimal("0.00")


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
        self._session_factory, self._actor, self._settings = session_factory, actor, settings
        self._worker: FunctionWorker | None = None
        self._cart: list[CartEntry] = []
        self._customers: list[tuple[uuid.UUID, str, str | None, str | None]] = []
        self._dealers: list[tuple[uuid.UUID, str, str | None, str | None]] = []
        self._last_sale_id: uuid.UUID | None = None
        layout = QVBoxLayout(self)
        title = QLabel("New Pesticide Sale / Delivery Challan")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        top = QHBoxLayout()
        recipient_group, recipient_form = QGroupBox("Invoice Recipient"), QFormLayout()
        recipient_group.setLayout(recipient_form)
        self.recipient_type, self.recipient = QComboBox(), QComboBox()
        self.recipient_type.addItems(("Customer", "Dealer"))
        self.recipient.setMinimumWidth(300)
        recipient_form.addRow("Type", self.recipient_type)
        recipient_form.addRow("Name", self.recipient)
        details_group, details_form = QGroupBox("Delivery Details"), QFormLayout()
        details_group.setLayout(details_form)
        self.order_number, self.territory = QLineEdit(), QLineEdit()
        self.policy, self.store = QLineEdit("NET SALE"), QLineEdit("FINISHED")
        self.delivery_address = QLineEdit()
        for field_label, field_widget in (
            ("Order #", self.order_number),
            ("Territory", self.territory),
            ("Policy", self.policy),
            ("Store", self.store),
            ("Delivery address", self.delivery_address),
        ):
            details_form.addRow(field_label, field_widget)
        top.addWidget(recipient_group)
        top.addWidget(details_group, 1)
        layout.addLayout(top)

        stock_group, stock_layout = QGroupBox("Add Product Batch"), QHBoxLayout()
        stock_group.setLayout(stock_layout)
        self.batch, self.quantity, self.unit_price = QComboBox(), QSpinBox(), MoneyEdit()
        self.batch.setMinimumWidth(440)
        self.quantity.setRange(1, 1_000_000)
        add = QPushButton("Add to Cart")
        for stock_widget in (
            QLabel("Product / batch"),
            self.batch,
            QLabel("Qty"),
            self.quantity,
            QLabel("Unit price"),
            self.unit_price,
            add,
        ):
            stock_layout.addWidget(stock_widget, 1 if stock_widget is self.batch else 0)
        layout.addWidget(stock_group)
        self.cart_table = QTableWidget(0, 7)
        self.cart_table.setHorizontalHeaderLabels(
            ("Product", "Batch", "Qty", "Unit", "Line Discount", "Total", "Action")
        )
        self.cart_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.cart_table, 1)

        bottom = QHBoxLayout()
        payment_group, payment_layout = QGroupBox("Payments"), QVBoxLayout()
        payment_group.setLayout(payment_layout)
        self.payments = PaymentEditor()
        payment_layout.addWidget(self.payments)
        totals_group, totals_form = QGroupBox("Invoice Totals"), QFormLayout()
        totals_group.setLayout(totals_form)
        self.subtotal_label = QLabel(f"{settings.app_currency} 0.00")
        self.discount, self.tax = MoneyEdit(), MoneyEdit()
        self.total_label = QLabel(f"{settings.app_currency} 0.00")
        self.total_label.setStyleSheet("font-size: 16pt; font-weight: 700; color: #17324d;")
        for total_label, total_widget in (
            ("Subtotal", self.subtotal_label),
            ("Order discount", self.discount),
            ("Tax", self.tax),
            ("Total", self.total_label),
        ):
            totals_form.addRow(total_label, total_widget)
        bottom.addWidget(payment_group, 2)
        bottom.addWidget(totals_group, 1)
        layout.addLayout(bottom)
        actions = QHBoxLayout()
        self.save_pdf, self.preview = QPushButton("Save PDF"), QPushButton("Print Preview")
        self.complete = QPushButton("Complete Sale & Queue Emails")
        self.save_pdf.setProperty("secondary", True)
        self.preview.setProperty("secondary", True)
        self.save_pdf.setEnabled(False)
        self.preview.setEnabled(False)
        actions.addWidget(self.save_pdf)
        actions.addWidget(self.preview)
        actions.addStretch()
        actions.addWidget(self.complete)
        layout.addLayout(actions)

        self.recipient_type.currentIndexChanged.connect(self._set_recipients)
        self.recipient.currentIndexChanged.connect(self._recipient_changed)
        self.batch.currentIndexChanged.connect(self._batch_changed)
        add.clicked.connect(self._add_batch)
        self.discount.textChanged.connect(self._calculate)
        self.tax.textChanged.connect(self._calculate)
        self.complete.clicked.connect(self.save)
        self.save_pdf.clicked.connect(self._save_last_pdf)
        self.preview.clicked.connect(self._preview_last)
        self._load_choices()

    def _load_choices(self) -> None:
        def operation() -> tuple[
            list[tuple[uuid.UUID, str, str | None, str | None]],
            list[tuple[uuid.UUID, str, str | None, str | None]],
            list[tuple[uuid.UUID, str, str, int, Decimal]],
        ]:
            with self._session_factory() as session:
                customers = list(
                    session.scalars(select(Customer).order_by(Customer.name).limit(500))
                )
                dealers = list(
                    session.scalars(
                        select(Dealer)
                        .where(Dealer.is_active.is_(True))
                        .order_by(Dealer.business_name, Dealer.name)
                        .limit(500)
                    )
                )
                batches = list(
                    session.scalars(
                        select(StockBatch)
                        .where(
                            StockBatch.is_active.is_(True),
                            StockBatch.quantity_available > 0,
                            (
                                StockBatch.expiry_date.is_(None)
                                | (StockBatch.expiry_date >= date.today())
                            ),
                        )
                        .order_by(StockBatch.expiry_date.asc().nullslast(), StockBatch.created_at)
                    )
                )
                return (
                    [(c.id, f"{c.name} — {c.phone}", c.address, None) for c in customers],
                    [
                        (d.id, f"{d.display_name} — {d.phone}", d.address, d.territory)
                        for d in dealers
                    ],
                    [
                        (
                            b.id,
                            b.product.display_name,
                            b.batch_number,
                            b.quantity_available,
                            b.selling_price,
                        )
                        for b in batches
                    ],
                )

        self._worker = start_worker(
            operation, succeeded=self._choices_loaded, failed=lambda error: show_error(self, error)
        )

    def _choices_loaded(self, result: object) -> None:
        self._customers, self._dealers, batches = cast(
            tuple[
                list[tuple[uuid.UUID, str, str | None, str | None]],
                list[tuple[uuid.UUID, str, str | None, str | None]],
                list[tuple[uuid.UUID, str, str, int, Decimal]],
            ],
            result,
        )
        self.batch.clear()
        for batch_id, product, number, available, price in batches:
            self.batch.addItem(
                f"{product} | Batch {number} | Available {available}",
                (batch_id, product, number, available, price),
            )
        self._set_recipients()
        self._batch_changed()

    def _set_recipients(self) -> None:
        self.recipient.clear()
        is_dealer = self.recipient_type.currentText() == "Dealer"
        if not is_dealer:
            self.recipient.addItem("Walk-in Customer", None)
        for item_id, label, address, territory in self._dealers if is_dealer else self._customers:
            self.recipient.addItem(label, (item_id, address, territory))
        self._recipient_changed()

    def _recipient_changed(self) -> None:
        if data := self.recipient.currentData():
            _recipient_id, address, territory = data
            self.delivery_address.setText(address or "")
            if territory:
                self.territory.setText(territory)

    def _batch_changed(self) -> None:
        if data := self.batch.currentData():
            _batch_id, _product, _number, available, price = data
            self.quantity.setMaximum(available)
            self.unit_price.setText(f"{price:.2f}")

    def _add_batch(self) -> None:
        data = self.batch.currentData()
        if data is None:
            show_error(self, ConflictError("No available stock batch was found."))
            return
        batch_id, product, number, available, _price = data
        if any(item.batch_id == batch_id for item in self._cart):
            show_error(self, ConflictError("That batch is already in the cart."))
            return
        entry = CartEntry(
            batch_id,
            product,
            number,
            available,
            self.quantity.value(),
            self.unit_price.decimal_value("Unit price"),
        )
        self._cart.append(entry)
        row = self.cart_table.rowCount()
        self.cart_table.insertRow(row)
        self.cart_table.setItem(row, 0, QTableWidgetItem(product))
        self.cart_table.setItem(row, 1, QTableWidgetItem(number))
        quantity = QSpinBox()
        quantity.setRange(1, available)
        quantity.setValue(entry.quantity)
        quantity.valueChanged.connect(self._calculate)
        self.cart_table.setCellWidget(row, 2, quantity)
        unit_price = MoneyEdit(f"{entry.unit_price:.2f}")
        unit_price.textChanged.connect(self._calculate)
        self.cart_table.setCellWidget(row, 3, unit_price)
        line_discount = MoneyEdit()
        line_discount.textChanged.connect(self._calculate)
        self.cart_table.setCellWidget(row, 4, line_discount)
        self.cart_table.setItem(row, 5, QTableWidgetItem("0.00"))
        remove = QPushButton("Remove")
        remove.setProperty("danger", True)
        remove.clicked.connect(lambda _checked=False, item=entry: self._remove(item))
        self.cart_table.setCellWidget(row, 6, remove)
        self._calculate()

    def _remove(self, entry: CartEntry) -> None:
        row = self._cart.index(entry)
        self._cart.pop(row)
        self.cart_table.removeRow(row)
        self._calculate()

    def _sale_lines(self) -> tuple[PesticideSaleLineInput, ...]:
        lines: list[PesticideSaleLineInput] = []
        for row, entry in enumerate(self._cart):
            quantity, price, discount = (
                self.cart_table.cellWidget(row, index) for index in (2, 3, 4)
            )
            assert isinstance(quantity, QSpinBox) and isinstance(price, MoneyEdit)
            assert isinstance(discount, MoneyEdit)
            lines.append(
                PesticideSaleLineInput(
                    entry.batch_id,
                    quantity.value(),
                    price.decimal_value("Unit price"),
                    discount.decimal_value("Line discount"),
                )
            )
        return tuple(lines)

    def _calculate(self) -> None:
        try:
            subtotal = line_discounts = Decimal("0.00")
            for row, line in enumerate(self._sale_lines()):
                gross = (line.unit_price or Decimal("0.00")) * line.quantity
                subtotal += gross
                line_discounts += line.discount
                if item := self.cart_table.item(row, 5):
                    item.setText(f"{gross - line.discount:,.2f}")
            total = (
                subtotal - line_discounts - self.discount.decimal_value() + self.tax.decimal_value()
            )
        except Exception:
            return
        self.subtotal_label.setText(f"{self._settings.app_currency} {subtotal:,.2f}")
        self.total_label.setText(f"{self._settings.app_currency} {total:,.2f}")

    def save(self) -> None:
        recipient = self.recipient.currentData()
        recipient_id = recipient[0] if recipient else None
        is_dealer = self.recipient_type.currentText() == "Dealer"
        command = CreatePesticideSaleCommand(
            lines=self._sale_lines(),
            payments=self.payments.values(),
            customer_id=None if is_dealer else recipient_id,
            dealer_id=recipient_id if is_dealer else None,
            order_discount=self.discount.decimal_value(),
            tax=self.tax.decimal_value(),
            order_number=self.order_number.text().strip() or None,
            territory=self.territory.text().strip() or None,
            delivery_address=self.delivery_address.text().strip() or None,
            policy=self.policy.text().strip() or None,
            store=self.store.text().strip() or None,
        )
        self.complete.setEnabled(False)

        def operation() -> tuple[uuid.UUID, str]:
            with self._session_factory.begin() as session:
                stored = SettingsService(session, self._settings.app_secret_key.get_secret_value())
                sale = PesticideSaleService(session).create(
                    command,
                    self._actor,
                    invoice_prefix=stored.get(SettingCategory.GENERAL, "invoice_prefix", "INV")
                    or "INV",
                    shop_name=stored.get(SettingCategory.SHOP, "name", "Pesticide Shop")
                    or "Pesticide Shop",
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
            f"Invoice {invoice} was saved and recipient/owner emails were queued.",
        )
        self.sale_completed.emit(invoice)
        self._cart.clear()
        self.cart_table.setRowCount(0)
        self.discount.setText("0.00")
        self.tax.setText("0.00")
        self.payments.clear()
        self._calculate()
        self._load_choices()

    def _receipt_payload(self) -> bytes:
        if self._last_sale_id is None:
            raise ConflictError("Complete a sale before generating an invoice.")
        with self._session_factory() as session:
            sale = session.execute(
                select(Sale).options(selectinload(Sale.items)).where(Sale.id == self._last_sale_id)
            ).scalar_one()
            methods = session.scalars(
                select(Payment.method).where(
                    Payment.sale_id == sale.id, Payment.direction == PaymentDirection.INCOMING
                )
            )
            method_text = ", ".join(dict.fromkeys(m.value for m in methods)) or "UNPAID"
            return ReceiptGenerator().generate_a4(
                SaleReceiptData.from_sale(sale, method_text),
                load_shop_profile(session, self._settings),
            )

    def _save_last_pdf(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save invoice", "delivery-challan-invoice.pdf", "PDF documents (*.pdf)"
        )
        if path:
            self._worker = start_worker(
                lambda: PrinterService().save_pdf(Path(path), self._receipt_payload()),
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
