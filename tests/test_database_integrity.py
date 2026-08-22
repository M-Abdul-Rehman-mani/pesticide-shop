import pytest
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.security.authentication import AuthenticatedUser


def test_audit_log_is_database_immutable(db_session: Session, owner: AuthenticatedUser) -> None:
    entry = AuditLog(
        user_id=owner.id,
        action="TEST_EVENT",
        entity_type="Test",
        entity_id=None,
        new_value={"safe": True},
    )
    db_session.add(entry)
    db_session.flush()
    with pytest.raises(DBAPIError):
        db_session.execute(
            update(AuditLog).where(AuditLog.id == entry.id).values(action="TAMPERED")
        )
