"""Damage intake with inventory transition and owner notification."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
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
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import Settings
from app.models.enums import DamageType, SettingCategory
from app.security.authentication import AuthenticatedUser
from app.services.damage_service import DamageService
from app.services.dto import CreateDamageCommand
from app.services.settings_service import SettingsService
from app.ui.forms import MoneyEdit
from app.ui.widgets import show_error
from app.ui.workers import FunctionWorker, start_worker


class DamagesScreen(QWidget):
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
        layout = QVBoxLayout(self)
        title = QLabel("Record Damage")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        group = QGroupBox("Damage details")
        form = QFormLayout(group)
        self.imei = QLineEdit()
        self.imei.setMaxLength(15)
        self.damage_type = QComboBox()
        for value in DamageType:
            self.damage_type.addItem(value.value.title(), value)
        self.description = QTextEdit()
        self.description.setMaximumHeight(100)
        self.estimated_loss = MoneyEdit()
        self.repair_cost = MoneyEdit()
        self.notes = QTextEdit()
        self.notes.setMaximumHeight(80)
        form.addRow("IMEI", self.imei)
        form.addRow("Damage type", self.damage_type)
        form.addRow("Description", self.description)
        form.addRow("Estimated loss", self.estimated_loss)
        form.addRow("Repair cost", self.repair_cost)
        form.addRow("Notes", self.notes)
        layout.addWidget(group)
        actions = QHBoxLayout()
        self.save_button = QPushButton("Record Damage")
        actions.addStretch()
        actions.addWidget(self.save_button)
        layout.addLayout(actions)
        layout.addStretch()
        self.save_button.clicked.connect(self.save)

    def save(self) -> None:
        command = CreateDamageCommand(
            imei=self.imei.text(),
            damage_type=self.damage_type.currentData(),
            description=self.description.toPlainText(),
            estimated_loss=self.estimated_loss.decimal_value("Estimated loss"),
            repair_cost=self.repair_cost.decimal_value("Repair cost"),
            notes=self.notes.toPlainText().strip() or None,
        )
        self.save_button.setEnabled(False)

        def operation() -> str:
            with self._session_factory.begin() as session:
                stored = SettingsService(session, self._settings.app_secret_key.get_secret_value())
                return (
                    DamageService(session)
                    .create(
                        command,
                        self._actor,
                        owner_email=stored.get(
                            SettingCategory.EMAIL,
                            "owner_email",
                            self._settings.owner_email,
                        ),
                    )
                    .damage_number
                )

        self._worker = start_worker(
            operation,
            succeeded=self._saved,
            failed=lambda error: show_error(self, error),
            finished=lambda: self.save_button.setEnabled(True),
        )

    def _saved(self, damage_number: object) -> None:
        QMessageBox.information(
            self,
            "Damage recorded",
            f"Damage {damage_number} and its inventory transaction were committed.",
        )
        self.imei.clear()
        self.description.clear()
        self.estimated_loss.setText("0.00")
        self.repair_cost.setText("0.00")
        self.notes.clear()
