"""Optional shop identity fields used to brand customer receipts."""

from __future__ import annotations

from typing import cast

from PySide6.QtWidgets import (
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
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import Settings
from app.models.enums import SettingCategory
from app.security.authentication import AuthenticatedUser
from app.services.settings_service import SettingsService
from app.ui.widgets import show_error
from app.ui.workers import FunctionWorker, start_worker


class ShopSettingsScreen(QWidget):
    """Manage the optional shop profile printed on receipts."""

    _KEYS = (
        "name",
        "owner_name",
        "address",
        "phone",
        "email",
        "website",
        "tax_information",
        "logo_path",
    )

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

        root = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("Shop Settings")
        title.setObjectName("PageTitle")
        self.save_button = QPushButton("Save Shop Settings")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.save_button)
        root.addLayout(header)

        notice = QLabel(
            "All fields are optional. Any value you enter here is printed on sale and return "
            "receipts; blank fields are omitted."
        )
        notice.setWordWrap(True)
        root.addWidget(notice)

        group = QGroupBox("Shop identity and receipt branding")
        form = QFormLayout(group)
        form.setContentsMargins(24, 24, 24, 24)
        self.shop_name = QLineEdit()
        self.owner_name = QLineEdit()
        self.address = QTextEdit()
        self.address.setMaximumHeight(90)
        self.phone = QLineEdit()
        self.email = QLineEdit()
        self.website = QLineEdit()
        self.tax_information = QLineEdit()
        self.logo_path = QLineEdit()
        self.logo_path.setPlaceholderText("Optional PNG or JPEG image")

        choose_logo = QPushButton("Choose…")
        choose_logo.setProperty("secondary", True)
        clear_logo = QPushButton("Clear")
        clear_logo.setProperty("secondary", True)
        logo_row = QHBoxLayout()
        logo_row.addWidget(self.logo_path)
        logo_row.addWidget(choose_logo)
        logo_row.addWidget(clear_logo)

        form.addRow("Shop name", self.shop_name)
        form.addRow("Owner name", self.owner_name)
        form.addRow("Address", self.address)
        form.addRow("Contact number", self.phone)
        form.addRow("Email", self.email)
        form.addRow("Website", self.website)
        form.addRow("Tax / registration", self.tax_information)
        form.addRow("Logo", logo_row)
        root.addWidget(group)
        root.addStretch()

        choose_logo.clicked.connect(self._choose_logo)
        clear_logo.clicked.connect(self.logo_path.clear)
        self.save_button.clicked.connect(self.save)
        self._load()

    def _choose_logo(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Choose shop logo",
            "",
            "Images (*.png *.jpg *.jpeg)",
        )
        if path:
            self.logo_path.setText(path)

    def refresh(self) -> None:
        self._load()

    def _load(self) -> None:
        def operation() -> dict[str, str]:
            with self._session_factory() as session:
                service = SettingsService(
                    session,
                    self._settings.app_secret_key.get_secret_value(),
                )
                return {key: service.get(SettingCategory.SHOP, key, "") or "" for key in self._KEYS}

        self._worker = start_worker(
            operation,
            succeeded=self._set_loaded,
            failed=lambda error: show_error(self, error),
        )

    def _set_loaded(self, values: object) -> None:
        data = cast(dict[str, str], values)
        self.shop_name.setText(data["name"])
        self.owner_name.setText(data["owner_name"])
        self.address.setPlainText(data["address"])
        self.phone.setText(data["phone"])
        self.email.setText(data["email"])
        self.website.setText(data["website"])
        self.tax_information.setText(data["tax_information"])
        self.logo_path.setText(data["logo_path"])

    def save(self) -> None:
        values = {
            "name": self.shop_name.text(),
            "owner_name": self.owner_name.text(),
            "address": self.address.toPlainText(),
            "phone": self.phone.text(),
            "email": self.email.text(),
            "website": self.website.text(),
            "tax_information": self.tax_information.text(),
            "logo_path": self.logo_path.text(),
        }
        self.save_button.setEnabled(False)

        def operation() -> None:
            with self._session_factory.begin() as session:
                service = SettingsService(
                    session,
                    self._settings.app_secret_key.get_secret_value(),
                )
                for key, value in values.items():
                    service.set(
                        actor=self._actor,
                        category=SettingCategory.SHOP,
                        key=key,
                        value=value,
                    )

        self._worker = start_worker(
            operation,
            succeeded=lambda _result: QMessageBox.information(
                self,
                "Shop settings saved",
                "Shop details were saved and will appear on newly generated receipts.",
            ),
            failed=lambda error: show_error(self, error),
            finished=lambda: self.save_button.setEnabled(True),
        )
