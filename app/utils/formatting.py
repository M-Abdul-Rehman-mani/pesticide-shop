"""Display-only formatting kept separate from financial calculations."""

from datetime import date, datetime
from decimal import Decimal


def format_money(value: Decimal, currency: str) -> str:
    return f"{currency} {value:,.2f}"


def format_date(value: date | datetime | None) -> str:
    return value.strftime("%d-%b-%Y") if value else "—"
