"""Authorized email, receipt, printer, backup, and security settings."""

from __future__ import annotations

import uuid
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
    QMessageBox,
    QPushButton,
    QScrollArea,
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
from app.email.email_service import OutgoingEmail
from app.email.smtp_client import SMTPConfig, SMTPEmailService
from app.models.email_history import EmailHistory
from app.models.enums import EmailStatus, SettingCategory
from app.printing.printer_service import PrinterService
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, has_permission
from app.services.backup_service import BackupService
from app.services.settings_service import SettingsService
from app.tasks.email_tasks import send_email
from app.ui.widgets import RowsTableModel, show_error
from app.ui.workers import FunctionWorker, start_worker
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
        form.addRow("Currency", self.currency)
        form.addRow("Timezone", self.timezone)
        form.addRow("Invoice prefix", self.invoice_prefix)
        self.tabs.addTab(tab, "General")

    def _build_email(self) -> None:
        tab, form = self._tab_form()
        self.smtp_host = QLineEdit(self._settings.smtp_host or "")
        self.smtp_port = QSpinBox()
        self.smtp_port.setRange(1, 65535)
        self.smtp_port.setValue(self._settings.smtp_port)
        self.smtp_username = QLineEdit(self._settings.smtp_username or "")
        self.smtp_password = QLineEdit()
        self.smtp_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.smtp_password.setPlaceholderText("Leave blank to keep the saved password")
        self.smtp_tls = QCheckBox("Use STARTTLS")
        self.smtp_tls.setChecked(self._settings.smtp_use_tls)
        self.smtp_from = QLineEdit(self._settings.smtp_from_email or "")
        self.owner_email = QLineEdit(self._settings.owner_email or "")
        test = QPushButton("Send Test Email")
        test.setProperty("secondary", True)
        test.clicked.connect(self._send_test_email)
        form.addRow("SMTP host", self.smtp_host)
        form.addRow("SMTP port", self.smtp_port)
        form.addRow("Username", self.smtp_username)
        form.addRow("Password", self.smtp_password)
        form.addRow("Security", self.smtp_tls)
        form.addRow("Sender email", self.smtp_from)
        form.addRow("Owner email", self.owner_email)
        form.addRow("", test)
        self.tabs.addTab(tab, "Email")

    def _build_printer(self) -> None:
        tab, form = self._tab_form()
        self.printer = QComboBox()
        self.printer.addItem("System default", "")
        for printer in PrinterService().available_printers():
            self.printer.addItem(printer, printer)
        self.receipt_width = QComboBox()
        self.receipt_width.addItem("A4", "A4")
        self.receipt_width.addItem("58 mm thermal", "58")
        self.receipt_width.addItem("80 mm thermal", "80")
        form.addRow("Default printer", self.printer)
        form.addRow("Default receipt format", self.receipt_width)
        self.tabs.addTab(tab, "Printer")

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
        restore = QPushButton("Restore Backup…")
        restore.setProperty("danger", True)
        restore.setEnabled(has_permission(self._actor.role, Permission.RESTORE_DATABASE))
        restore.clicked.connect(self._restore)
        form.addRow("Backup directory", self.backup_directory)
        form.addRow("Retention days", self.retention)
        form.addRow("", backup_now)
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
            (SettingCategory.EMAIL, "smtp_host"),
            (SettingCategory.EMAIL, "smtp_port"),
            (SettingCategory.EMAIL, "smtp_username"),
            (SettingCategory.EMAIL, "smtp_use_tls"),
            (SettingCategory.EMAIL, "smtp_from_email"),
            (SettingCategory.EMAIL, "owner_email"),
            (SettingCategory.PRINTER, "default_printer"),
            (SettingCategory.PRINTER, "receipt_width"),
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
            ((SettingCategory.EMAIL, "smtp_host"), self.smtp_host),
            ((SettingCategory.EMAIL, "smtp_username"), self.smtp_username),
            ((SettingCategory.EMAIL, "smtp_from_email"), self.smtp_from),
            ((SettingCategory.EMAIL, "owner_email"), self.owner_email),
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
        if directory := data.get((SettingCategory.BACKUP, "directory")):
            self.backup_directory.setText(directory)
        if retention := data.get((SettingCategory.BACKUP, "retention_days")):
            self.retention.setValue(int(retention))
        if timeout := data.get((SettingCategory.SECURITY, "session_timeout")):
            self.session_timeout.setValue(int(timeout))
        for key, combo in (
            ((SettingCategory.PRINTER, "default_printer"), self.printer),
            ((SettingCategory.PRINTER, "receipt_width"), self.receipt_width),
        ):
            index = combo.findData(data.get(key))
            if index >= 0:
                combo.setCurrentIndex(index)

    def save(self) -> None:
        values = (
            (SettingCategory.GENERAL, "currency", self.currency.text(), False),
            (SettingCategory.GENERAL, "timezone", self.timezone.text(), False),
            (SettingCategory.GENERAL, "invoice_prefix", self.invoice_prefix.text(), False),
            (SettingCategory.EMAIL, "smtp_host", self.smtp_host.text(), False),
            (SettingCategory.EMAIL, "smtp_port", str(self.smtp_port.value()), False),
            (SettingCategory.EMAIL, "smtp_username", self.smtp_username.text(), False),
            (SettingCategory.EMAIL, "smtp_use_tls", str(self.smtp_tls.isChecked()).lower(), False),
            (SettingCategory.EMAIL, "smtp_from_email", self.smtp_from.text(), False),
            (SettingCategory.EMAIL, "owner_email", self.owner_email.text(), False),
            (SettingCategory.PRINTER, "default_printer", self.printer.currentData(), False),
            (SettingCategory.PRINTER, "receipt_width", self.receipt_width.currentData(), False),
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
        recipient = self.owner_email.text()
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
