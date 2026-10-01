"""Moving a dealer's unused account credit onto their invoices and back.

A dealer who pays more than they owe keeps the excess on account as credit. That
money was already counted when it was received, so applying it to an invoice is
recorded as an ``applied_credit`` payment entry: it settles the invoice without
being counted again as cash, and without changing the dealer's balance (which
already went down when the money came in).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import ColumnElement, case, func, select
from sqlalchemy.orm import Session

from app.models.enums import PaymentDirection, PaymentMethod
from app.models.payment import Payment
from app.models.sale import Sale
from app.security.authentication import AuthenticatedUser
from app.services.money_service import payment_status

ZERO = Decimal("0.00")
APPLIED_REFERENCE = "Account credit"


def _signed() -> ColumnElement[Decimal]:
    return case(
        (Payment.direction == PaymentDirection.INCOMING, Payment.amount),
        else_=-Payment.amount,
    )


def available_credit(session: Session, dealer_id: uuid.UUID) -> Decimal:
    """Credit held on the dealer's account that no invoice has used yet."""

    received = session.scalar(
        select(func.coalesce(func.sum(_signed()), ZERO)).where(
            Payment.dealer_id == dealer_id,
            Payment.sale_id.is_(None),
            Payment.purchase_id.is_(None),
            Payment.sale_return_id.is_(None),
            Payment.applied_credit.is_(False),
        )
    )
    applied = session.scalar(
        select(func.coalesce(func.sum(_signed()), ZERO)).where(
            Payment.dealer_id == dealer_id, Payment.applied_credit.is_(True)
        )
    )
    return max(Decimal(received or 0) - Decimal(applied or 0), ZERO)


def apply_credit(
    session: Session, sale: Sale, dealer_id: uuid.UUID, actor: AuthenticatedUser
) -> Decimal:
    """Settle as much of the invoice as the dealer's unused credit covers."""

    applied = min(available_credit(session, dealer_id), sale.remaining_amount)
    if applied <= 0:
        return ZERO
    session.add(
        Payment(
            sale_id=sale.id,
            dealer_id=dealer_id,
            method=PaymentMethod.OTHER,
            direction=PaymentDirection.INCOMING,
            amount=applied,
            applied_credit=True,
            reference=APPLIED_REFERENCE,
            notes=f"Advance credit applied to {sale.invoice_number}",
            received_by=actor.id,
        )
    )
    _settle(sale, applied)
    return applied


def release_credit(session: Session, sale: Sale, actor: AuthenticatedUser) -> Decimal:
    """Put account credit used by this invoice back on the dealer's account.

    Called before an invoice is voided or edited, so credit that paid it is not
    mistaken for a cash payment that must be reversed by hand first.
    """

    if sale.dealer_id is None:
        return ZERO
    used = Decimal(
        session.scalar(
            select(func.coalesce(func.sum(_signed()), ZERO)).where(
                Payment.sale_id == sale.id, Payment.applied_credit.is_(True)
            )
        )
        or 0
    )
    if used <= 0:
        return ZERO
    session.add(
        Payment(
            sale_id=sale.id,
            dealer_id=sale.dealer_id,
            method=PaymentMethod.OTHER,
            direction=PaymentDirection.OUTGOING,
            amount=used,
            applied_credit=True,
            reference=APPLIED_REFERENCE,
            notes=f"Advance credit released from {sale.invoice_number}",
            received_by=actor.id,
        )
    )
    _settle(sale, -used)
    return used


def _settle(sale: Sale, amount: Decimal) -> None:
    sale.paid_amount += amount
    sale.remaining_amount -= amount
    sale.payment_status = payment_status(sale.total, sale.paid_amount)
