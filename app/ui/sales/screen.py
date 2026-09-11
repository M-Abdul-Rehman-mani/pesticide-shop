"""Pesticide sales by batch with customer/dealer invoices and receipt actions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import cast

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
from app.models.enums import PaymentMethod, SaleStatus, SettingCategory
from app.models.inventory import StockBatch
from app.models.product import Product
from app.models.sale import Sale, SaleItem
from app.models.sale_return import SaleReturn, SaleReturnItem
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, has_permission
from app.services.catalog_service import CustomerService, DealerService
from app.services.dto import CreatePesticideSaleCommand, PesticideSaleLineInput
from app.services.pesticide_sale_service import PesticideSaleService
from app.services.sale_return_service import (
    ReturnableLine,
    ReturnLineInput,
    SaleReturnService,
)
from app.services.settings_service import SettingsService
from app.ui.documents import InvoiceDocumentActions
from app.ui.forms import MoneyEdit, PaymentEditor
from app.ui.widgets import (
    PageHeader,
    RowsTableModel,
    configure_searchable_combo,
    configure_table,
    populate_enum_combo,
    populate_row_actions,
    record_count_text,
    selected_enum,
    show_error,
    show_record_details,
    wrap_scroll,
)
from app.ui.workers import FunctionWorker, start_worker
from app.utils.exceptions import ConflictError, NotFoundError
from app.utils.formatting import format_date

#: Tab positions on this screen, in the order they are built.
NEW_SALE_TAB = 0
HISTORY_TAB = 1
RETURNS_TAB = 2


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


class SaleReturnDialog(QDialog):
    """Choose how much of each invoice line is coming back."""

    def __init__(
        self,
        invoice_number: str,
        lines: list[ReturnableLine],
        currency: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._lines = lines
        self.setWindowTitle(f"Record a return against {invoice_number}")
        self.resize(760, 420)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Enter the quantity coming back on each line. Restocked goods go back to the "
            "batch they came from; clear the tick for damaged or expired stock. The credit "
            "settles what is still owed, and anything beyond that is refunded."
        )
        note.setWordWrap(True)
        self.table = QTableWidget(len(lines), 6)
        self.table.setHorizontalHeaderLabels(
            ("Product", "Batch", "Sold", "Returnable", "Return", "Restock")
        )
        configure_table(
            self.table,
            stretch_column=0,
            minimum_section_size=72,
            editable=True,
            widget_columns={4: 96, 5: 80},
        )
        self._quantities: list[QSpinBox] = []
        self._restock: list[QCheckBox] = []
        for row, line in enumerate(lines):
            self.table.setItem(row, 0, QTableWidgetItem(line.product))
            self.table.setItem(row, 1, QTableWidgetItem(line.batch_number))
            self.table.setItem(row, 2, QTableWidgetItem(f"{line.sold:,}"))
            self.table.setItem(row, 3, QTableWidgetItem(f"{line.returnable:,}"))
            quantity = QSpinBox()
            quantity.setRange(0, line.returnable)
            quantity.valueChanged.connect(self._update_credit)
            self.table.setCellWidget(row, 4, quantity)
            self._quantities.append(quantity)
            restock = QCheckBox()
            restock.setChecked(True)
            restock.setToolTip("Clear this for damaged or expired goods that cannot be resold")
            self.table.setCellWidget(row, 5, restock)
            self._restock.append(restock)
        self._currency = currency
        self.credit_label = QLabel()
        self.credit_label.setObjectName("SectionTitle")
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("Damaged, wrong product, customer changed their mind…")
        self.method = QComboBox()
        populate_enum_combo(self.method, PaymentMethod)
        form = QFormLayout()
        form.addRow("Reason *", self.reason)
        form.addRow("Refund method", self.method)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Record return")
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(note)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.credit_label)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self._update_credit()

    def _update_credit(self) -> None:
        credit = sum(
            (
                line.unit_price * box.value()
                for line, box in zip(self._lines, self._quantities, strict=True)
            ),
            Decimal("0.00"),
        )
        self.credit_label.setText(f"Credit: {self._currency} {credit:,.0f}")

    def _accept_if_valid(self) -> None:
        if not self.reason.text().strip():
            QMessageBox.information(self, "Reason required", "Say why the goods came back.")
            self.reason.setFocus()
            return
        if not self.values():
            QMessageBox.information(
                self, "Nothing selected", "Enter a quantity against at least one line."
            )
            return
        self.accept()

    def payment_method(self) -> PaymentMethod:
        return selected_enum(self.method, PaymentMethod)

    def values(self) -> tuple[ReturnLineInput, ...]:
        return tuple(
            ReturnLineInput(line.sale_item_id, box.value(), restock.isChecked())
            for line, box, restock in zip(self._lines, self._quantities, self._restock, strict=True)
            if box.value() > 0
        )


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
        self._barcodes: dict[str, uuid.UUID] = {}
        #: Set while an existing invoice is being amended rather than created.
        self._editing: uuid.UUID | None = None
        self._editing_number = ""
        self._last_invoice_number = "invoice"
        self._documents = InvoiceDocumentActions(self, session_factory, settings)
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
        self.add_recipient = QPushButton()
        self.add_recipient.setProperty("secondary", True)
        recipient_form.addRow("Type", self.recipient_type)
        recipient_form.addRow("Name", self.recipient)
        recipient_form.addRow("", self.add_recipient)
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
        # Recipient names are long; give the group a fair share of the row so the
        # selected customer or dealer stays readable.
        top.addWidget(recipient_group, 2)
        top.addWidget(details_group, 3)
        self._top_layout = top
        layout.addLayout(top)

        stock_group, stock_layout = QGroupBox("Add Product Batch"), QHBoxLayout()
        stock_group.setLayout(stock_layout)
        self.scan = QLineEdit()
        self.scan.setPlaceholderText("Scan barcode")
        self.scan.setClearButtonEnabled(True)
        self.scan.setMaximumWidth(190)
        self.scan.setToolTip(
            "Scan a product barcode to select its batch. Most scanners send Enter, "
            "which adds the item straight to the invoice."
        )
        self.batch, self.quantity, self.unit_price = QComboBox(), QSpinBox(), MoneyEdit()
        configure_searchable_combo(self.batch, "Type a product or batch number")
        self.quantity.setRange(1, 1_000_000)
        add = QPushButton("+  Add batch")
        add.setToolTip("Add this batch and quantity to the invoice")
        for stock_widget in (
            QLabel("Barcode"),
            self.scan,
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
        configure_table(
            self.cart_table,
            stretch_column=0,
            minimum_section_size=80,
            widget_columns={2: 92, 3: 104, 4: 118, 6: 96},
        )
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
        self.print_invoice = QPushButton("Print Invoice")
        self.complete = QPushButton("Complete Sale && Queue Emails")
        self.cancel_edit = QPushButton("Cancel edit")
        self.cancel_edit.setProperty("secondary", True)
        self.cancel_edit.setVisible(False)
        for secondary in (self.save_pdf, self.preview, self.print_invoice):
            secondary.setProperty("secondary", True)
            secondary.setEnabled(False)
        self.print_invoice.setToolTip(
            "Print the receipt straight to the thermal printer chosen in Settings > Printer"
        )
        self.complete.setEnabled(False)
        self.complete.setToolTip("Save the sale, reduce stock, and queue invoice emails")
        actions.addWidget(self.save_pdf)
        actions.addWidget(self.preview)
        actions.addWidget(self.print_invoice)
        actions.addStretch()
        actions.addWidget(self.cancel_edit)
        actions.addWidget(self.complete)
        layout.addLayout(actions)

        self.recipient_type.currentIndexChanged.connect(self._set_recipients)
        self.recipient_type.currentIndexChanged.connect(self._update_add_recipient_button)
        self.recipient.currentIndexChanged.connect(self._recipient_changed)
        self.batch.currentIndexChanged.connect(self._batch_changed)
        add.clicked.connect(self._add_batch)
        self.scan.returnPressed.connect(self._scan_barcode)
        self.discount.textChanged.connect(self._calculate)
        self.tax.textChanged.connect(self._calculate)
        self.complete.clicked.connect(self.save)
        self.cancel_edit.clicked.connect(self._cancel_edit)
        self.save_pdf.clicked.connect(self._save_last_pdf)
        self.preview.clicked.connect(self._preview_last)
        self.print_invoice.clicked.connect(self._print_last_receipt)
        self.add_recipient.clicked.connect(self._add_recipient)
        self._update_add_recipient_button()
        self._build_sales_history_tab()
        self._build_returns_tab()
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
            list[tuple[uuid.UUID, str, str, int, Decimal, str]],
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
                            b.product.barcode or "",
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

    def _is_dealer_sale(self) -> bool:
        return self.recipient_type.currentText() == "Dealer"

    def _update_add_recipient_button(self) -> None:
        """Offer the shortcut that matches the selected recipient type."""

        dealer = self._is_dealer_sale()
        self.add_recipient.setText("+ Add dealer" if dealer else "+ Add walk-in customer")
        self.add_recipient.setToolTip(
            "Create a trade dealer account without leaving this sale"
            if dealer
            else "Create a counter customer without leaving this sale"
        )

    def _add_recipient(self) -> None:
        if self._is_dealer_sale():
            self._add_dealer()
        else:
            self._add_walk_in_customer()

    def _add_dealer(self) -> None:
        """Create a dealer from the sales screen and select it for this invoice."""

        from app.ui.dealers.screen import DealerDialog

        dialog = DealerDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        data = dialog.values()

        def operation() -> tuple[uuid.UUID, str, str, str | None, str | None]:
            with self._session_factory.begin() as session:
                dealer = DealerService(session).create(
                    actor=self._actor,
                    name=data.name,
                    phone=data.phone,
                    business_name=data.business_name,
                    email=data.email,
                    address=data.address,
                    cnic=data.cnic,
                    tax_number=data.tax_number,
                    territory=data.territory,
                    credit_limit=data.credit_limit,
                    notes=data.notes,
                )
                return (
                    dealer.id,
                    dealer.display_name,
                    dealer.phone,
                    dealer.address,
                    dealer.territory,
                )

        def added(result: object) -> None:
            dealer_id, display_name, phone, address, territory = cast(
                tuple[uuid.UUID, str, str, str | None, str | None], result
            )
            self._dealers.append((dealer_id, f"{display_name} — {phone}", address, territory))
            self._dealers.sort(key=lambda dealer: dealer[1].casefold())
            self._set_recipients()
            self._select_recipient(dealer_id)

        self._worker = start_worker(
            operation, succeeded=added, failed=lambda error: show_error(self, error)
        )

    def _select_recipient(self, recipient_id: uuid.UUID) -> None:
        for index in range(self.recipient.count()):
            data = self.recipient.itemData(index)
            if data and data[0] == recipient_id:
                self.recipient.setCurrentIndex(index)
                return

    def _add_walk_in_customer(self) -> None:
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
            self._select_recipient(customer_id)

        self._worker = start_worker(
            operation, succeeded=added, failed=lambda error: show_error(self, error)
        )

    def _choices_loaded(self, result: object) -> None:
        self._customers, self._dealers, batches = cast(
            tuple[
                list[tuple[uuid.UUID, str, str | None, str | None]],
                list[tuple[uuid.UUID, str, str | None, str | None]],
                list[tuple[uuid.UUID, str, str, int, Decimal, str]],
            ],
            result,
        )
        self.batch.clear()
        self._barcodes = {}
        for batch_id, product, number, available, price, barcode in batches:
            self.batch.addItem(
                f"{product} | Batch {number} | Available {available}",
                (batch_id, product, number, available, price),
            )
            # First batch wins: stock is listed soonest-to-expire, which is the one
            # that should leave the shelf first.
            if barcode:
                self._barcodes.setdefault(barcode, batch_id)
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
            self.unit_price.setText(f"{price:.0f}")

    def _scan_barcode(self) -> None:
        """Select the scanned product's batch and add it to the invoice.

        Scanners type the code and press Enter, so one scan should be one line.
        """

        code = "".join(self.scan.text().split())
        self.scan.clear()
        if not code:
            return
        batch_id = self._barcodes.get(code)
        if batch_id is None:
            show_error(
                self,
                NotFoundError(
                    f"No sellable stock matches barcode {code}. Check the product's "
                    "barcode, or that a batch is still in stock."
                ),
            )
            return
        for index in range(self.batch.count()):
            data = self.batch.itemData(index)
            if data and data[0] == batch_id:
                self.batch.setCurrentIndex(index)
                break
        self.quantity.setValue(1)
        self._add_batch()
        self.scan.setFocus()

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
        unit_price = MoneyEdit(f"{entry.unit_price:.0f}")
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
                    item.setText(f"{gross - line.discount:,.0f}")
            total = (
                subtotal - line_discounts - self.discount.decimal_value() + self.tax.decimal_value()
            )
        except Exception:
            return
        self.subtotal_label.setText(f"{self._settings.app_currency} {subtotal:,.0f}")
        self.total_label.setText(f"{self._settings.app_currency} {total:,.0f}")

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
        editing = self._editing

        def operation() -> tuple[uuid.UUID, str]:
            with self._session_factory.begin() as session:
                if editing is not None:
                    amended = PesticideSaleService(session).amend(editing, command, self._actor)
                    return amended.id, amended.invoice_number
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
        was_editing = self._editing is not None
        self._leave_edit_mode()
        self._select_invoice(sale_id, invoice)
        QMessageBox.information(
            self,
            "Invoice updated" if was_editing else "Sale completed",
            f"Invoice {invoice} was updated; its stock and balance were adjusted."
            if was_editing
            else f"Invoice {invoice} was saved and recipient/owner emails were queued.",
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
        self.tabs.currentChanged.connect(self._tab_changed)

    def _tab_changed(self, index: int) -> None:
        """Reload whichever list has just been opened."""

        if index == HISTORY_TAB:
            self._refresh_sales_history()
        elif index == RETURNS_TAB:
            self._refresh_returns()

    def _build_returns_tab(self) -> None:
        """List the credit notes, so a return is visible after it is recorded."""

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        search_row = QHBoxLayout()
        self.returns_search = QLineEdit()
        self.returns_search.setPlaceholderText(
            "Search return number, invoice, customer, dealer, or product"
        )
        self.returns_search.setClearButtonEnabled(True)
        refresh = QPushButton("Refresh")
        refresh.setProperty("secondary", True)
        search_row.addWidget(QLabel("Search"))
        search_row.addWidget(self.returns_search, 1)
        search_row.addWidget(refresh)
        self.returns_model = RowsTableModel(
            (
                "Return",
                "Date",
                "Invoice",
                "Recipient",
                "Products",
                "Credit",
                "Settled",
                "Recorded by",
                "Actions",
            ),
            page,
        )
        self.returns_table = QTableView()
        self.returns_table.setModel(self.returns_model)
        configure_table(self.returns_table, stretch_column=4, minimum_section_size=76)
        self.returns_count = QLabel("Loading returns…")
        self.returns_count.setObjectName("RecordCount")
        hint = QLabel(
            "Record a return from All Sales > Record a return. A credit note is never edited; "
            "correct one by recording the opposite movement."
        )
        hint.setObjectName("FieldHint")
        hint.setWordWrap(True)
        layout.addLayout(search_row)
        layout.addWidget(self.returns_table, 1)
        layout.addWidget(self.returns_count)
        layout.addWidget(hint)
        self.tabs.addTab(page, "Sale Returns")
        self._returns: list[SaleReturn] = []
        self._returns_search_timer = QTimer(self)
        self._returns_search_timer.setSingleShot(True)
        self._returns_search_timer.setInterval(300)
        self._returns_search_timer.timeout.connect(self._refresh_returns)
        self.returns_search.textChanged.connect(lambda _text: self._returns_search_timer.start())
        self.returns_search.returnPressed.connect(self._refresh_returns)
        refresh.clicked.connect(self._refresh_returns)

    def _refresh_returns(self) -> None:
        query = self.returns_search.text().strip()

        def operation() -> list[SaleReturn]:
            with self._session_factory() as session:
                statement = select(SaleReturn).options(
                    selectinload(SaleReturn.items), selectinload(SaleReturn.sale)
                )
                if query:
                    pattern = f"%{query}%"
                    statement = statement.where(
                        or_(
                            SaleReturn.return_number.ilike(pattern),
                            SaleReturn.reason.ilike(pattern),
                            SaleReturn.sale.has(
                                or_(
                                    Sale.invoice_number.ilike(pattern),
                                    Sale.customer.has(Customer.name.ilike(pattern)),
                                    Sale.dealer.has(
                                        or_(
                                            Dealer.name.ilike(pattern),
                                            Dealer.business_name.ilike(pattern),
                                        )
                                    ),
                                )
                            ),
                            SaleReturn.items.any(
                                SaleReturnItem.product.has(Product.name.ilike(pattern))
                            ),
                        )
                    )
                documents = list(
                    session.scalars(statement.order_by(SaleReturn.returned_at.desc()).limit(500))
                )
                for document in documents:
                    # Touch the joined rows while the session is open; the list is
                    # rendered after it closes.
                    _ = document.creator.full_name
                    _ = self._recipient_name(document.sale)
                    _ = [(item.product.display_name, item.quantity) for item in document.items]
                session.expunge_all()
                return documents

        self._worker = start_worker(
            operation,
            succeeded=self._display_returns,
            failed=lambda error: show_error(self, error),
        )

    @staticmethod
    def _recipient_name(sale: Sale) -> str:
        if sale.dealer:
            return sale.dealer.display_name
        return sale.customer.name if sale.customer else "Walk-in"

    def _display_returns(self, result: object) -> None:
        self._returns = cast(list[SaleReturn], result)
        currency = self._settings.app_currency
        rows: list[tuple[object, ...]] = []
        for document in self._returns:
            products = ", ".join(
                f"{item.product.display_name} x {item.quantity}"
                + ("" if item.restocked else " (not restocked)")
                for item in document.items
            )
            rows.append(
                (
                    document.return_number,
                    format_date(document.returned_at),
                    document.sale.invoice_number,
                    self._recipient_name(document.sale),
                    products,
                    f"{currency} {document.total:,.0f}",
                    "Refunded" if document.refunded else "Against balance",
                    document.creator.full_name,
                    "",
                )
            )
        self.returns_model.set_rows(rows)
        populate_row_actions(
            self.returns_table,
            8,
            len(rows),
            (
                ("View details", self._view_return),
                ("Open the invoice", self._open_returned_invoice),
            ),
        )
        self.returns_count.setText(record_count_text(len(rows), "return"))

    def _view_return(self, row: int) -> None:
        if row >= len(self._returns):
            return
        document = self._returns[row]
        currency = self._settings.app_currency
        products = "\n".join(
            f"{item.product.display_name} — {item.quantity} x {item.unit_price:,.0f}"
            f" = {item.total:,.0f}" + ("" if item.restocked else "  (written off, not restocked)")
            for item in document.items
        )
        show_record_details(
            self,
            f"Return {document.return_number}",
            (
                ("Return", document.return_number),
                ("Date", format_date(document.returned_at)),
                ("Invoice", document.sale.invoice_number),
                ("Recipient", self._recipient_name(document.sale)),
                ("Returned", products),
                ("Credit", f"{currency} {document.total:,.0f}"),
                (
                    "Settlement",
                    "Refunded to the buyer"
                    if document.refunded
                    else "Credited against the invoice balance",
                ),
                ("Reason", document.reason),
                ("Recorded by", document.creator.full_name),
            ),
        )

    def _open_returned_invoice(self, row: int) -> None:
        """Show the invoice a credit note was raised against."""

        if row >= len(self._returns):
            return
        document = self._returns[row]
        self.sales_search.setText(document.sale.invoice_number)
        self.tabs.setCurrentIndex(HISTORY_TAB)
        self._refresh_sales_history()

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
                    f"{self._settings.app_currency} {sale.total:,.0f}",
                    f"{self._settings.app_currency} {sale.paid_amount:,.0f}",
                    f"{self._settings.app_currency} {sale.remaining_amount:,.0f}",
                    sale.status.value,
                    "",
                )
            )
        self.sales_model.set_rows(rows)
        populate_row_actions(
            self.sales_table,
            8,
            len(rows),
            (
                ("View details", self._view_sale),
                ("Print preview", self._preview_sale),
                ("Print invoice", self._print_sale),
                ("Edit sale", self._edit_sale),
                ("Record a return", self._return_sale),
                ("Void sale", self._void_sale),
            ),
        )
        self.sales_count.setText(record_count_text(len(rows), "sale"))

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
            f"{item.product.display_name} — {item.quantity} x {item.unit_price:,.0f}"
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
                ("Total", f"{self._settings.app_currency} {sale.total:,.0f}"),
                ("Paid", f"{self._settings.app_currency} {sale.paid_amount:,.0f}"),
                ("Balance", f"{self._settings.app_currency} {sale.remaining_amount:,.0f}"),
                ("Payment", sale.payment_status.value),
                ("Status", sale.status.value),
                ("Address", sale.delivery_address),
                ("Notes", sale.notes),
            ),
        )

    def _preview_sale(self, row: int) -> None:
        if self._select_history_row(row):
            self._preview_last()

    def _print_sale(self, row: int) -> None:
        if self._select_history_row(row):
            self._print_last_receipt()

    def _enter_edit_mode(self, sale_id: uuid.UUID, invoice_number: str) -> None:
        """Switch the New Sale tab into amending an existing invoice."""

        self._editing = sale_id
        self._editing_number = invoice_number
        self.complete.setText(f"Save changes to {invoice_number}")
        self.complete.setToolTip("Update this invoice, adjusting stock and the balance")
        self.cancel_edit.setVisible(True)
        self.payments.setEnabled(False)
        self.payments.setToolTip(
            "Payments are not edited here. Reverse a payment first if one was taken."
        )
        self.tabs.setCurrentIndex(NEW_SALE_TAB)

    def _leave_edit_mode(self) -> None:
        self._editing = None
        self._editing_number = ""
        self.complete.setText("Complete Sale && Queue Emails")
        self.complete.setToolTip("Save the sale, reduce stock, and queue invoice emails")
        self.cancel_edit.setVisible(False)
        self.payments.setEnabled(True)
        self.payments.setToolTip("")

    def _cancel_edit(self) -> None:
        self._leave_edit_mode()
        self._clear_cart()
        self.tabs.setCurrentIndex(HISTORY_TAB)

    def _clear_cart(self) -> None:
        self._cart.clear()
        self.cart_table.setRowCount(0)
        self.discount.setText("0.00")
        self.tax.setText("0.00")
        self.payments.clear()
        self.notes.clear()
        self._calculate()
        self._update_cart_state()

    def _edit_sale(self, row: int) -> None:
        """Load a completed invoice back into the sale form for correction."""

        if row >= len(self._history_sales):
            return
        sale = self._history_sales[row]
        if sale.status is not SaleStatus.COMPLETED:
            QMessageBox.information(self, "Not editable", "A voided sale cannot be edited.")
            return
        sale_id = sale.id

        def operation() -> tuple[
            str, dict[str, object], list[tuple[uuid.UUID, int, Decimal, Decimal]]
        ]:
            with self._session_factory() as session:
                loaded = session.execute(
                    select(Sale).options(selectinload(Sale.items)).where(Sale.id == sale_id)
                ).scalar_one()
                header: dict[str, object] = {
                    "dealer": loaded.dealer_id is not None,
                    "recipient_id": loaded.dealer_id or loaded.customer_id,
                    "order_number": loaded.order_number or "",
                    "territory": loaded.territory or "",
                    "policy": loaded.policy or "",
                    "store": loaded.store or "",
                    "delivery_address": loaded.delivery_address or "",
                    "notes": loaded.notes or "",
                    "discount": loaded.discount,
                    "tax": loaded.tax,
                }
                lines = [
                    (item.stock_batch_id, item.quantity, item.unit_price, item.discount)
                    for item in loaded.items
                ]
                return loaded.invoice_number, header, lines

        def loaded(result: object) -> None:
            invoice_number, header, lines = cast(
                tuple[str, dict[str, object], list[tuple[uuid.UUID, int, Decimal, Decimal]]],
                result,
            )
            self._clear_cart()
            self.recipient_type.setCurrentText("Dealer" if header["dealer"] else "Customer")
            recipient_id = header["recipient_id"]
            if isinstance(recipient_id, uuid.UUID):
                self._select_recipient(recipient_id)
            self.order_number.setText(str(header["order_number"]))
            self.territory.setText(str(header["territory"]))
            self.policy.setText(str(header["policy"]))
            self.store.setText(str(header["store"]))
            self.delivery_address.setText(str(header["delivery_address"]))
            self.notes.setText(str(header["notes"]))
            self.discount.setText(f"{header['discount']:.0f}")
            self.tax.setText(f"{header['tax']:.0f}")
            missing = [
                batch_id
                for batch_id, _quantity, _price, _line_discount in lines
                if not self._add_existing_line(batch_id, _quantity, _price, _line_discount)
            ]
            if missing:
                show_error(
                    self,
                    ConflictError(
                        "Some batches on this invoice are no longer sellable, so they were "
                        "left out. Check the lines before saving."
                    ),
                )
            self._enter_edit_mode(sale_id, invoice_number)

        self._worker = start_worker(
            operation, succeeded=loaded, failed=lambda error: show_error(self, error)
        )

    def _add_existing_line(
        self, batch_id: uuid.UUID, quantity: int, unit_price: Decimal, line_discount: Decimal
    ) -> bool:
        """Put one saved invoice line back in the cart. False when its batch is gone."""

        for index in range(self.batch.count()):
            data = self.batch.itemData(index)
            if data and data[0] == batch_id:
                self.batch.setCurrentIndex(index)
                # The batch still holds what this invoice took, so allow the
                # original quantity even when the free stock is now lower.
                self.quantity.setMaximum(max(self.quantity.maximum(), quantity))
                self.quantity.setValue(quantity)
                self.unit_price.setText(f"{unit_price:.0f}")
                self._add_batch()
                row = len(self._cart) - 1
                discount_widget = self.cart_table.cellWidget(row, 4)
                if isinstance(discount_widget, MoneyEdit):
                    discount_widget.setText(f"{line_discount:.0f}")
                self._calculate()
                return True
        return False

    def _return_sale(self, row: int) -> None:
        """Record goods coming back against a completed invoice."""

        if row >= len(self._history_sales):
            return
        sale = self._history_sales[row]
        if sale.status is not SaleStatus.COMPLETED:
            QMessageBox.information(self, "Not available", "A voided sale cannot accept a return.")
            return
        sale_id, invoice_number = sale.id, sale.invoice_number

        def operation() -> list[ReturnableLine]:
            with self._session_factory() as session:
                return SaleReturnService(session).returnable_lines(sale_id)

        def choose(result: object) -> None:
            lines = [line for line in cast(list[ReturnableLine], result) if line.returnable > 0]
            if not lines:
                QMessageBox.information(
                    self,
                    "Nothing to return",
                    f"Every line on {invoice_number} has already been returned.",
                )
                return
            dialog = SaleReturnDialog(invoice_number, lines, self._settings.app_currency, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            selected = dialog.values()
            if not selected:
                QMessageBox.information(
                    self, "Nothing selected", "Enter a quantity against at least one line."
                )
                return
            reason, method = dialog.reason.text(), dialog.payment_method()

            def record() -> str:
                with self._session_factory.begin() as session:
                    stored = SettingsService(
                        session, self._settings.app_secret_key.get_secret_value()
                    )
                    document = SaleReturnService(session).record(
                        sale_id,
                        selected,
                        self._actor,
                        reason=reason,
                        refund_method=method,
                        return_prefix=stored.get(SettingCategory.GENERAL, "return_prefix", "RET")
                        or "RET",
                    )
                    return (
                        f"{document.return_number} credited "
                        f"{self._settings.app_currency} {document.total:,.0f}"
                        + (" and the balance was refunded." if document.refunded else ".")
                    )

            self._worker = start_worker(
                record,
                succeeded=lambda message: self._return_recorded(str(message), invoice_number),
                failed=lambda error: show_error(self, error),
            )

        self._worker = start_worker(
            operation, succeeded=choose, failed=lambda error: show_error(self, error)
        )

    def _return_recorded(self, message: str, invoice_number: str) -> None:
        QMessageBox.information(self, "Return recorded", message)
        self._load_choices()
        self._refresh_sales_history()
        self._refresh_returns()
        self.sale_completed.emit(invoice_number)

    def _void_sale(self, row: int) -> None:
        """Cancel a sale, returning its stock and clearing the dealer's debt."""

        if row >= len(self._history_sales):
            return
        sale = self._history_sales[row]
        if not has_permission(self._actor.role, Permission.VOID_SALE):
            QMessageBox.information(self, "Not permitted", "Your role cannot void a sale.")
            return
        if sale.status is not SaleStatus.COMPLETED:
            QMessageBox.information(self, "Already voided", "This sale is already voided.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Void {sale.invoice_number}")
        form = QFormLayout(dialog)
        explanation = QLabel(
            f"Voiding {sale.invoice_number} returns its stock to the batches it came "
            "from and removes the charge from the recipient's account. The invoice is "
            "kept and marked voided; it stops counting in reports and statements.\n\n"
            "Payments must be reversed before an invoice can be voided."
        )
        explanation.setWordWrap(True)
        reason = QLineEdit()
        reason.setPlaceholderText("Wrong product, wrong quantity, customer cancelled…")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Void sale")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(explanation)
        form.addRow("Reason *", reason)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        sale_id, void_reason = sale.id, reason.text()

        def operation() -> None:
            with self._session_factory.begin() as session:
                PesticideSaleService(session).void(sale_id, self._actor, reason=void_reason)

        def voided(_result: object) -> None:
            QMessageBox.information(
                self, "Sale voided", f"{sale.invoice_number} was voided and its stock returned."
            )
            self._last_sale_id = None
            for button in (self.save_pdf, self.preview, self.print_invoice):
                button.setEnabled(False)
            self._load_choices()
            self._refresh_sales_history()
            self.sale_completed.emit(sale.invoice_number)

        self._worker = start_worker(
            operation, succeeded=voided, failed=lambda error: show_error(self, error)
        )

    def _select_history_row(self, row: int) -> bool:
        if row >= len(self._history_sales):
            return False
        sale = self._history_sales[row]
        self._select_invoice(sale.id, sale.invoice_number)
        return True

    def _select_invoice(self, sale_id: uuid.UUID, invoice_number: str) -> None:
        """Point the document actions at one saved invoice and enable them."""

        self._last_sale_id = sale_id
        self._last_invoice_number = invoice_number
        for button in (self.save_pdf, self.preview, self.print_invoice):
            button.setEnabled(True)

    def _require_sale(self) -> uuid.UUID | None:
        """Return the invoice the document buttons act on, or warn and return None."""

        if self._last_sale_id is None:
            show_error(self, ConflictError("Complete a sale before generating an invoice."))
            return None
        return self._last_sale_id

    def _save_last_pdf(self) -> None:
        if (sale_id := self._require_sale()) is not None:
            self._documents.save_pdf(sale_id, suggested_name=f"{self._last_invoice_number}.pdf")

    def _preview_last(self) -> None:
        if (sale_id := self._require_sale()) is not None:
            self._documents.preview(sale_id)

    def _print_last_receipt(self) -> None:
        if (sale_id := self._require_sale()) is not None:
            self._documents.print_receipt(sale_id)
