"""Re-check the signed-in account at the start of every database transaction.

The main window holds the user as signed in, so without this an account an owner
has just disabled or demoted would keep its old powers until it logged out.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

from app.models.user import User
from app.security.authentication import AuthenticatedUser
from app.utils.exceptions import AuthenticationError

SESSION_CHANGED_MESSAGE = "Your account was disabled or its role changed. Please sign in again."


def guard_sessions(
    session_factory: sessionmaker[Session], actor: AuthenticatedUser
) -> Callable[[], None]:
    """Refuse every new transaction once ``actor`` is disabled or changes role.

    Returns a function that removes the guard, called when the user signs out.
    """

    def check(_session: Session, transaction: SessionTransaction, connection: Any) -> None:
        if transaction.parent is not None:
            return
        row = connection.execute(
            select(User.is_active, User.role).where(User.id == actor.id)
        ).one_or_none()
        if row is None or not row.is_active or row.role is not actor.role:
            raise AuthenticationError(SESSION_CHANGED_MESSAGE)

    event.listen(session_factory, "after_begin", check)

    def remove() -> None:
        if event.contains(session_factory, "after_begin", check):
            event.remove(session_factory, "after_begin", check)

    return remove
