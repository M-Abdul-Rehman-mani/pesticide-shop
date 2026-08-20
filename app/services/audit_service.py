"""Append-only audit event creation."""

from __future__ import annotations

import platform
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit import AuditLog


class AuditService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        *,
        actor_id: uuid.UUID | None,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID | None,
        old_value: dict[str, Any] | None = None,
        new_value: dict[str, Any] | None = None,
        machine: str | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            user_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            old_value=old_value,
            new_value=new_value,
            ip_or_machine=machine or platform.node()[:255] or None,
        )
        self._session.add(entry)
        return entry
