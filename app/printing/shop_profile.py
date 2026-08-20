"""Load receipt branding from encrypted application settings."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.models.enums import SettingCategory
from app.printing.receipt_generator import ShopProfile
from app.services.settings_service import SettingsService


def load_shop_profile(session: Session, settings: Settings) -> ShopProfile:
    """Return the current database-managed receipt profile with environment defaults."""

    stored = SettingsService(session, settings.app_secret_key.get_secret_value())
    logo_value = stored.get(SettingCategory.SHOP, "logo_path", "") or ""
    return ShopProfile(
        name=stored.get(SettingCategory.SHOP, "name", "Mobile Shop") or "Mobile Shop",
        address=stored.get(SettingCategory.SHOP, "address", "") or "",
        phone=stored.get(SettingCategory.SHOP, "phone", "") or "",
        email=stored.get(SettingCategory.SHOP, "email", "") or "",
        tax_information=stored.get(SettingCategory.SHOP, "tax_information", "") or "",
        currency=stored.get(SettingCategory.GENERAL, "currency", settings.app_currency)
        or settings.app_currency,
        footer=stored.get(
            SettingCategory.RECEIPT,
            "footer",
            "Thank you for your business.",
        )
        or "Thank you for your business.",
        logo_path=Path(logo_value).expanduser() if logo_value else None,
    )
