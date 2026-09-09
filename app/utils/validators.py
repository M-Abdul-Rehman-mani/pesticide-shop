"""Reusable validation and normalization routines."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.utils.exceptions import ValidationError

MONEY_QUANTUM = Decimal("0.01")
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_PATTERN = re.compile(r"^\+?[0-9][0-9 ()-]{5,24}$")


def normalize_email(value: str | None, *, required: bool = False) -> str | None:
    email = value.strip().lower() if value else None
    if not email:
        if required:
            raise ValidationError("Email address is required.")
        return None
    if len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
        raise ValidationError("Enter a valid email address.")
    return email


def normalize_phone(value: str, *, required: bool = True) -> str:
    phone = value.strip()
    if not phone and not required:
        return ""
    if not PHONE_PATTERN.fullmatch(phone):
        raise ValidationError("Enter a valid phone number.")
    return phone


def money(value: Decimal | str | int, *, field: str = "Amount") -> Decimal:
    """Return a finite two-decimal money value; floats are deliberately rejected."""

    if isinstance(value, float):
        raise TypeError("Financial values must use Decimal, string, or integer input")
    try:
        result = Decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"{field} is not a valid amount.") from exc
    if not result.is_finite():
        raise ValidationError(f"{field} must be finite.")
    return result


def nonnegative_money(value: Decimal | str | int, *, field: str = "Amount") -> Decimal:
    result = money(value, field=field)
    if result < 0:
        raise ValidationError(f"{field} cannot be negative.")
    return result


def normalize_barcode(value: str | None) -> str | None:
    """Return a tidy barcode, or ``None`` when the product has none.

    Scanners emit surrounding whitespace and sometimes a trailing newline, and an
    empty string would collide with every other blank barcode on the unique index.
    """

    if value is None:
        return None
    cleaned = "".join(value.split())
    if not cleaned:
        return None
    if len(cleaned) > 64:
        raise ValidationError("A barcode may be at most 64 characters.")
    return cleaned
