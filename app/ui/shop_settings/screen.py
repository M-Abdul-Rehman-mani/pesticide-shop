"""Optional shop identity fields used to brand customer receipts."""

from __future__ import annotations

from pathlib import Path
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
from app.ui.widgets import show_error, wrap_scroll
from app.ui.workers import FunctionWorker, start_worker
from app.utils.paths import store_shop_logo


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
        "billing_address",
        "billing_phone",
        "billing_email",
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
        root.setContentsMargins(16, 12, 16, 10)
        header = QHBoxLayout()
        title = QLabel("Shop Settings")
        title.setObjectName("PageTitle")
        self.save_button = QPushButton("Save Shop Settings")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.save_button)
        root.addLayout(header)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        notice = QLabel(
            "All fields are optional. Any value you enter here is printed on sale and return "
            "receipts; blank fields are omitted."
        )
        notice.setWordWrap(True)
        body_layout.addWidget(notice)

        group = QGroupBox("Shop identity and receipt branding")
        form = QFormLayout(group)
        form.setContentsMargins(16, 16, 16, 16)
        self.shop_name = QLineEdit()
        self.owner_name = QLineEdit()
        self.address = QTextEdit()
        self.address.setMaximumHeight(70)
        self.phone = QLineEdit()
        self.email = QLineEdit()
        self.website = QLineEdit()
        self.tax_information = QLineEdit()
        self.billing_address = QTextEdit()
        self.billing_address.setMaximumHeight(70)
        self.billing_address.setPlaceholderText("Leave blank to reuse the counter address")
        self.billing_phone = QLineEdit()
        self.billing_phone.setPlaceholderText("Leave blank to reuse the counter number")
        self.billing_email = QLineEdit()
        self.billing_email.setPlaceholderText("Leave blank to reuse the counter email")
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
        self.logo_status = QLabel()
        self.logo_status.setWordWrap(True)

        form.addRow("Shop name", self.shop_name)
        form.addRow("Owner name", self.owner_name)
        form.addRow("Address", self.address)
        form.addRow("Contact number", self.phone)
        form.addRow("Email", self.email)
        form.addRow("Website", self.website)
        form.addRow("Tax / registration", self.tax_information)
        billing_note = QLabel(
            "Billing details appear on the A4 delivery challan and the emailed copy. "
            "The details above are printed on the counter receipt. Leave a billing "
            "field blank to reuse the counter one."
        )
        billing_note.setWordWrap(True)
        billing_note.setObjectName("PageSubtitle")
        form.addRow("", billing_note)
        form.addRow("Billing address", self.billing_address)
        form.addRow("Billing contact number", self.billing_phone)
        form.addRow("Billing email", self.billing_email)
        form.addRow("Logo", logo_row)
        form.addRow("", self.logo_status)
        body_layout.addWidget(group)
        body_layout.addStretch()
        root.addWidget(wrap_scroll(body), 1)

        choose_logo.clicked.connect(self._choose_logo)
        clear_logo.clicked.connect(self.logo_path.clear)
        self.logo_path.textChanged.connect(self._update_logo_status)
        self.save_button.clicked.connect(self.save)
        self._load()

    def _choose_logo(self) -> None:
        """Copy the chosen image into application storage and point at that copy.

        Referencing the file where the operator found it means the logo silently
        vanishes from receipts once that file is moved, renamed, or the shop runs
        on a different machine.
        """

        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Choose shop logo",
            "",
            "Images (*.png *.jpg *.jpeg)",
        )
        if not path:
            return
        try:
            stored = store_shop_logo(Path(path))
        except OSError as error:
            QMessageBox.warning(
                self,
                "Logo could not be used",
                f"The image could not be copied into the application:\n{error}",
            )
            return
        self.logo_path.setText(str(stored))

    def _update_logo_status(self) -> None:
        """Say plainly whether the configured logo can actually be printed."""

        text = self.logo_path.text().strip()
        if not text:
            self.logo_status.setText("No logo set. Receipts print the shop name only.")
            self.logo_status.setStyleSheet("color: #65786f;")
            return
        if Path(text).expanduser().is_file():
            self.logo_status.setText("Logo found; it prints on invoices and receipts.")
            self.logo_status.setStyleSheet("color: #176d49;")
        else:
            self.logo_status.setText(
                "This image is missing, so nothing will print. Choose the file again."
            )
            self.logo_status.setStyleSheet("color: #b8362c;")

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
        self.billing_address.setPlainText(data["billing_address"])
        self.billing_phone.setText(data["billing_phone"])
        self.billing_email.setText(data["billing_email"])
        self._update_logo_status()

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
            "billing_address": self.billing_address.toPlainText(),
            "billing_phone": self.billing_phone.text(),
            "billing_email": self.billing_email.text(),
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
