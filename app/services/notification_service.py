"""Transactional email outbox creation; SMTP delivery happens after commit."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.email_history import EmailHistory
from app.models.enums import EmailStatus
from app.utils.validators import normalize_email


@dataclass(frozen=True, slots=True)
class QueuedMessage:
    recipient: str
    subject: str
    body_text: str
    body_html: str | None = None


class NotificationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def queue(
        self,
        *,
        message: QueuedMessage,
        template: str,
        entity_type: str,
        entity_id: uuid.UUID,
    ) -> EmailHistory:
        recipient = normalize_email(message.recipient, required=True)
        assert recipient is not None
        history = EmailHistory(
            recipient=recipient,
            subject=message.subject,
            template=template,
            entity_type=entity_type,
            entity_id=entity_id,
            status=EmailStatus.PENDING,
            attempts=0,
            body_text=message.body_text,
            body_html=message.body_html,
        )
        self._session.add(history)
        return history
