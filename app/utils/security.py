"""Password hashing and secret encryption primitives."""

from __future__ import annotations

import base64
import hashlib

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from app.utils.exceptions import ValidationError

_PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65_536, parallelism=4)
_DUMMY_HASH = _PASSWORD_HASHER.hash("invalid-password-used-only-for-timing-equalization")


def validate_password_strength(password: str) -> None:
    if len(password) < 10:
        raise ValidationError("Password must contain at least 10 characters.")
    if not any(character.isupper() for character in password):
        raise ValidationError("Password must include an uppercase letter.")
    if not any(character.islower() for character in password):
        raise ValidationError("Password must include a lowercase letter.")
    if not any(character.isdigit() for character in password):
        raise ValidationError("Password must include a number.")


def hash_password(password: str, *, allow_weak_demo_password: bool = False) -> str:
    if not allow_weak_demo_password:
        validate_password_strength(password)
    return _PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Verify a password while doing equivalent work for unknown users."""

    candidate_hash = password_hash or _DUMMY_HASH
    try:
        return _PASSWORD_HASHER.verify(candidate_hash, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _PASSWORD_HASHER.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


class SecretCipher:
    """Encrypt database-held settings using the environment-only application key."""

    def __init__(self, application_secret: str) -> None:
        digest = hashlib.sha256(application_secret.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValidationError(
                "The saved secret cannot be decrypted with this application key."
            ) from exc
