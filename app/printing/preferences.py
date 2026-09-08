"""Printer preferences shared by every screen that prints a document."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.models.enums import SettingCategory
from app.printing.printer_service import (
    DEFAULT_RECEIPT_FORMAT,
    SYSTEM_DEFAULT_PRINTER,
    ReceiptFormat,
    receipt_format,
)
from app.services.settings_service import SettingsService


@dataclass(frozen=True, slots=True)
class PrintPreferences:
    """The printer and receipt geometry chosen in Settings > Printer."""

    printer_name: str = SYSTEM_DEFAULT_PRINTER
    receipt: ReceiptFormat = DEFAULT_RECEIPT_FORMAT
    #: Overrides the roll's standard printable strip when a particular printer
    #: marks a narrower or wider band than the 203 dpi norm.
    print_width_mm: int | None = None

    @property
    def effective_print_width_mm(self) -> int | None:
        """The width receipts are laid out across, honouring any override."""

        return self.print_width_mm or self.receipt.print_width_mm


def load_print_preferences(session: Session, settings: Settings) -> PrintPreferences:
    stored = SettingsService(session, settings.app_secret_key.get_secret_value())
    override = (stored.get(SettingCategory.PRINTER, "print_width", "") or "").strip()
    return PrintPreferences(
        printer_name=(
            stored.get(SettingCategory.PRINTER, "default_printer", SYSTEM_DEFAULT_PRINTER) or ""
        ).strip(),
        receipt=receipt_format(stored.get(SettingCategory.PRINTER, "receipt_width")),
        print_width_mm=int(override) if override.isdigit() else None,
    )
