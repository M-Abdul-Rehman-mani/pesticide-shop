"""Authorized database settings with encryption for sensitive values."""

from __future__ import annotations

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import SettingCategory
from app.models.settings import AppSetting
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, require_permission
from app.services.audit_service import AuditService
from app.utils.exceptions import ValidationError
from app.utils.security import SecretCipher
from app.utils.validators import normalize_email, normalize_phone

_PREFIX_PATTERN = re.compile(r"^[A-Z0-9-]{1,12}$")


class SettingsService:
    def __init__(self, session: Session, application_secret: str) -> None:
        self._session = session
        self._cipher = SecretCipher(application_secret)
        self._audit = AuditService(session)

    def get(self, category: SettingCategory, key: str, default: str | None = None) -> str | None:
        setting = self._session.execute(
            select(AppSetting).where(AppSetting.category == category, AppSetting.key == key)
        ).scalar_one_or_none()
        if setting is None:
            return default
        return self._cipher.decrypt(setting.value) if setting.is_secret else setting.value

    def set(
        self,
        *,
        actor: AuthenticatedUser,
        category: SettingCategory,
        key: str,
        value: str,
        is_secret: bool = False,
    ) -> AppSetting:
        require_permission(actor.role, Permission.MANAGE_SETTINGS)
        value = self._validated_value(category, key, value, is_secret=is_secret)
        setting = self._session.execute(
            select(AppSetting)
            .where(AppSetting.category == category, AppSetting.key == key)
            .with_for_update(of=AppSetting)
        ).scalar_one_or_none()
        stored_value = self._cipher.encrypt(value) if is_secret else value
        old_for_audit = (
            "***" if setting and setting.is_secret else (setting.value if setting else None)
        )
        if setting is None:
            setting = AppSetting(
                category=category, key=key, value=stored_value, is_secret=is_secret
            )
            self._session.add(setting)
            self._session.flush()
        else:
            setting.value = stored_value
            setting.is_secret = is_secret
        self._audit.record(
            actor_id=actor.id,
            action="SETTINGS_CHANGED",
            entity_type="AppSetting",
            entity_id=setting.id,
            old_value={"value": old_for_audit},
            new_value={
                "category": category.value,
                "key": key,
                "value": "***" if is_secret else value,
            },
        )
        return setting

    @staticmethod
    def _validated_value(
        category: SettingCategory,
        key: str,
        value: str,
        *,
        is_secret: bool,
    ) -> str:
        if is_secret:
            if not value:
                raise ValidationError("A secret setting cannot be empty.")
            return value
        cleaned = value.strip()
        if (category, key) == (SettingCategory.GENERAL, "currency"):
            if len(cleaned) != 3 or not cleaned.isalpha():
                raise ValidationError("Currency must be a three-letter code such as PKR.")
            return cleaned.upper()
        if (category, key) == (SettingCategory.GENERAL, "timezone"):
            try:
                ZoneInfo(cleaned)
            except ZoneInfoNotFoundError as exc:
                raise ValidationError("Enter a valid IANA timezone such as Asia/Karachi.") from exc
        if category is SettingCategory.GENERAL and key in {"invoice_prefix", "return_prefix"}:
            prefix = cleaned.upper()
            if not _PREFIX_PATTERN.fullmatch(prefix):
                raise ValidationError(
                    "Document prefixes may contain 1-12 letters, numbers, or hyphens."
                )
            return prefix
        if (category, key) == (SettingCategory.SHOP, "name") and not cleaned:
            raise ValidationError("Shop name is required.")
        if category in {SettingCategory.SHOP, SettingCategory.EMAIL} and key in {
            "email",
            "owner_email",
            "smtp_from_email",
        }:
            return normalize_email(cleaned) or ""
        if (category, key) == (SettingCategory.SHOP, "phone"):
            return normalize_phone(cleaned, required=False)
        if (category, key) == (SettingCategory.EMAIL, "smtp_port"):
            try:
                port = int(cleaned)
            except ValueError as exc:
                raise ValidationError("SMTP port must be a number.") from exc
            if port not in range(1, 65_536):
                raise ValidationError("SMTP port must be between 1 and 65535.")
            return str(port)
        if (category, key) == (SettingCategory.EMAIL, "smtp_use_tls"):
            if cleaned.lower() not in {"true", "false"}:
                raise ValidationError("SMTP TLS must be true or false.")
            return cleaned.lower()
        if (category, key) == (
            SettingCategory.PRINTER,
            "receipt_width",
        ) and cleaned not in {"A4", "58", "80"}:
            raise ValidationError("Receipt width must be A4, 58, or 80.")
        if (category, key) in {
            (SettingCategory.SECURITY, "session_timeout"),
            (SettingCategory.BACKUP, "retention_days"),
        }:
            try:
                number = int(cleaned)
            except ValueError as exc:
                raise ValidationError("This setting must be a whole number.") from exc
            upper = 1440 if category is SettingCategory.SECURITY else 3650
            lower = 1 if category is SettingCategory.BACKUP else 5
            if number not in range(lower, upper + 1):
                raise ValidationError(f"This setting must be between {lower} and {upper}.")
            return str(number)
        if (category, key) == (SettingCategory.BACKUP, "directory") and not cleaned:
            raise ValidationError("Backup directory is required.")
        return cleaned
