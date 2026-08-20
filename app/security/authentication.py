"""User authentication, password changes, and account administration."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import UserRole
from app.models.user import User
from app.security.permissions import Permission, require_permission
from app.services.audit_service import AuditService
from app.utils.exceptions import AuthenticationError, ConflictError, NotFoundError, ValidationError
from app.utils.security import hash_password, password_needs_rehash, verify_password
from app.utils.validators import normalize_email


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    id: uuid.UUID
    username: str
    full_name: str
    email: str
    role: UserRole
    must_change_password: bool

    @classmethod
    def from_model(cls, user: User) -> AuthenticatedUser:
        return cls(
            id=user.id,
            username=user.username,
            full_name=user.full_name,
            email=user.email,
            role=user.role,
            must_change_password=user.must_change_password,
        )


class AuthenticationService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._audit = AuditService(session)

    def authenticate(self, username: str, password: str) -> AuthenticatedUser:
        normalized = username.strip().lower()
        user = self._session.execute(
            select(User).where(func.lower(User.username) == normalized)
        ).scalar_one_or_none()
        if not verify_password(user.password_hash if user else None, password):
            self._audit.record(
                actor_id=user.id if user else None,
                action="LOGIN_FAILED",
                entity_type="User",
                entity_id=user.id if user else None,
                new_value={"username": normalized},
            )
            raise AuthenticationError()
        if user is None:
            # Verification cannot succeed without a stored hash. This explicit guard
            # makes the security invariant visible to static analysis as well.
            raise AuthenticationError()
        if not user.is_active:
            raise AuthenticationError("This user account is disabled.")
        if password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(password, allow_weak_demo_password=True)
        user.last_login_at = datetime.now(UTC)
        self._audit.record(
            actor_id=user.id,
            action="LOGIN_SUCCEEDED",
            entity_type="User",
            entity_id=user.id,
        )
        return AuthenticatedUser.from_model(user)

    def create_user(
        self,
        *,
        actor: AuthenticatedUser,
        username: str,
        full_name: str,
        email: str,
        password: str,
        role: UserRole,
        must_change_password: bool = True,
        allow_weak_demo_password: bool = False,
    ) -> User:
        require_permission(actor.role, Permission.MANAGE_USERS)
        if role is UserRole.OWNER:
            require_permission(actor.role, Permission.MANAGE_OWNER_USERS)
        normalized_username = username.strip().lower()
        if len(normalized_username) < 3:
            raise ValidationError("Username must contain at least 3 characters.")
        if not full_name.strip():
            raise ValidationError("Full name is required.")
        normalized_email = normalize_email(email, required=True)
        assert normalized_email is not None
        user = User(
            username=normalized_username,
            full_name=full_name.strip(),
            email=normalized_email,
            password_hash=hash_password(
                password, allow_weak_demo_password=allow_weak_demo_password
            ),
            role=role,
            is_active=True,
            must_change_password=must_change_password,
        )
        self._session.add(user)
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise ConflictError("That username or email address is already in use.") from exc
        self._audit.record(
            actor_id=actor.id,
            action="USER_CREATED",
            entity_type="User",
            entity_id=user.id,
            new_value={"username": user.username, "email": user.email, "role": user.role.value},
        )
        return user

    def change_password(
        self,
        *,
        actor: AuthenticatedUser,
        current_password: str,
        new_password: str,
    ) -> None:
        user = self._session.get(User, actor.id)
        if user is None or not verify_password(user.password_hash, current_password):
            raise AuthenticationError("Current password is incorrect.")
        if verify_password(user.password_hash, new_password):
            raise ValidationError("The new password must be different from the current password.")
        user.password_hash = hash_password(new_password)
        user.must_change_password = False
        self._audit.record(
            actor_id=actor.id,
            action="PASSWORD_CHANGED",
            entity_type="User",
            entity_id=user.id,
        )

    def set_active(self, *, actor: AuthenticatedUser, user_id: uuid.UUID, is_active: bool) -> User:
        require_permission(actor.role, Permission.MANAGE_USERS)
        target = self._session.get(User, user_id)
        if target is None:
            raise NotFoundError("User was not found.")
        if target.role is UserRole.OWNER:
            require_permission(actor.role, Permission.MANAGE_OWNER_USERS)
        if target.id == actor.id and not is_active:
            raise ValidationError("You cannot disable your own account.")
        if target.role is UserRole.OWNER and not is_active:
            active_owners = self._session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.role == UserRole.OWNER, User.is_active.is_(True))
            )
            if active_owners == 1:
                raise ValidationError("The final active owner account cannot be disabled.")
        old_value = target.is_active
        target.is_active = is_active
        self._audit.record(
            actor_id=actor.id,
            action="USER_ENABLED" if is_active else "USER_DISABLED",
            entity_type="User",
            entity_id=target.id,
            old_value={"is_active": old_value},
            new_value={"is_active": is_active},
        )
        return target

    def find_user(self, value: str) -> User | None:
        normalized = value.strip().lower()
        return self._session.execute(
            select(User).where(
                or_(func.lower(User.username) == normalized, func.lower(User.email) == normalized)
            )
        ).scalar_one_or_none()
