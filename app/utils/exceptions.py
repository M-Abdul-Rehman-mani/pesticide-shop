"""User-safe application exceptions."""

from __future__ import annotations


class ApplicationError(Exception):
    """Base error whose message is safe to show in the desktop UI."""

    def __init__(self, message: str, *, code: str = "APPLICATION_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class ValidationError(ApplicationError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="VALIDATION_ERROR")


class NotFoundError(ApplicationError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="NOT_FOUND")


class ConflictError(ApplicationError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="CONFLICT")


class PermissionDeniedError(ApplicationError):
    def __init__(self, message: str = "You do not have permission to perform this action.") -> None:
        super().__init__(message, code="PERMISSION_DENIED")


class AuthenticationError(ApplicationError):
    def __init__(self, message: str = "Invalid username or password.") -> None:
        super().__init__(message, code="AUTHENTICATION_FAILED")


class InfrastructureError(ApplicationError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="INFRASTRUCTURE_ERROR")
