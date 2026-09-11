"""Authorized email, receipt, printer, backup, and security settings."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableView,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import Settings
from app.email.configuration import OWNER_EMAIL_SEPARATOR, parse_owner_emails
from app.email.email_service import OutgoingEmail
from app.email.smtp_client import SMTPConfig, SMTPEmailService
from app.models.email_history import EmailHistory
from app.models.enums import EmailStatus, SettingCategory
from app.printing.alignment_test import alignment_test_page
from app.printing.print_preview import open_print_preview
from app.printing.printer_service import (
    DEFAULT_RECEIPT_FORMAT,
    RECEIPT_FORMATS,
    SYSTEM_DEFAULT_PRINTER,
    PrinterService,
    ReceiptFormat,
    receipt_format,
    write_temporary_pdf,
)
from app.printing.receipt_generator import ReceiptGenerator
from app.printing.sample_receipt import sample_receipt
from app.printing.shop_profile import load_shop_profile
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, has_permission
from app.services.backup_service import BackupService
from app.services.data_export_service import DataExportResult, DataExportService
from app.services.settings_service import SettingsService
from app.tasks.email_tasks import send_email
from app.ui.widgets import RowsTableModel, show_error
from app.ui.workers import FunctionWorker, start_worker
from app.utils.exceptions import ValidationError
from app.utils.formatting import format_date


class SettingsScreen(QWidget):
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
        self._email_ids: list[uuid.UUID] = []
        root = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("Application Settings")
        title.setObjectName("PageTitle")
        self.save_button = QPushButton("Save Settings")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.save_button)
        root.addLayout(header)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)
        self._build_general()
        self._build_email()
        self._build_printer()
        self._build_receipt()
        self._build_backup()
        self._build_database()
        self._build_security()
        self._build_email_history()
        self.save_button.clicked.connect(self.save)
        self._load()

    @staticmethod
    def _tab_form() -> tuple[QWidget, QFormLayout]:
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 0, 0, 0)
        inner = QWidget()
        form = QFormLayout(inner)
        form.setContentsMargins(16, 16, 16, 16)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        return tab, form

    def _build_general(self) -> None:
        tab, form = self._tab_form()
        self.currency = QLineEdit(self._settings.app_currency)
        self.timezone = QLineEdit(self._settings.app_timezone)
        self.invoice_prefix = QLineEdit("INV")
        self.return_prefix = QLineEdit("RET")
        self.return_prefix.setToolTip("Numbers the credit note raised when goods come back")
        form.addRow("Currency", self.currency)
        form.addRow("Timezone", self.timezone)
        form.addRow("Invoice prefix", self.invoice_prefix)
        form.addRow("Return prefix", self.return_prefix)
        prefix_note = QLabel(
            "Prefixes may be 1-12 letters, numbers, or hyphens. Changing one affects documents "
            "issued from then on; those already issued keep their numbers."
        )
        prefix_note.setWordWrap(True)
        form.addRow(prefix_note)
        self.tabs.addTab(tab, "General")

    def _build_email(self) -> None:
        tab, form = self._tab_form()
        self.smtp_host = QLineEdit(self._settings.smtp_host or "")
        self.smtp_port = QSpinBox()
        self.smtp_port.setRange(1, 65535)
        self.smtp_port.setValue(self._settings.smtp_port)
        self.smtp_username = QLineEdit(self._settings.smtp_username or "")
        self.smtp_username.setPlaceholderText("The mailbox you sign in with, e.g. shop@gmail.com")
        self.smtp_password = QLineEdit()
        self.smtp_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.smtp_password.setPlaceholderText("Leave blank to keep the saved password")
        self.smtp_tls = QCheckBox("Use STARTTLS")
        self.smtp_tls.setChecked(self._settings.smtp_use_tls)
        self.smtp_from = QLineEdit(self._settings.smtp_from_email or "")
        test = QPushButton("Send Test Email")
        test.setProperty("secondary", True)
        test.clicked.connect(self._send_test_email)
        form.addRow("SMTP host", self.smtp_host)
        form.addRow("SMTP port", self.smtp_port)
        form.addRow("Username", self.smtp_username)
        form.addRow("Password", self.smtp_password)
        form.addRow("Security", self.smtp_tls)
        form.addRow("Sender email", self.smtp_from)
        form.addRow("Owner emails", self._build_owner_emails())
        gmail_note = QLabel(
            "Gmail: the username is the full address, and the password must be a 16-character "
            "App Password generated with 2-Step Verification on — Google refuses account "
            "passwords over SMTP. Host smtp.gmail.com, port 587, STARTTLS on."
        )
        gmail_note.setWordWrap(True)
        form.addRow(gmail_note)
        form.addRow("", test)
        self.tabs.addTab(tab, "Email")

    def _build_owner_emails(self) -> QWidget:
        """A list of the people copied on every invoice, with add and remove."""

        panel = QWidget()
        # Without this the form stretches the row and strands the hint far below it.
        panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        entry_row = QHBoxLayout()
        self.owner_email_entry = QLineEdit()
        self.owner_email_entry.setPlaceholderText("owner@example.com")
        self.owner_email_entry.returnPressed.connect(self._add_owner_email)
        add = QPushButton("Add")
        add.setProperty("secondary", True)
        add.clicked.connect(self._add_owner_email)
        self.remove_owner_email = QPushButton("Remove")
        self.remove_owner_email.setProperty("danger", True)
        self.remove_owner_email.clicked.connect(self._remove_owner_email)
        entry_row.addWidget(self.owner_email_entry, 1)
        entry_row.addWidget(add)
        entry_row.addWidget(self.remove_owner_email)
        self.owner_emails = QListWidget()
        self.owner_emails.setFixedHeight(96)
        self.owner_emails.setAlternatingRowColors(True)
        self.owner_emails.itemSelectionChanged.connect(self._owner_email_selection_changed)
        hint = QLabel("Everyone listed gets a copy of every invoice and the daily report.")
        hint.setObjectName("FieldHint")
        hint.setWordWrap(True)
        layout.addLayout(entry_row)
        layout.addWidget(self.owner_emails)
        layout.addWidget(hint)
        self._set_owner_emails(parse_owner_emails(self._settings.owner_email))
        return panel

    def _set_owner_emails(self, addresses: Sequence[str]) -> None:
        self.owner_emails.clear()
        self.owner_emails.addItems(list(addresses))
        self._owner_email_selection_changed()

    def _owner_email_addresses(self) -> tuple[str, ...]:
        return tuple(
            item.text()
            for index in range(self.owner_emails.count())
            if (item := self.owner_emails.item(index)) is not None
        )

    def _owner_email_selection_changed(self) -> None:
        self.remove_owner_email.setEnabled(bool(self.owner_emails.selectedItems()))

    def _add_owner_email(self) -> None:
        typed = self.owner_email_entry.text().strip()
        if not typed:
            return
        addresses = parse_owner_emails(typed)
        if not addresses:
            show_error(self, ValidationError(f"{typed} is not a valid email address."))
            return
        existing = {address.lower() for address in self._owner_email_addresses()}
        added = [address for address in addresses if address.lower() not in existing]
        if not added:
            show_error(self, ValidationError("That address is already on the list."))
            return
        self.owner_emails.addItems(added)
        self.owner_email_entry.clear()

    def _remove_owner_email(self) -> None:
        for item in self.owner_emails.selectedItems():
            self.owner_emails.takeItem(self.owner_emails.row(item))
        self._owner_email_selection_changed()

    def _build_printer(self) -> None:
        tab, form = self._tab_form()
        service = PrinterService()
        system_default = service.system_default_printer()
        self.printer = QComboBox()
        self.printer.addItem(
            f"System default ({system_default})" if system_default else "System default",
            SYSTEM_DEFAULT_PRINTER,
        )
        for printer in service.available_printers():
            self.printer.addItem(printer, printer)
        self.receipt_width = QComboBox()
        for choice in RECEIPT_FORMATS:
            self.receipt_width.addItem(choice.label, choice.key)
        self.receipt_width.setCurrentIndex(
            max(0, self.receipt_width.findData(DEFAULT_RECEIPT_FORMAT.key))
        )
        self.print_width = QSpinBox()
        self.print_width.setRange(30, 80)
        self.print_width.setSuffix(" mm")
        self.print_width.setToolTip(
            "How wide a strip the print head can mark. A thermal roll always has a "
            "blank margin the printer cannot reach."
        )
        self.printer_summary = QLabel()
        self.printer_summary.setWordWrap(True)
        preview = QPushButton("Preview Sample Receipt")
        alignment = QPushButton("Print Alignment Test")
        for secondary in (preview, alignment):
            secondary.setProperty("secondary", True)
        preview.setToolTip("Show how a receipt will look on the selected printer and paper")
        alignment.setToolTip("Print a ruler strip that shows what this printer can actually mark")
        preview.clicked.connect(self._preview_sample_receipt)
        alignment.clicked.connect(self._print_alignment_test)
        form.addRow("Default printer", self.printer)
        form.addRow("Default receipt format", self.receipt_width)
        form.addRow("Printable width", self.print_width)
        form.addRow("", self.printer_summary)
        form.addRow("", preview)
        form.addRow("", alignment)
        self.printer.currentIndexChanged.connect(self._update_printer_summary)
        self.receipt_width.currentIndexChanged.connect(self._receipt_format_changed)
        self.print_width.valueChanged.connect(self._update_printer_summary)
        self._receipt_format_changed()
        self.tabs.addTab(tab, "Printer")

    def _selected_receipt_format(self) -> ReceiptFormat:
        return receipt_format(str(self.receipt_width.currentData() or ""))

    def _receipt_format_changed(self) -> None:
        """Follow the chosen roll's standard printable strip unless it is overridden."""

        chosen = self._selected_receipt_format()
        thermal = chosen.print_width_mm is not None
        self.print_width.setEnabled(thermal)
        if thermal and chosen.print_width_mm is not None:
            self.print_width.setMaximum(chosen.width_mm or 80)
            self.print_width.setValue(chosen.print_width_mm)
        self._update_printer_summary()

    def _print_alignment_test(self) -> None:
        """Print the calibration strip straight to the selected printer."""

        printer_name = str(self.printer.currentData() or SYSTEM_DEFAULT_PRINTER)
        chosen = self._selected_receipt_format()
        if chosen.width_mm is None:
            QMessageBox.information(
                self,
                "Thermal formats only",
                "The alignment test checks a thermal roll. Choose 58 mm or 80 mm first.",
            )
            return
        paper = chosen.width_mm
        printable = self.print_width.value()

        def operation() -> Path:
            return write_temporary_pdf(
                alignment_test_page(paper, printable), f"alignment-{paper}-{printable}"
            )

        def send(result: object) -> None:
            path = cast(Path, result)
            service = PrinterService()
            try:
                service.print_pdf(
                    path,
                    self,
                    printer_name=printer_name,
                    prompt=not service.resolve_printer_name(printer_name),
                )
            except Exception as error:
                show_error(self, error)

        self._worker = start_worker(
            operation, succeeded=send, failed=lambda error: show_error(self, error)
        )

    def _update_printer_summary(self) -> None:
        service = PrinterService()
        selected = str(self.printer.currentData() or SYSTEM_DEFAULT_PRINTER)
        resolved = service.resolve_printer_name(selected)
        chosen = self._selected_receipt_format()
        summary = (
            f"Invoices print on {resolved or 'no printer (install one first)'} "
            f"using {chosen.label} paper. The print preview follows this choice."
        )
        if chosen.width_mm is not None:
            blank = (chosen.width_mm - self.print_width.value()) / 2
            summary += (
                f" Receipts are laid out across {self.print_width.value()} mm, leaving "
                f"{blank:.1f} mm blank on each side of the roll. Use the alignment test "
                "if the right-hand column is cut off."
            )
        self.printer_summary.setText(summary)

    def _preview_sample_receipt(self) -> None:
        """Render a sample invoice so the printer choice can be checked at once."""

        printer_name = str(self.printer.currentData() or SYSTEM_DEFAULT_PRINTER)
        chosen = receipt_format(str(self.receipt_width.currentData() or ""))

        def operation() -> Path:
            with self._session_factory() as session:
                shop = load_shop_profile(session, self._settings)
            generator = ReceiptGenerator()
            receipt = sample_receipt(shop.currency)
            payload = (
                generator.generate_thermal(receipt, shop, chosen.width_mm)
                if chosen.width_mm is not None
                else generator.generate_a4(receipt, shop)
            )
            return write_temporary_pdf(payload, f"sample-receipt-{chosen.key}")

        self._worker = start_worker(
            lambda: operation(),
            succeeded=lambda path: open_print_preview(
                cast(Path, path),
                self,
                printer_name=printer_name,
                suggested_filename="sample-receipt.pdf",
            ),
            failed=lambda error: show_error(self, error),
        )

    def _build_receipt(self) -> None:
        tab, form = self._tab_form()
        self.receipt_footer = QTextEdit("Thank you for your business.")
        self.receipt_footer.setMaximumHeight(100)
        form.addRow("Footer text", self.receipt_footer)
        self.tabs.addTab(tab, "Receipt")

    def _build_backup(self) -> None:
        tab, form = self._tab_form()
        self.backup_directory = QLineEdit(str(self._settings.backup_directory))
        self.retention = QSpinBox()
        self.retention.setRange(1, 3650)
        self.retention.setValue(self._settings.backup_retention_days)
        backup_now = QPushButton("Back Up Now")
        backup_now.clicked.connect(self._backup_now)
        self.export_data_button = QPushButton("Back Up To Excel && CSV")
        self.export_data_button.clicked.connect(self._export_data)
        self.export_data_button.setEnabled(
            has_permission(self._actor.role, Permission.MANAGE_SETTINGS)
        )
        export_note = QLabel(
            "Writes every table to one spreadsheet and one archive of CSV files, named for the "
            "dates the data covers. Readable anywhere; use a database backup to restore."
        )
        export_note.setWordWrap(True)
        restore = QPushButton("Restore Backup…")
        restore.setProperty("danger", True)
        restore.setEnabled(has_permission(self._actor.role, Permission.RESTORE_DATABASE))
        restore.clicked.connect(self._restore)
        form.addRow("Backup directory", self.backup_directory)
        form.addRow("Retention days", self.retention)
        form.addRow("Database backup", backup_now)
        form.addRow("Data backup", self.export_data_button)
        form.addRow(export_note)
        form.addRow("Owner only", restore)
        self.tabs.addTab(tab, "Backup")

    def _build_database(self) -> None:
        tab, form = self._tab_form()
        form.addRow("Host", QLabel(self._settings.database_host))
        form.addRow("Port", QLabel(str(self._settings.database_port)))
        form.addRow("Database", QLabel(self._settings.database_name))
        form.addRow("User", QLabel(self._settings.database_user))
        notice = QLabel(
            "Database credentials are controlled by environment variables and are never displayed."
        )
        notice.setWordWrap(True)
        form.addRow(notice)
        self.tabs.addTab(tab, "Database")

    def _build_security(self) -> None:
        tab, form = self._tab_form()
        self.session_timeout = QSpinBox()
        self.session_timeout.setRange(5, 1440)
        self.session_timeout.setValue(self._settings.app_session_timeout_minutes)
        form.addRow("Session timeout (minutes)", self.session_timeout)
        form.addRow(QLabel("Password hashing uses Argon2id. Audit logs are append-only."))
        self.tabs.addTab(tab, "Security")

    def _build_email_history(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        controls = QHBoxLayout()
        refresh = QPushButton("Refresh")
        retry = QPushButton("Retry Selected")
        controls.addStretch()
        controls.addWidget(refresh)
        controls.addWidget(retry)
        self.email_model = RowsTableModel(
            ("Recipient", "Subject", "Type", "Status", "Attempts", "Created", "Sent", "Error")
        )
        self.email_table = QTableView()
        self.email_table.setModel(self.email_model)
        self.email_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        layout.addLayout(controls)
        layout.addWidget(self.email_table)
        refresh.clicked.connect(self._load_email_history)
        retry.clicked.connect(self._retry_email)
        self.tabs.addTab(tab, "Email History")

    def _load(self) -> None:
        keys = (
            (SettingCategory.GENERAL, "currency"),
            (SettingCategory.GENERAL, "timezone"),
            (SettingCategory.GENERAL, "invoice_prefix"),
            (SettingCategory.GENERAL, "return_prefix"),
            (SettingCategory.EMAIL, "smtp_host"),
            (SettingCategory.EMAIL, "smtp_port"),
            (SettingCategory.EMAIL, "smtp_username"),
            (SettingCategory.EMAIL, "smtp_use_tls"),
            (SettingCategory.EMAIL, "smtp_from_email"),
            (SettingCategory.EMAIL, "owner_email"),
            (SettingCategory.PRINTER, "default_printer"),
            (SettingCategory.PRINTER, "receipt_width"),
            (SettingCategory.PRINTER, "print_width"),
            (SettingCategory.RECEIPT, "footer"),
            (SettingCategory.BACKUP, "directory"),
            (SettingCategory.BACKUP, "retention_days"),
            (SettingCategory.SECURITY, "session_timeout"),
        )

        def operation() -> dict[tuple[SettingCategory, str], str | None]:
            with self._session_factory() as session:
                service = SettingsService(session, self._settings.app_secret_key.get_secret_value())
                return {(category, key): service.get(category, key) for category, key in keys}

        self._worker = start_worker(
            operation,
            succeeded=self._set_loaded,
            failed=lambda error: show_error(self, error),
        )
        self._load_email_history()

    def _set_loaded(self, values: object) -> None:
        data = cast(dict[tuple[SettingCategory, str], str | None], values)
        mappings = (
            ((SettingCategory.GENERAL, "currency"), self.currency),
            ((SettingCategory.GENERAL, "timezone"), self.timezone),
            ((SettingCategory.GENERAL, "invoice_prefix"), self.invoice_prefix),
            ((SettingCategory.GENERAL, "return_prefix"), self.return_prefix),
            ((SettingCategory.EMAIL, "smtp_host"), self.smtp_host),
            ((SettingCategory.EMAIL, "smtp_username"), self.smtp_username),
            ((SettingCategory.EMAIL, "smtp_from_email"), self.smtp_from),
        )
        for key, widget in mappings:
            if data.get(key) is not None:
                widget.setText(data[key])
        if (footer := data.get((SettingCategory.RECEIPT, "footer"))) is not None:
            self.receipt_footer.setPlainText(footer)
        if port := data.get((SettingCategory.EMAIL, "smtp_port")):
            self.smtp_port.setValue(int(port))
        if tls := data.get((SettingCategory.EMAIL, "smtp_use_tls")):
            self.smtp_tls.setChecked(tls.lower() == "true")
        if (owners := data.get((SettingCategory.EMAIL, "owner_email"))) is not None:
            self._set_owner_emails(parse_owner_emails(owners))
        if directory := data.get((SettingCategory.BACKUP, "directory")):
            self.backup_directory.setText(directory)
        if retention := data.get((SettingCategory.BACKUP, "retention_days")):
            self.retention.setValue(int(retention))
        if timeout := data.get((SettingCategory.SECURITY, "session_timeout")):
            self.session_timeout.setValue(int(timeout))
        stored_printer = (data.get((SettingCategory.PRINTER, "default_printer")) or "").strip()
        if stored_printer and self.printer.findData(stored_printer) < 0:
            # Keep a printer that is currently offline selectable so saving other
            # settings does not silently reset the shop's chosen device.
            self.printer.addItem(f"{stored_printer} (not connected)", stored_printer)
        index = self.printer.findData(stored_printer)
        self.printer.setCurrentIndex(max(0, index))
        width_index = self.receipt_width.findData(
            data.get((SettingCategory.PRINTER, "receipt_width"))
        )
        if width_index >= 0:
            self.receipt_width.setCurrentIndex(width_index)
        self._receipt_format_changed()
        stored_print_width = (data.get((SettingCategory.PRINTER, "print_width")) or "").strip()
        if stored_print_width.isdigit():
            self.print_width.setValue(int(stored_print_width))
        self._update_printer_summary()

    def save(self) -> None:
        values = (
            (SettingCategory.GENERAL, "currency", self.currency.text(), False),
            (SettingCategory.GENERAL, "timezone", self.timezone.text(), False),
            (SettingCategory.GENERAL, "invoice_prefix", self.invoice_prefix.text(), False),
            (SettingCategory.GENERAL, "return_prefix", self.return_prefix.text(), False),
            (SettingCategory.EMAIL, "smtp_host", self.smtp_host.text(), False),
            (SettingCategory.EMAIL, "smtp_port", str(self.smtp_port.value()), False),
            (SettingCategory.EMAIL, "smtp_username", self.smtp_username.text(), False),
            (SettingCategory.EMAIL, "smtp_use_tls", str(self.smtp_tls.isChecked()).lower(), False),
            (SettingCategory.EMAIL, "smtp_from_email", self.smtp_from.text(), False),
            (
                SettingCategory.EMAIL,
                "owner_email",
                OWNER_EMAIL_SEPARATOR.join(self._owner_email_addresses()),
                False,
            ),
            (SettingCategory.PRINTER, "default_printer", self.printer.currentData(), False),
            (SettingCategory.PRINTER, "receipt_width", self.receipt_width.currentData(), False),
            (SettingCategory.PRINTER, "print_width", str(self.print_width.value()), False),
            (SettingCategory.RECEIPT, "footer", self.receipt_footer.toPlainText(), False),
            (SettingCategory.SECURITY, "session_timeout", str(self.session_timeout.value()), False),
            (SettingCategory.BACKUP, "directory", self.backup_directory.text(), False),
            (SettingCategory.BACKUP, "retention_days", str(self.retention.value()), False),
        )
        smtp_password = self.smtp_password.text()
        self.save_button.setEnabled(False)

        def operation() -> None:
            with self._session_factory.begin() as session:
                service = SettingsService(session, self._settings.app_secret_key.get_secret_value())
                for category, key, value, secret in values:
                    service.set(
                        actor=self._actor,
                        category=category,
                        key=key,
                        value=str(value),
                        is_secret=secret,
                    )
                if smtp_password:
                    service.set(
                        actor=self._actor,
                        category=SettingCategory.EMAIL,
                        key="smtp_password",
                        value=smtp_password,
                        is_secret=True,
                    )

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: QMessageBox.information(
                self, "Settings saved", "Application settings were saved and audited."
            ),
            failed=lambda error: show_error(self, error),
            finished=lambda: self.save_button.setEnabled(True),
        )

    def _send_test_email(self) -> None:
        entered_password = self.smtp_password.text()
        recipient = next(iter(self._owner_email_addresses()), "")
        host = self.smtp_host.text()
        port = self.smtp_port.value()
        from_email = self.smtp_from.text()
        username = self.smtp_username.text().strip() or None
        use_tls = self.smtp_tls.isChecked()

        def operation() -> None:
            password = entered_password
            if not password:
                with self._session_factory() as session:
                    stored = SettingsService(
                        session, self._settings.app_secret_key.get_secret_value()
                    )
                    password = (
                        stored.get(
                            SettingCategory.EMAIL,
                            "smtp_password",
                            self._settings.smtp_password.get_secret_value()
                            if self._settings.smtp_password
                            else None,
                        )
                        or ""
                    )
            config = SMTPConfig(
                host=host,
                port=port,
                from_email=from_email,
                username=username,
                password=password or None,
                use_tls=use_tls,
                timeout_seconds=self._settings.smtp_timeout_seconds,
            )
            SMTPEmailService(config).send(
                OutgoingEmail(
                    recipient=recipient,
                    subject="Pesticide Shop Manager - Test Email",
                    text_body="SMTP configuration is working.",
                )
            )

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: QMessageBox.information(
                self, "Test successful", "The test email was sent."
            ),
            failed=lambda error: show_error(self, error),
        )

    def _backup_now(self) -> None:
        runtime_settings = self._backup_runtime_settings()
        self._worker = start_worker(
            lambda: BackupService(runtime_settings).create_backup(self._actor.id),
            succeeded=lambda result: QMessageBox.information(
                self, "Backup complete", f"Created {result.path} ({result.size_bytes:,} bytes)."
            ),
            failed=lambda error: show_error(self, error),
        )

    def _export_data(self) -> None:
        directory = Path(self.backup_directory.text()).expanduser()
        runtime_settings = self._backup_runtime_settings()
        actor = self._actor
        self.export_data_button.setEnabled(False)

        def operation() -> DataExportResult:
            with self._session_factory() as session:
                result = DataExportService(session, runtime_settings).export(directory, actor)
                session.commit()
                return result

        self._worker = start_worker(
            operation,
            succeeded=self._data_exported,
            failed=self._data_export_failed,
        )

    def _data_exported(self, result: DataExportResult) -> None:
        self.export_data_button.setEnabled(True)
        period = (
            f"{format_date(result.period_start)} to {format_date(result.period_end)}"
            if result.period_start
            else "no dated records"
        )
        QMessageBox.information(
            self,
            "Data backup complete",
            f"{result.total_rows:,} rows covering {period} were written to:\n\n"
            f"{result.workbook}\n{result.csv_archive}",
        )

    def _data_export_failed(self, error: Exception) -> None:
        self.export_data_button.setEnabled(True)
        show_error(self, error)

    def _restore(self) -> None:
        runtime_settings = self._backup_runtime_settings()
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Select Database Backup",
            str(runtime_settings.backup_directory),
            "PostgreSQL backups (*.dump *.sql)",
        )
        if not path:
            return
        answer = QMessageBox.warning(
            self,
            "Confirm Database Restore",
            "Restoring replaces current database objects with backup contents. Close all other "
            "application instances first. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        self._worker = start_worker(
            lambda: BackupService(runtime_settings).restore(Path(path), self._actor),
            succeeded=lambda _result: QMessageBox.information(
                self, "Restore complete", "Restart the application before continuing."
            ),
            failed=lambda error: show_error(self, error),
        )

    def _backup_runtime_settings(self) -> Settings:
        return self._settings.model_copy(
            update={
                "backup_directory": Path(self.backup_directory.text()).expanduser(),
                "backup_retention_days": self.retention.value(),
            }
        )

    def _load_email_history(self) -> None:
        def operation() -> tuple[list[tuple[object, ...]], list[uuid.UUID]]:
            with self._session_factory() as session:
                records = list(
                    session.scalars(
                        select(EmailHistory).order_by(EmailHistory.created_at.desc()).limit(500)
                    )
                )
                return (
                    [
                        (
                            record.recipient,
                            record.subject,
                            record.template,
                            record.status.value,
                            record.attempts,
                            format_date(record.created_at),
                            format_date(record.sent_at),
                            record.last_error or "",
                        )
                        for record in records
                    ],
                    [record.id for record in records],
                )

        self._worker = start_worker(
            operation,
            succeeded=self._set_email_history,
            failed=lambda error: show_error(self, error),
        )

    def _set_email_history(self, result: object) -> None:
        rows, self._email_ids = cast(tuple[list[tuple[object, ...]], list[uuid.UUID]], result)
        self.email_model.set_rows(rows)

    def _retry_email(self) -> None:
        selected = self.email_table.selectionModel().selectedRows()
        if not selected:
            QMessageBox.information(self, "Select email", "Select a failed email to retry.")
            return
        email_id = self._email_ids[selected[0].row()]

        def operation() -> None:
            with self._session_factory.begin() as session:
                record = session.get(EmailHistory, email_id)
                if record and record.status is not EmailStatus.SENT:
                    record.status = EmailStatus.PENDING
                    record.last_error = None
            send_email.delay(str(email_id))

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: QMessageBox.information(
                self, "Email queued", "The email was queued for retry."
            ),
            failed=lambda error: show_error(self, error),
        )
