"""Pesticide sales by batch with customer/dealer invoices and receipt actions."""

from __future__ import annotations

import tempfile
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.config.settings import Settings
from app.models.customer import Customer
from app.models.dealer import Dealer
from app.models.enums import PaymentDirection, SettingCategory
from app.models.inventory import StockBatch
from app.models.payment import Payment
from app.models.product import Product
from app.models.sale import Sale, SaleItem
from app.printing.printer_service import PrinterService
from app.printing.receipt_generator import ReceiptGenerator, SaleReceiptData
from app.printing.shop_profile import load_shop_profile
from app.security.authentication import AuthenticatedUser
from app.services.catalog_service import CustomerService
from app.services.dto import CreatePesticideSaleCommand, PesticideSaleLineInput
from app.services.pesticide_sale_service import PesticideSaleService
from app.services.settings_service import SettingsService
from app.ui.forms import MoneyEdit, PaymentEditor
from app.ui.widgets import (
    PageHeader,
    RowsTableModel,
    configure_searchable_combo,
    configure_table,
    populate_row_actions,
    show_error,
    show_record_details,
    wrap_scroll,
)
from app.ui.workers import FunctionWorker, start_worker
from app.utils.exceptions import ConflictError
from app.utils.formatting import format_date


@dataclass(slots=True)
class CartEntry:
    batch_id: uuid.UUID
    product: str
    batch_number: str
    available: int = 1
    quantity: int = 1
    unit_price: Decimal = Decimal("0.00")


class WalkInCustomerDialog(QDialog):
    """Small counter-sale form; only the customer name is required."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add walk-in customer")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name, self.phone, self.email, self.address = (
            QLineEdit(),
            QLineEdit(),
            QLineEdit(),
            QLineEdit(),
        )
        self.name.setPlaceholderText("Required")
        self.phone.setPlaceholderText("Optional")
        self.email.setPlaceholderText("Optional")
        self.address.setPlaceholderText("Optional")
        form.addRow("Name *", self.name)
        form.addRow("Phone", self.phone)
        form.addRow("Email", self.email)
        form.addRow("Address", self.address)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _accept_if_valid(self) -> None:
        if not self.name.text().strip():
            QMessageBox.information(self, "Name required", "Enter the walk-in customer's name.")
            self.name.setFocus()
            return
        self.accept()


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
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 12, 16, 10)
        root_layout.setSpacing(8)
        root_layout.addWidget(
            PageHeader(
                "Sales",
                "Create a sale or search and review previous invoices.",
            )
        )
        self.tabs = QTabWidget()
        root_layout.addWidget(self.tabs, 1)
        sale_page = QWidget()
        layout = QVBoxLayout(sale_page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        self.tabs.addTab(wrap_scroll(sale_page), "New Sale")

        top = QHBoxLayout()
        recipient_group, recipient_form = QGroupBox("Invoice Recipient"), QFormLayout()
        recipient_group.setLayout(recipient_form)
        self.recipient_type, self.recipient = QComboBox(), QComboBox()
        self.recipient_type.addItems(("Customer", "Dealer"))
        configure_searchable_combo(self.recipient, "Type to find a customer or dealer")
        add_walk_in = QPushButton("+ Add walk-in customer")
        add_walk_in.setProperty("secondary", True)
        recipient_form.addRow("Type", self.recipient_type)
        recipient_form.addRow("Name", self.recipient)
        recipient_form.addRow("", add_walk_in)
        details_group, details_form = QGroupBox("Delivery Details"), QFormLayout()
        details_group.setLayout(details_form)
        self.order_number, self.territory = QLineEdit(), QLineEdit()
        self.policy, self.store = QLineEdit("NET SALE"), QLineEdit("FINISHED")
        self.delivery_address, self.notes = QLineEdit(), QLineEdit()
        self.order_number.setPlaceholderText("Optional customer order reference")
        self.territory.setPlaceholderText("Sales territory")
        self.delivery_address.setPlaceholderText("Address printed on the invoice")
        self.notes.setPlaceholderText("Optional invoice note")
        for field_label, field_widget in (
            ("Order #", self.order_number),
            ("Territory", self.territory),
            ("Policy", self.policy),
            ("Store", self.store),
            ("Delivery address", self.delivery_address),
            ("Notes", self.notes),
        ):
            details_form.addRow(field_label, field_widget)
        top.addWidget(recipient_group)
        top.addWidget(details_group, 1)
        self._top_layout = top
        layout.addLayout(top)

        stock_group, stock_layout = QGroupBox("Add Product Batch"), QHBoxLayout()
        stock_group.setLayout(stock_layout)
        self.batch, self.quantity, self.unit_price = QComboBox(), QSpinBox(), MoneyEdit()
        configure_searchable_combo(self.batch, "Type a product or batch number")
        self.quantity.setRange(1, 1_000_000)
        add = QPushButton("+  Add batch")
        add.setToolTip("Add this batch and quantity to the invoice")
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
        configure_table(self.cart_table, stretch_column=0, minimum_section_size=80)
        layout.addWidget(self.cart_table, 1)
        self.cart_count = QLabel("0 items in invoice")
        self.cart_count.setObjectName("RecordCount")
        layout.addWidget(self.cart_count)

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
        self.total_label.setObjectName("GrandTotal")
        self.total_label.setStyleSheet("color: #176d49;")
        for total_label, total_widget in (
            ("Subtotal", self.subtotal_label),
            ("Order discount", self.discount),
            ("Tax", self.tax),
            ("Total", self.total_label),
        ):
            totals_form.addRow(total_label, total_widget)
        bottom.addWidget(payment_group, 2)
        bottom.addWidget(totals_group, 1)
        self._bottom_layout = bottom
        layout.addLayout(bottom)
        actions = QHBoxLayout()
        self.save_pdf, self.preview = QPushButton("Save PDF"), QPushButton("Print Preview")
        self.complete = QPushButton("Complete Sale & Queue Emails")
        self.save_pdf.setProperty("secondary", True)
        self.preview.setProperty("secondary", True)
        self.save_pdf.setEnabled(False)
        self.preview.setEnabled(False)
        self.complete.setEnabled(False)
        self.complete.setToolTip("Save the sale, reduce stock, and queue invoice emails")
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
        add_walk_in.clicked.connect(self._add_walk_in_customer)
        self._build_sales_history_tab()
        self._load_choices()
        self._update_cart_state()

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)  # type: ignore[arg-type]
        direction = (
            QBoxLayout.Direction.TopToBottom
            if self.width() < 940
            else QBoxLayout.Direction.LeftToRight
        )
        self._top_layout.setDirection(direction)
        self._bottom_layout.setDirection(direction)

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
                        .join(StockBatch.product)
                        .where(
                            Product.is_active.is_(True),
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
                    [
                        (
                            c.id,
                            f"{c.name} — {c.phone}" if c.phone else c.name,
                            c.address,
                            None,
                        )
                        for c in customers
                    ],
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

    def refresh(self) -> None:
        self._load_choices()
        if self.tabs.currentIndex() == 1:
            self._refresh_sales_history()

    def _add_walk_in_customer(self) -> None:
        if self.recipient_type.currentText() != "Customer":
            self.recipient_type.setCurrentText("Customer")
        dialog = WalkInCustomerDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.name.text().strip()
        phone = dialog.phone.text().strip()
        email = dialog.email.text().strip() or None
        address = dialog.address.text().strip() or None

        def operation() -> tuple[uuid.UUID, str, str | None]:
            with self._session_factory.begin() as session:
                customer = CustomerService(session).create(
                    actor=self._actor,
                    name=name,
                    phone=phone,
                    email=email,
                    address=address,
                    notes="Added during walk-in sale",
                )
                return customer.id, customer.phone, customer.address

        def added(result: object) -> None:
            customer_id, stored_phone, stored_address = cast(
                tuple[uuid.UUID, str, str | None], result
            )
            label = f"{name} — {stored_phone}" if stored_phone else name
            self._customers.append((customer_id, label, stored_address, None))
            self._customers.sort(key=lambda customer: customer[1].casefold())
            self._set_recipients()
            for index in range(self.recipient.count()):
                data = self.recipient.itemData(index)
                if data and data[0] == customer_id:
                    self.recipient.setCurrentIndex(index)
                    break

        self._worker = start_worker(
            operation, succeeded=added, failed=lambda error: show_error(self, error)
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
            self.recipient.addItem("Walk-in (no details)", None)
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
        self._update_cart_state()

    def _remove(self, entry: CartEntry) -> None:
        row = self._cart.index(entry)
        self._cart.pop(row)
        self.cart_table.removeRow(row)
        self._calculate()
        self._update_cart_state()

    def _update_cart_state(self) -> None:
        count = len(self._cart)
        self.cart_count.setText(f"{count} {'item' if count == 1 else 'items'} in invoice")
        self.complete.setEnabled(count > 0)

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
        if self.recipient.currentIndex() < 0:
            QMessageBox.information(
                self,
                "Select recipient",
                "Choose a recipient from the list, or add a walk-in customer.",
            )
            return
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
            notes=self.notes.text().strip() or None,
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
            finished=self._update_cart_state,
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
        self.notes.clear()
        self._calculate()
        self._update_cart_state()
        self._load_choices()
        self._refresh_sales_history()

    def _build_sales_history_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        search_row = QHBoxLayout()
        self.sales_search = QLineEdit()
        self.sales_search.setPlaceholderText(
            "Search invoice, customer, dealer, phone, order, or product"
        )
        self.sales_search.setClearButtonEnabled(True)
        refresh = QPushButton("Refresh")
        refresh.setProperty("secondary", True)
        search_row.addWidget(QLabel("Search"))
        search_row.addWidget(self.sales_search, 1)
        search_row.addWidget(refresh)
        self.sales_model = RowsTableModel(
            (
                "Invoice",
                "Date",
                "Recipient",
                "Products",
                "Total",
                "Paid",
                "Balance",
                "Status",
                "Actions",
            ),
            page,
        )
        self.sales_table = QTableView()
        self.sales_table.setModel(self.sales_model)
        configure_table(self.sales_table, stretch_column=3, minimum_section_size=76)
        self.sales_count = QLabel("Loading sales…")
        self.sales_count.setObjectName("RecordCount")
        layout.addLayout(search_row)
        layout.addWidget(self.sales_table, 1)
        layout.addWidget(self.sales_count)
        self.tabs.addTab(page, "All Sales")
        self._history_sales: list[Sale] = []
        self._sales_search_timer = QTimer(self)
        self._sales_search_timer.setSingleShot(True)
        self._sales_search_timer.setInterval(300)
        self._sales_search_timer.timeout.connect(self._refresh_sales_history)
        self.sales_search.textChanged.connect(lambda _text: self._sales_search_timer.start())
        self.sales_search.returnPressed.connect(self._refresh_sales_history)
        refresh.clicked.connect(self._refresh_sales_history)
        self.tabs.currentChanged.connect(
            lambda index: self._refresh_sales_history() if index == 1 else None
        )

    def _refresh_sales_history(self) -> None:
        query = self.sales_search.text().strip()

        def operation() -> list[Sale]:
            with self._session_factory() as session:
                statement = select(Sale).options(selectinload(Sale.items))
                if query:
                    pattern = f"%{query}%"
                    statement = statement.where(
                        or_(
                            Sale.invoice_number.ilike(pattern),
                            Sale.order_number.ilike(pattern),
                            Sale.customer.has(
                                or_(Customer.name.ilike(pattern), Customer.phone.ilike(pattern))
                            ),
                            Sale.dealer.has(
                                or_(
                                    Dealer.name.ilike(pattern),
                                    Dealer.business_name.ilike(pattern),
                                    Dealer.phone.ilike(pattern),
                                )
                            ),
                            Sale.items.any(
                                SaleItem.product.has(
                                    or_(
                                        Product.name.ilike(pattern),
                                        Product.manufacturer.ilike(pattern),
                                    )
                                )
                            ),
                        )
                    )
                sales = list(session.scalars(statement.order_by(Sale.sale_date.desc()).limit(500)))
                for sale in sales:
                    _ = [(item.product.display_name, item.quantity) for item in sale.items]
                session.expunge_all()
                return sales

        self._worker = start_worker(
            operation,
            succeeded=self._display_sales_history,
            failed=lambda error: show_error(self, error),
        )

    def _display_sales_history(self, result: object) -> None:
        self._history_sales = cast(list[Sale], result)
        rows: list[tuple[object, ...]] = []
        for sale in self._history_sales:
            recipient = (
                sale.dealer.display_name
                if sale.dealer
                else sale.customer.name
                if sale.customer
                else "Walk-in"
            )
            products = ", ".join(
                f"{item.product.display_name} x {item.quantity}" for item in sale.items
            )
            rows.append(
                (
                    sale.invoice_number,
                    format_date(sale.sale_date),
                    recipient,
                    products,
                    f"{self._settings.app_currency} {sale.total:,.2f}",
                    f"{self._settings.app_currency} {sale.paid_amount:,.2f}",
                    f"{self._settings.app_currency} {sale.remaining_amount:,.2f}",
                    sale.status.value,
                    "",
                )
            )
        self.sales_model.set_rows(rows)
        populate_row_actions(
            self.sales_table,
            8,
            len(rows),
            (("View details", self._view_sale), ("Print preview", self._preview_sale)),
        )
        self.sales_count.setText(f"{len(rows)} sale{'s' if len(rows) != 1 else ''}")

    def _view_sale(self, row: int) -> None:
        if row >= len(self._history_sales):
            return
        sale = self._history_sales[row]
        recipient = (
            sale.dealer.display_name
            if sale.dealer
            else sale.customer.name
            if sale.customer
            else "Walk-in"
        )
        products = "\n".join(
            f"{item.product.display_name} — {item.quantity} x {item.unit_price:,.2f}"
            for item in sale.items
        )
        show_record_details(
            self,
            f"Sale {sale.invoice_number}",
            (
                ("Invoice", sale.invoice_number),
                ("Date", format_date(sale.sale_date)),
                ("Recipient", recipient),
                ("Products", products),
                ("Total", f"{self._settings.app_currency} {sale.total:,.2f}"),
                ("Paid", f"{self._settings.app_currency} {sale.paid_amount:,.2f}"),
                ("Balance", f"{self._settings.app_currency} {sale.remaining_amount:,.2f}"),
                ("Payment", sale.payment_status.value),
                ("Status", sale.status.value),
                ("Address", sale.delivery_address),
                ("Notes", sale.notes),
            ),
        )

    def _preview_sale(self, row: int) -> None:
        if row >= len(self._history_sales):
            return
        self._last_sale_id = self._history_sales[row].id
        self._preview_last()

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
