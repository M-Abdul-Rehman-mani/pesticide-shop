"""Invoice/IMEI return workflow with manager approval and refund validation."""

from __future__ import annotations

import tempfile
import uuid
from dataclasses import replace
from pathlib import Path
from typing import cast

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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.config.settings import Settings
from app.models.enums import PaymentMethod, ReturnCondition, ReturnReason, SettingCategory
from app.models.return_record import SaleReturn
from app.printing.printer_service import PrinterService
from app.printing.receipt_generator import ReceiptGenerator, ReturnReceiptData
from app.printing.shop_profile import load_shop_profile
from app.security.authentication import AuthenticatedUser, AuthenticationService
from app.security.permissions import Permission, has_permission, require_permission
from app.services.dto import CreateReturnCommand
from app.services.return_service import ReturnService
from app.services.settings_service import SettingsService
from app.ui.forms import MoneyEdit
from app.ui.widgets import show_error
from app.ui.workers import FunctionWorker, start_worker


class ReturnsScreen(QWidget):
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
        self._last_return_id: uuid.UUID | None = None
        layout = QVBoxLayout(self)
        title = QLabel("Create Sale Return")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        group = QGroupBox("Return details")
        form = QFormLayout(group)
        self.invoice = QLineEdit()
        self.invoice.setPlaceholderText("INV-2026-000001")
        self.imei = QLineEdit()
        self.imei.setMaxLength(15)
        self.reason = QComboBox()
        for reason in ReturnReason:
            self.reason.addItem(reason.value.replace("_", " ").title(), reason)
        self.condition = QComboBox()
        for condition in ReturnCondition:
            self.condition.addItem(condition.value.title(), condition)
        self.refund = MoneyEdit()
        self.method = QComboBox()
        for method in PaymentMethod:
            self.method.addItem(method.value.replace("_", " ").title(), method)
        self.notes = QTextEdit()
        self.notes.setMaximumHeight(80)
        form.addRow("Invoice", self.invoice)
        form.addRow("IMEI", self.imei)
        form.addRow("Reason", self.reason)
        form.addRow("Condition", self.condition)
        form.addRow("Refund amount", self.refund)
        form.addRow("Refund method", self.method)
        form.addRow("Notes", self.notes)
        layout.addWidget(group)
        approval = QGroupBox("Approval")
        approval_form = QFormLayout(approval)
        self.approver_username = QLineEdit()
        self.approver_password = QLineEdit()
        self.approver_password.setEchoMode(QLineEdit.EchoMode.Password)
        if has_permission(actor.role, Permission.APPROVE_RETURN):
            self.approver_username.setPlaceholderText("Leave blank to approve as current user")
        else:
            self.approver_username.setPlaceholderText("Manager or owner username")
        approval_form.addRow("Approver username", self.approver_username)
        approval_form.addRow("Approver password", self.approver_password)
        layout.addWidget(approval)
        actions = QHBoxLayout()
        self.save_pdf = QPushButton("Save Return PDF")
        self.save_pdf.setProperty("secondary", True)
        self.save_pdf.setEnabled(False)
        self.preview = QPushButton("Print Preview")
        self.preview.setProperty("secondary", True)
        self.preview.setEnabled(False)
        self.save_button = QPushButton("Complete Return and Refund")
        actions.addWidget(self.save_pdf)
        actions.addWidget(self.preview)
        actions.addStretch()
        actions.addWidget(self.save_button)
        layout.addLayout(actions)
        layout.addStretch()
        self.save_button.clicked.connect(self.save)
        self.save_pdf.clicked.connect(self._save_last_pdf)
        self.preview.clicked.connect(self._preview_last)

    def save(self) -> None:
        base_command = CreateReturnCommand(
            invoice_number=self.invoice.text(),
            imei=self.imei.text(),
            reason=self.reason.currentData(),
            condition=self.condition.currentData(),
            refund_amount=self.refund.decimal_value("Refund"),
            refund_method=self.method.currentData(),
            approved_by=self._actor.id,
            notes=self.notes.toPlainText().strip() or None,
        )
        username = self.approver_username.text().strip()
        password = self.approver_password.text()
        self.save_button.setEnabled(False)

        def operation() -> tuple[uuid.UUID, str]:
            with self._session_factory.begin() as session:
                approver = self._actor
                if username:
                    approver = AuthenticationService(session).authenticate(username, password)
                require_permission(approver.role, Permission.APPROVE_RETURN)
                command = replace(base_command, approved_by=approver.id)
                stored = SettingsService(session, self._settings.app_secret_key.get_secret_value())
                document = ReturnService(session).create(
                    command,
                    self._actor,
                    return_prefix=stored.get(SettingCategory.GENERAL, "return_prefix", "RET")
                    or "RET",
                    shop_name=stored.get(SettingCategory.SHOP, "name", "Mobile Shop")
                    or "Mobile Shop",
                    owner_email=stored.get(
                        SettingCategory.EMAIL, "owner_email", self._settings.owner_email
                    ),
                )
                return document.id, document.return_number

        self._worker = start_worker(
            operation,
            succeeded=self._saved,
            failed=lambda error: show_error(self, error),
            finished=lambda: self.save_button.setEnabled(True),
        )

    def _saved(self, return_number: object) -> None:
        return_id, number = cast(tuple[uuid.UUID, str], return_number)
        self._last_return_id = return_id
        self.save_pdf.setEnabled(True)
        self.preview.setEnabled(True)
        QMessageBox.information(
            self,
            "Return completed",
            f"Return {number}, refund, inventory history, and audit event were committed.",
        )
        for field in (self.invoice, self.imei, self.approver_username, self.approver_password):
            field.clear()
        self.refund.setText("0.00")
        self.notes.clear()

    def _receipt_payload(self) -> bytes:
        if self._last_return_id is None:
            from app.utils.exceptions import ConflictError

            raise ConflictError("Complete a return before generating its receipt.")
        with self._session_factory() as session:
            document = session.execute(
                select(SaleReturn)
                .options(selectinload(SaleReturn.items))
                .where(SaleReturn.id == self._last_return_id)
            ).scalar_one()
            return ReceiptGenerator().generate_return_a4(
                ReturnReceiptData.from_return(document),
                load_shop_profile(session, self._settings),
            )

    def _save_last_pdf(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save return receipt", "return-receipt.pdf", "PDF documents (*.pdf)"
        )
        if not path:
            return
        self._worker = start_worker(
            lambda: PrinterService().save_pdf(Path(path), self._receipt_payload()),
            succeeded=lambda saved: QMessageBox.information(
                self, "Return receipt saved", f"Saved to {saved}"
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
