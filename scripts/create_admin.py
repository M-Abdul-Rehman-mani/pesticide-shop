"""Interactively bootstrap an owner account without embedding a password."""

from __future__ import annotations

import argparse
import getpass

from sqlalchemy import func, select

from app.database.session import SessionFactory
from app.models.enums import UserRole
from app.models.user import User
from app.services.audit_service import AuditService
from app.utils.security import hash_password
from app.utils.validators import normalize_email


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a Mobile Shop owner account")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--full-name", required=True)
    parser.add_argument("--email", required=True)
    arguments = parser.parse_args()
    first = getpass.getpass("New password: ")
    second = getpass.getpass("Confirm password: ")
    if first != second:
        raise SystemExit("Passwords do not match.")
    email = normalize_email(arguments.email, required=True)
    assert email is not None
    with SessionFactory.begin() as session:
        duplicate = session.scalar(
            select(func.count())
            .select_from(User)
            .where(
                (func.lower(User.username) == arguments.username.strip().lower())
                | (func.lower(User.email) == email)
            )
        )
        if duplicate:
            raise SystemExit("Username or email already exists.")
        user = User(
            username=arguments.username.strip().lower(),
            full_name=arguments.full_name.strip(),
            email=email,
            password_hash=hash_password(first),
            role=UserRole.OWNER,
            is_active=True,
            must_change_password=False,
        )
        session.add(user)
        session.flush()
        AuditService(session).record(
            actor_id=user.id,
            action="OWNER_BOOTSTRAPPED",
            entity_type="User",
            entity_id=user.id,
            new_value={"username": user.username, "email": user.email},
        )
    print(f"Owner {arguments.username!r} created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
