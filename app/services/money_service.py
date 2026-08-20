"""Shared exact-money calculations."""

from __future__ import annotations

from decimal import Decimal

from app.models.enums import PaymentStatus
from app.utils.exceptions import ValidationError
from app.utils.validators import nonnegative_money


def payment_status(total: Decimal, paid: Decimal) -> PaymentStatus:
    if paid < 0:
        raise ValidationError("Paid amount cannot be negative.")
    if paid > total:
        raise ValidationError("Paid amount cannot exceed the document total.")
    if paid == Decimal("0.00"):
        return PaymentStatus.UNPAID
    if paid == total:
        return PaymentStatus.PAID
    return PaymentStatus.PARTIALLY_PAID


def document_totals(
    subtotal: Decimal, discount: Decimal, tax: Decimal
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    subtotal = nonnegative_money(subtotal, field="Subtotal")
    discount = nonnegative_money(discount, field="Discount")
    tax = nonnegative_money(tax, field="Tax")
    if discount > subtotal:
        raise ValidationError("Discount cannot exceed subtotal.")
    total = nonnegative_money(subtotal - discount + tax, field="Total")
    return subtotal, discount, tax, total
