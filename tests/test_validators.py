from decimal import Decimal

import pytest

from app.utils.exceptions import ValidationError
from app.utils.validators import money, normalize_email, normalize_phone, validate_imei


def test_imei_validation() -> None:
    assert validate_imei(" 350000000000501 ") == "350000000000501"
    for invalid in ("", "123", "35000000000050A", "3500000000005010"):
        with pytest.raises(ValidationError):
            validate_imei(invalid)


def test_money_rejects_floats_and_rounds_decimal_input() -> None:
    assert money(Decimal("1.235")) == Decimal("1.24")
    with pytest.raises(TypeError):
        money(1.2)  # type: ignore[arg-type]


def test_email_and_phone_validation() -> None:
    assert normalize_email(" PERSON@EXAMPLE.COM ") == "person@example.com"
    assert normalize_phone("+92 300 1234567") == "+92 300 1234567"
    with pytest.raises(ValidationError):
        normalize_email("not-an-email")
    with pytest.raises(ValidationError):
        normalize_phone("abc")
