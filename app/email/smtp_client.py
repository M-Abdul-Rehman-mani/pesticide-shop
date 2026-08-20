"""Configurable SMTP implementation with no hardcoded credentials."""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

from app.email.email_service import OutgoingEmail
from app.utils.exceptions import InfrastructureError

logger = logging.getLogger("app.email.smtp")


@dataclass(frozen=True, slots=True)
class SMTPConfig:
    host: str
    port: int
    from_email: str
    username: str | None = None
    password: str | None = None
    use_tls: bool = True
    timeout_seconds: int = 30


class SMTPEmailService:
    def __init__(self, config: SMTPConfig) -> None:
        self._config = config

    def send(self, message: OutgoingEmail) -> None:
        if not self._config.host or not self._config.from_email:
            raise InfrastructureError("SMTP is not configured.")
        email = EmailMessage()
        email["From"] = self._config.from_email
        email["To"] = message.recipient
        email["Subject"] = message.subject
        email.set_content(message.text_body)
        if message.html_body:
            email.add_alternative(message.html_body, subtype="html")
        for attachment in message.attachments:
            major, minor = attachment.mime_type.split("/", maxsplit=1)
            email.add_attachment(
                attachment.content,
                maintype=major,
                subtype=minor,
                filename=attachment.filename,
            )
        context = ssl.create_default_context()
        logger.info("Sending email to %s with subject %s", message.recipient, message.subject)
        with smtplib.SMTP(
            self._config.host,
            self._config.port,
            timeout=self._config.timeout_seconds,
        ) as client:
            client.ehlo()
            if self._config.use_tls:
                client.starttls(context=context)
                client.ehlo()
            if self._config.username:
                client.login(self._config.username, self._config.password or "")
            client.send_message(email)
        logger.info("Email sent to %s", message.recipient)
