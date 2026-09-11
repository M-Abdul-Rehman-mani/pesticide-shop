"""Reliable post-commit email delivery from the database outbox."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.config.settings import Settings
from app.email.email_service import EmailAttachment, EmailService, OutgoingEmail
from app.models.email_history import EmailHistory
from app.models.enums import EmailStatus, PaymentDirection
from app.models.payment import Payment
from app.models.sale import Sale
from app.printing.receipt_generator import ReceiptGenerator, SaleReceiptData
from app.printing.shop_profile import load_shop_profile
from app.utils.exceptions import ConflictError, NotFoundError

logger = logging.getLogger("app.email.outbox")


@dataclass(frozen=True, slots=True)
class ClaimedEmail:
    id: uuid.UUID
    recipient: str
    subject: str
    body_text: str
    body_html: str | None
    template: str
    entity_type: str
    entity_id: uuid.UUID
    attachment_name: str | None
    attachment_data: bytes | None


class OutboxDeliveryService:
    """Claim one message, send it outside a transaction, then persist its outcome."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        transport: EmailService,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._transport = transport
        self._settings = settings

    def deliver(self, email_id: uuid.UUID) -> None:
        claimed = self._claim(email_id)
        try:
            attachment = self._build_attachment(claimed)
            message = OutgoingEmail(
                recipient=claimed.recipient,
                subject=claimed.subject,
                text_body=claimed.body_text,
                html_body=claimed.body_html,
                attachments=(attachment,) if attachment else (),
            )
            self._transport.send(message)
        except Exception as exc:
            self._mark_failed(claimed.id, exc)
            raise
        self._mark_sent(claimed.id, attachment)

    def _claim(self, email_id: uuid.UUID) -> ClaimedEmail:
        with self._session_factory.begin() as session:
            row = session.execute(
                select(EmailHistory)
                .where(EmailHistory.id == email_id)
                .with_for_update(of=EmailHistory, skip_locked=True)
            ).scalar_one_or_none()
            if row is None:
                raise NotFoundError("Email queue record was not found or is being processed.")
            if row.status is EmailStatus.SENT:
                raise ConflictError("This email has already been sent.")
            stale_before = datetime.now(UTC) - timedelta(minutes=15)
            if (
                row.status is EmailStatus.SENDING
                and row.last_attempt_at is not None
                and row.last_attempt_at > stale_before
            ):
                raise ConflictError("This email is already being sent.")
            row.status = EmailStatus.SENDING
            row.attempts += 1
            row.last_attempt_at = datetime.now(UTC)
            row.last_error = None
            return ClaimedEmail(
                id=row.id,
                recipient=row.recipient,
                subject=row.subject,
                body_text=row.body_text,
                body_html=row.body_html,
                template=row.template,
                entity_type=row.entity_type,
                entity_id=row.entity_id,
                attachment_name=row.attachment_name,
                attachment_data=row.attachment_data,
            )

    def _build_attachment(self, email: ClaimedEmail) -> EmailAttachment | None:
        if email.attachment_name and email.attachment_data:
            return EmailAttachment(email.attachment_name, email.attachment_data)
        if email.entity_type != "Sale":
            return None
        with self._session_factory() as session:
            sale = session.execute(
                select(Sale).options(selectinload(Sale.items)).where(Sale.id == email.entity_id)
            ).scalar_one_or_none()
            if sale is None:
                raise NotFoundError("The sale for this email no longer exists.")
            methods = list(
                session.scalars(
                    select(Payment.method).where(
                        Payment.sale_id == sale.id,
                        Payment.direction == PaymentDirection.INCOMING,
                    )
                ).all()
            )
            profile = load_shop_profile(session, self._settings)
            data = SaleReceiptData.from_sale(
                sale, ", ".join(dict.fromkeys(method.value for method in methods)) or "UNPAID"
            )
            payload = ReceiptGenerator().generate_a4(data, profile)
            return EmailAttachment(f"{sale.invoice_number}.pdf", payload)

    def _mark_sent(self, email_id: uuid.UUID, attachment: EmailAttachment | None) -> None:
        with self._session_factory.begin() as session:
            row = session.get(EmailHistory, email_id)
            if row is None:
                return
            row.status = EmailStatus.SENT
            row.sent_at = datetime.now(UTC)
            if attachment:
                row.attachment_name = attachment.filename
                row.attachment_data = attachment.content

    def _mark_failed(self, email_id: uuid.UUID, error: Exception) -> None:
        logger.exception("Email delivery failed for outbox record %s", email_id)
        with self._session_factory.begin() as session:
            row = session.get(EmailHistory, email_id)
            if row is None:
                return
            row.status = EmailStatus.FAILED
            row.last_error = f"{error.__class__.__name__}: {error}"[:2000]


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """What happened when an entity's queued messages were sent straight away."""

    sent: int = 0
    failed: int = 0
    skipped: bool = False

    @property
    def attempted(self) -> int:
        return self.sent + self.failed


def deliver_pending_for(
    session_factory: sessionmaker[Session],
    settings: Settings,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
) -> DeliveryOutcome:
    """Send one record's queued messages now rather than leaving them for the worker.

    A shop running without the background worker would otherwise watch its invoice
    emails pile up unsent. Delivery still goes through the outbox: the row is written
    inside the sale's own transaction and only claimed here, after that transaction
    has committed. So a refused login or a dropped connection leaves a failed record
    to retry and never touches the sale, and the message can never be sent twice.
    """

    from app.email.configuration import load_smtp_config
    from app.email.smtp_client import SMTPEmailService

    with session_factory() as session:
        config = load_smtp_config(session, settings)
        pending = list(
            session.scalars(
                select(EmailHistory.id)
                .where(
                    EmailHistory.entity_type == entity_type,
                    EmailHistory.entity_id == entity_id,
                    EmailHistory.status.in_({EmailStatus.PENDING, EmailStatus.FAILED}),
                )
                .order_by(EmailHistory.created_at)
            )
        )
    if not pending:
        return DeliveryOutcome()
    if not config.host or not config.from_email:
        # Nothing is configured to send with; the messages stay queued rather than
        # being marked failed for a reason that has nothing to do with them.
        logger.info("SMTP is not configured; %d message(s) left queued", len(pending))
        return DeliveryOutcome(skipped=True)
    delivery = OutboxDeliveryService(session_factory, SMTPEmailService(config), settings)
    sent = failed = 0
    for email_id in pending:
        try:
            delivery.deliver(email_id)
        except Exception:
            # Already logged and recorded against the row by the delivery service.
            failed += 1
        else:
            sent += 1
    return DeliveryOutcome(sent=sent, failed=failed)
