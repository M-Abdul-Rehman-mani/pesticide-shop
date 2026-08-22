"""Desktop application startup and lifecycle controller."""

from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

from app.config.settings import Settings
from app.ui.theme import APPLICATION_STYLESHEET

logger = logging.getLogger(__name__)


class ApplicationController(QObject):
    def __init__(self, application: QApplication, settings: Settings) -> None:
        super().__init__()
        from app.database.session import SessionFactory

        self._application = application
        self._settings = settings
        self._session_factory = SessionFactory
        self._window: QMainWindow | None = None

    def start(self) -> bool:
        return self._show_login()

    def _show_login(self) -> bool:
        from app.ui.login_window import ChangePasswordDialog, LoginDialog
        from app.ui.main_window import MainWindow

        login = LoginDialog(self._session_factory)
        if login.exec() != LoginDialog.DialogCode.Accepted or login.authenticated_user is None:
            return False
        user = login.authenticated_user
        if user.must_change_password:
            change = ChangePasswordDialog(self._session_factory, user)
            if change.exec() != ChangePasswordDialog.DialogCode.Accepted:
                QTimer.singleShot(0, self._application.quit)
                return False
        window = MainWindow(self._session_factory, user, self._load_runtime_settings())
        window.logout_requested.connect(self._logout)
        self._window = window
        window.show()
        return True

    def _load_runtime_settings(self) -> Settings:
        """Overlay non-secret database preferences after every successful login."""

        from app.models.enums import SettingCategory
        from app.services.settings_service import SettingsService

        with self._session_factory() as session:
            stored = SettingsService(session, self._settings.app_secret_key.get_secret_value())
            return self._settings.model_copy(
                update={
                    "app_currency": stored.get(
                        SettingCategory.GENERAL,
                        "currency",
                        self._settings.app_currency,
                    )
                    or self._settings.app_currency,
                    "app_timezone": stored.get(
                        SettingCategory.GENERAL,
                        "timezone",
                        self._settings.app_timezone,
                    )
                    or self._settings.app_timezone,
                    "app_session_timeout_minutes": int(
                        stored.get(
                            SettingCategory.SECURITY,
                            "session_timeout",
                            str(self._settings.app_session_timeout_minutes),
                        )
                        or self._settings.app_session_timeout_minutes
                    ),
                    "owner_email": stored.get(
                        SettingCategory.EMAIL,
                        "owner_email",
                        self._settings.owner_email,
                    ),
                    "backup_directory": Path(
                        stored.get(
                            SettingCategory.BACKUP,
                            "directory",
                            str(self._settings.backup_directory),
                        )
                        or self._settings.backup_directory
                    ),
                    "backup_retention_days": int(
                        stored.get(
                            SettingCategory.BACKUP,
                            "retention_days",
                            str(self._settings.backup_retention_days),
                        )
                        or self._settings.backup_retention_days
                    ),
                }
            )

    def _logout(self) -> None:
        if self._window is not None:
            window = self._window
            self._window = None
            window.close()
            window.deleteLater()
        QTimer.singleShot(0, self._relogin)

    def _relogin(self) -> None:
        if not self._show_login():
            self._application.quit()


def _install_exception_hook() -> None:
    def handle(exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
        logger.critical(
            "Unhandled exception\n%s", "".join(traceback.format_exception(exc_type, exc, tb))
        )
        QMessageBox.critical(
            None,
            "Unexpected error",
            "An unexpected error occurred. Details were written to logs/errors.log.",
        )

    sys.excepthook = handle


def _expected_revision() -> str:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    revision = ScriptDirectory.from_config(config).get_current_head()
    if revision is None:
        raise RuntimeError("No Alembic head revision is configured")
    return revision


def main() -> int:
    from pydantic import ValidationError as ConfigurationValidationError

    from app.config.logging import configure_logging
    from app.config.settings import get_settings

    application = QApplication(sys.argv)
    application.setApplicationName("Pesticide Shop Manager")
    application.setOrganizationName("Crop Care Retail")
    application.setStyle("Fusion")
    application.setStyleSheet(APPLICATION_STYLESHEET)
    try:
        settings = get_settings()
    except ConfigurationValidationError as exc:
        QMessageBox.critical(
            None,
            "Configuration error",
            "Application configuration is incomplete or invalid. Review .env.\n\n" + str(exc),
        )
        return 2
    configure_logging(settings.log_directory, settings.app_debug)
    _install_exception_hook()
    from app.database.connection import check_database
    from app.database.session import engine

    health = check_database(engine)
    if not health.available:
        QMessageBox.critical(
            None,
            "Database connection unavailable",
            "Database connection unavailable.\n\n"
            "Please check that PostgreSQL is running and try again.",
        )
        return 3
    expected = _expected_revision()
    if health.migration_revision != expected:
        QMessageBox.critical(
            None,
            "Database upgrade required",
            "The database schema is not current. Close the application and run:\n\n"
            "alembic upgrade head",
        )
        return 4
    controller = ApplicationController(application, settings)
    if not controller.start():
        return 0
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
