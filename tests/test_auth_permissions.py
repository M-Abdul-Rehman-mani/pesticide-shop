from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models.enums import UserRole
from app.models.user import User
from app.security.authentication import AuthenticatedUser, AuthenticationService
from app.security.permissions import Permission, has_permission, require_permission
from app.utils.exceptions import AuthenticationError, PermissionDeniedError, ValidationError
from app.utils.security import hash_password, verify_password


@pytest.mark.parametrize(
    ("role", "permission", "allowed"),
    [
        (UserRole.OWNER, Permission.MANAGE_USERS, True),
        (UserRole.MANAGER, Permission.MANAGE_OWNER_USERS, False),
        (UserRole.SALESPERSON, Permission.CREATE_SALE, True),
        (UserRole.SALESPERSON, Permission.MANAGE_SETTINGS, False),
        (UserRole.INVENTORY_MANAGER, Permission.RECORD_PURCHASE, True),
        (UserRole.INVENTORY_MANAGER, Permission.CREATE_SALE, False),
    ],
)
def test_role_permissions(role: UserRole, permission: Permission, allowed: bool) -> None:
    assert has_permission(role, permission) is allowed
    if allowed:
        require_permission(role, permission)
    else:
        with pytest.raises(PermissionDeniedError):
            require_permission(role, permission)


def test_password_hash_is_not_plaintext() -> None:
    password_hash = hash_password("StrongPassword123")
    assert password_hash != "StrongPassword123"
    assert verify_password(password_hash, "StrongPassword123")
    assert not verify_password(password_hash, "wrong")


def test_authenticate_and_force_password_change(db_session: Session) -> None:
    user = User(
        username="login-user",
        full_name="Login User",
        email="login@test.invalid",
        password_hash=hash_password("LoginPassword123"),
        role=UserRole.SALESPERSON,
        is_active=True,
        must_change_password=True,
    )
    db_session.add(user)
    db_session.flush()
    service = AuthenticationService(db_session)
    authenticated = service.authenticate("LOGIN-USER", "LoginPassword123")
    assert authenticated.id == user.id
    assert authenticated.must_change_password
    service.change_password(
        actor=authenticated,
        current_password="LoginPassword123",
        new_password="DifferentPassword456",
    )
    assert not user.must_change_password
    assert verify_password(user.password_hash, "DifferentPassword456")


def test_invalid_login_is_generic(db_session: Session) -> None:
    with pytest.raises(AuthenticationError, match="Invalid username or password"):
        AuthenticationService(db_session).authenticate("missing", "wrong")


def test_manager_cannot_create_owner(db_session: Session) -> None:
    manager_model = User(
        username="test-manager",
        full_name="Test Manager",
        email="test-manager@test.invalid",
        password_hash="unused",
        role=UserRole.MANAGER,
        is_active=True,
        must_change_password=False,
    )
    db_session.add(manager_model)
    db_session.flush()
    manager = AuthenticatedUser.from_model(manager_model)
    with pytest.raises(PermissionDeniedError):
        AuthenticationService(db_session).create_user(
            actor=manager,
            username="new-owner",
            full_name="New Owner",
            email="new-owner@test.invalid",
            password="OwnerPassword123",
            role=UserRole.OWNER,
        )


def test_user_administration_password_change_and_lookup(
    db_session: Session, owner: AuthenticatedUser
) -> None:
    service = AuthenticationService(db_session)
    user = service.create_user(
        actor=owner,
        username="  New.Manager  ",
        full_name="New Manager",
        email="NEW.MANAGER@TEST.INVALID",
        password="StrongPass123",
        role=UserRole.MANAGER,
    )
    assert user.username == "new.manager"
    assert service.find_user("NEW.MANAGER") is user
    authenticated = service.authenticate("new.manager", "StrongPass123")
    with pytest.raises(ValidationError, match="different"):
        service.change_password(
            actor=authenticated,
            current_password="StrongPass123",
            new_password="StrongPass123",
        )
    service.change_password(
        actor=authenticated,
        current_password="StrongPass123",
        new_password="EvenStronger456",
    )
    assert not user.must_change_password
    service.set_active(actor=owner, user_id=user.id, is_active=False)
    assert not user.is_active
    with pytest.raises(AuthenticationError, match="disabled"):
        service.authenticate("new.manager", "EvenStronger456")


def test_user_administration_validation(db_session: Session, owner: AuthenticatedUser) -> None:
    service = AuthenticationService(db_session)
    with pytest.raises(ValidationError, match="Username"):
        service.create_user(
            actor=owner,
            username="x",
            full_name="Short Username",
            email="short@test.invalid",
            password="StrongPass123",
            role=UserRole.MANAGER,
        )
    with pytest.raises(ValidationError, match="own"):
        service.set_active(actor=owner, user_id=owner.id, is_active=False)
