"""Display-only formatting kept separate from financial calculations."""

from datetime import date, datetime
from decimal import Decimal


def format_amount(value: Decimal) -> str:
    """Render money as whole units.

    The shop prices, sells, and settles in whole rupees, so fractions are noise on
    screen and on paper. Values are still stored and calculated as exact Decimals;
    only the display is rounded.
    """

    return f"{value:,.0f}"


def format_money(value: Decimal, currency: str) -> str:
    return f"{currency} {format_amount(value)}"


def format_date(value: date | datetime | None) -> str:
    return value.strftime("%d-%b-%Y") if value else "—"
