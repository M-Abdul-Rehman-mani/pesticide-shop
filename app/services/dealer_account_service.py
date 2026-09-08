"""Dealer running accounts: staged payments and the resulting statement."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.dealer import Dealer
from app.models.enums import PaymentDirection, PaymentMethod, SaleStatus
from app.models.payment import Payment
from app.models.sale import Sale
from app.security.authentication import AuthenticatedUser
from app.security.permissions import Permission, require_permission
from app.services.audit_service import AuditService
from app.services.money_service import payment_status
from app.utils.exceptions import NotFoundError, ValidationError
from app.utils.validators import nonnegative_money

ZERO = Decimal("0.00")
INVOICE = "INVOICE"
PAYMENT = "PAYMENT"


@dataclass(frozen=True, slots=True)
class SettledInvoice:
    """One invoice a payment was applied to, and what it cleared."""

    invoice_number: str
    applied: Decimal
    remaining: Decimal


@dataclass(frozen=True, slots=True)
class DealerPaymentResult:
    """The outcome of one payment received from a dealer."""

    amount: Decimal
    settled: tuple[SettledInvoice, ...]
    credited: Decimal
    balance: Decimal


@dataclass(frozen=True, slots=True)
class StatementEntry:
    """One line of a dealer statement, with the balance after it."""

    occurred_at: datetime
    kind: str
    reference: str
    detail: str
    charge: Decimal
    credit: Decimal
    balance: Decimal


@dataclass(frozen=True, slots=True)
class DealerStatement:
    """A dealer's full charge-and-payment history with its totals."""

    dealer_name: str
    entries: tuple[StatementEntry, ...]
    invoiced: Decimal
    paid: Decimal
    outstanding: Decimal
    credit_limit: Decimal

    @property
    def available_credit(self) -> Decimal | None:
        """Remaining credit, or ``None`` when the dealer has no limit set."""

        if self.credit_limit <= 0:
            return None
        return self.credit_limit - self.outstanding


class DealerAccountService:
    """Record staged dealer payments and report the running account."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._audit = AuditService(session)

    def record_payment(
        self,
        dealer_id: uuid.UUID,
        *,
        actor: AuthenticatedUser,
        method: PaymentMethod,
        amount: Decimal,
        reference: str | None = None,
        notes: str | None = None,
    ) -> DealerPaymentResult:
        """Take a payment against the dealer's account, oldest invoice first.

        Dealers settle their account in instalments rather than invoice by invoice,
        so one payment is spread over their unpaid invoices in date order. Anything
        left over stays on the account as credit and reduces the next invoice's
        balance, which is why the dealer balance may go negative.
        """

        require_permission(actor.role, Permission.MANAGE_DEALERS)
        dealer = self._session.execute(
            select(Dealer).where(Dealer.id == dealer_id).with_for_update(of=Dealer)
        ).scalar_one_or_none()
        if dealer is None:
            raise NotFoundError("Dealer was not found.")
        amount = nonnegative_money(amount, field="Payment")
        if amount <= 0:
            raise ValidationError("Payment must be greater than zero.")
        cleaned_reference = reference.strip() if reference and reference.strip() else None
        cleaned_notes = notes.strip() if notes and notes.strip() else None

        outstanding_sales = list(
            self._session.scalars(
                select(Sale)
                .where(
                    Sale.dealer_id == dealer_id,
                    Sale.status == SaleStatus.COMPLETED,
                    Sale.remaining_amount > 0,
                )
                .order_by(Sale.sale_date, Sale.invoice_number)
                .with_for_update(of=Sale)
            )
        )
        unapplied = amount
        settled: list[SettledInvoice] = []
        for sale in outstanding_sales:
            if unapplied <= 0:
                break
            applied = min(unapplied, sale.remaining_amount)
            sale.paid_amount += applied
            sale.remaining_amount -= applied
            sale.payment_status = payment_status(sale.total, sale.paid_amount)
            self._session.add(
                Payment(
                    sale_id=sale.id,
                    dealer_id=dealer.id,
                    method=method,
                    direction=PaymentDirection.INCOMING,
                    amount=applied,
                    reference=cleaned_reference,
                    notes=cleaned_notes,
                    received_by=actor.id,
                )
            )
            unapplied -= applied
            settled.append(SettledInvoice(sale.invoice_number, applied, sale.remaining_amount))
        if unapplied > 0:
            self._session.add(
                Payment(
                    dealer_id=dealer.id,
                    method=method,
                    direction=PaymentDirection.INCOMING,
                    amount=unapplied,
                    reference=cleaned_reference,
                    notes=cleaned_notes,
                    received_by=actor.id,
                )
            )
        dealer.balance -= amount
        self._audit.record(
            actor_id=actor.id,
            action="DEALER_PAYMENT_RECORDED",
            entity_type="Dealer",
            entity_id=dealer.id,
            old_value={"balance": str(dealer.balance + amount)},
            new_value={
                "amount": str(amount),
                "method": method.value,
                "reference": cleaned_reference,
                "invoices_settled": [entry.invoice_number for entry in settled],
                "account_credit": str(unapplied),
                "balance": str(dealer.balance),
            },
        )
        self._session.flush()
        return DealerPaymentResult(
            amount=amount,
            settled=tuple(settled),
            credited=unapplied,
            balance=dealer.balance,
        )

    def statement(self, dealer_id: uuid.UUID) -> DealerStatement:
        """Return every charge and payment for a dealer with a running balance."""

        dealer = self._session.get(Dealer, dealer_id)
        if dealer is None:
            raise NotFoundError("Dealer was not found.")
        sales = list(
            self._session.scalars(
                select(Sale)
                .where(Sale.dealer_id == dealer_id, Sale.status == SaleStatus.COMPLETED)
                .order_by(Sale.sale_date)
            )
        )
        payments = list(
            self._session.scalars(
                select(Payment)
                .where(
                    Payment.dealer_id == dealer_id,
                    Payment.direction == PaymentDirection.INCOMING,
                )
                .order_by(Payment.created_at)
            )
        )
        events: list[tuple[datetime, str, str, str, Decimal, Decimal]] = [
            (sale.sale_date, INVOICE, sale.invoice_number, "Invoice raised", sale.total, ZERO)
            for sale in sales
        ]
        events.extend(
            (
                payment.created_at,
                PAYMENT,
                payment.reference or "—",
                self._payment_detail(payment),
                ZERO,
                payment.amount,
            )
            for payment in payments
        )
        events.sort(key=lambda event: (event[0], event[1]))
        entries: list[StatementEntry] = []
        balance = ZERO
        for occurred_at, kind, reference, detail, charge, credit in events:
            balance += charge - credit
            entries.append(
                StatementEntry(occurred_at, kind, reference, detail, charge, credit, balance)
            )
        invoiced = sum((sale.total for sale in sales), ZERO)
        paid = sum((payment.amount for payment in payments), ZERO)
        return DealerStatement(
            dealer_name=dealer.display_name,
            entries=tuple(entries),
            invoiced=invoiced,
            paid=paid,
            outstanding=invoiced - paid,
            credit_limit=dealer.credit_limit,
        )

    @staticmethod
    def _payment_detail(payment: Payment) -> str:
        method = payment.method.value.replace("_", " ").title()
        if payment.is_account_credit:
            return f"{method} to account credit"
        sale = payment.sale
        return f"{method} to {sale.invoice_number}" if sale else method
