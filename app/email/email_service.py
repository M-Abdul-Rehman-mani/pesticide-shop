"""Transport-independent email message contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class EmailAttachment:
    filename: str
    content: bytes
    mime_type: str = "application/pdf"


@dataclass(frozen=True, slots=True)
class OutgoingEmail:
    recipient: str
    subject: str
    text_body: str
    html_body: str | None = None
    attachments: tuple[EmailAttachment, ...] = field(default_factory=tuple)


class EmailService(Protocol):
    def send(self, message: OutgoingEmail) -> None:
        """Deliver a message or raise a transport exception."""
