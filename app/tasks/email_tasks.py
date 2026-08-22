"""Celery tasks for reliable email outbox delivery."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.config.settings import get_settings
from app.database.session import SessionFactory
from app.email.configuration import load_smtp_config
from app.email.outbox import OutboxDeliveryService
from app.email.smtp_client import SMTPEmailService
from app.models.email_history import EmailHistory
from app.models.enums import EmailStatus
from app.tasks.celery_app import celery_app


@celery_app.task(
    bind=True,
    name="app.tasks.email_tasks.send_email",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=1800,
    retry_jitter=True,
    max_retries=5,
)
def send_email(self: object, email_id: str) -> None:
    settings = get_settings()
    with SessionFactory() as session:
        smtp_config = load_smtp_config(session, settings)
    delivery = OutboxDeliveryService(SessionFactory, SMTPEmailService(smtp_config), settings)
    delivery.deliver(uuid.UUID(email_id))


@celery_app.task(name="app.tasks.email_tasks.dispatch_pending_emails")
def dispatch_pending_emails() -> int:
    with SessionFactory() as session:
        ids = list(
            session.scalars(
                select(EmailHistory.id)
                .where(
                    EmailHistory.status.in_({EmailStatus.PENDING, EmailStatus.FAILED}),
                    EmailHistory.attempts < 6,
                )
                .order_by(EmailHistory.created_at)
                .limit(100)
            ).all()
        )
    for email_id in ids:
        send_email.delay(str(email_id))
    return len(ids)
