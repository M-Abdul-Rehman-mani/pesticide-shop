"""Central role-to-permission policy."""

from __future__ import annotations

from enum import StrEnum

from app.models.enums import UserRole
from app.utils.exceptions import PermissionDeniedError


class Permission(StrEnum):
    VIEW_DASHBOARD = "VIEW_DASHBOARD"
    CREATE_SALE = "CREATE_SALE"
    VOID_SALE = "VOID_SALE"
    RECORD_RETURN = "RECORD_RETURN"
    VIEW_INVENTORY = "VIEW_INVENTORY"
    MANAGE_INVENTORY = "MANAGE_INVENTORY"
    RECORD_PURCHASE = "RECORD_PURCHASE"
    MANAGE_CUSTOMERS = "MANAGE_CUSTOMERS"
    MANAGE_DEALERS = "MANAGE_DEALERS"
    MANAGE_SUPPLIERS = "MANAGE_SUPPLIERS"
    VIEW_REPORTS = "VIEW_REPORTS"
    VIEW_PROFIT = "VIEW_PROFIT"
    MANAGE_USERS = "MANAGE_USERS"
    MANAGE_OWNER_USERS = "MANAGE_OWNER_USERS"
    MANAGE_SETTINGS = "MANAGE_SETTINGS"
    RESTORE_DATABASE = "RESTORE_DATABASE"
    VIEW_AUDIT_LOG = "VIEW_AUDIT_LOG"
    RETRY_EMAIL = "RETRY_EMAIL"


_ALL_PERMISSIONS = frozenset(Permission)
ROLE_PERMISSIONS: dict[UserRole, frozenset[Permission]] = {
    UserRole.OWNER: _ALL_PERMISSIONS,
    UserRole.MANAGER: _ALL_PERMISSIONS
    - {Permission.MANAGE_OWNER_USERS, Permission.RESTORE_DATABASE},
    UserRole.SALESPERSON: frozenset(
        {
            Permission.VIEW_DASHBOARD,
            Permission.CREATE_SALE,
            Permission.VIEW_INVENTORY,
            Permission.MANAGE_CUSTOMERS,
            Permission.MANAGE_DEALERS,
        }
    ),
    UserRole.INVENTORY_MANAGER: frozenset(
        {
            Permission.VIEW_DASHBOARD,
            Permission.VIEW_INVENTORY,
            Permission.MANAGE_INVENTORY,
            Permission.RECORD_PURCHASE,
            Permission.MANAGE_SUPPLIERS,
            Permission.VIEW_REPORTS,
        }
    ),
}


def has_permission(role: UserRole, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]


def require_permission(role: UserRole, permission: Permission) -> None:
    if not has_permission(role, permission):
        raise PermissionDeniedError()
