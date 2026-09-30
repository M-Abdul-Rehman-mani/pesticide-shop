"""Add the TEST customer, dealer, supplier, and product to an existing install.

``create_admin`` already does this for a new install. Safe to run again: records
that exist are reused, never duplicated.
"""

from __future__ import annotations

from sqlalchemy import select

from app.database.session import SessionFactory
from app.models.enums import UserRole
from app.models.user import User
from app.security.authentication import AuthenticatedUser
from app.services.practice_records import ensure_practice_records


def main() -> int:
    with SessionFactory.begin() as session:
        owner = session.scalar(
            select(User)
            .where(User.role == UserRole.OWNER, User.is_active.is_(True))
            .order_by(User.created_at)
            .limit(1)
        )
        if owner is None:
            raise SystemExit("No active owner yet: run python -m scripts.create_admin first.")
        practice = ensure_practice_records(session, AuthenticatedUser.from_model(owner))
    print(practice.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
