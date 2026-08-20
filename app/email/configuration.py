"""Resolve SMTP settings from encrypted database preferences with environment fallback."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.email.smtp_client import SMTPConfig
from app.models.enums import SettingCategory
from app.services.settings_service import SettingsService


def load_smtp_config(session: Session, settings: Settings) -> SMTPConfig:
    stored = SettingsService(session, settings.app_secret_key.get_secret_value())
    host = stored.get(SettingCategory.EMAIL, "smtp_host", settings.smtp_host or "") or ""
    port = int(
        stored.get(SettingCategory.EMAIL, "smtp_port", str(settings.smtp_port))
        or settings.smtp_port
    )
    username = stored.get(SettingCategory.EMAIL, "smtp_username", settings.smtp_username)
    password = stored.get(
        SettingCategory.EMAIL,
        "smtp_password",
        settings.smtp_password.get_secret_value() if settings.smtp_password else None,
    )
    from_email = (
        stored.get(SettingCategory.EMAIL, "smtp_from_email", settings.smtp_from_email or "") or ""
    )
    use_tls = (
        stored.get(
            SettingCategory.EMAIL,
            "smtp_use_tls",
            "true" if settings.smtp_use_tls else "false",
        )
        or "true"
    ).lower() in {"1", "true", "yes", "on"}
    return SMTPConfig(
        host=host,
        port=port,
        from_email=from_email,
        username=username,
        password=password,
        use_tls=use_tls,
        timeout_seconds=settings.smtp_timeout_seconds,
    )
