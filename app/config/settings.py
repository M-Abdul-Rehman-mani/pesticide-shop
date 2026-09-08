"""Validated environment configuration with no embedded credentials."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic.fields import Field
from pydantic.functional_validators import field_validator, model_validator
from pydantic.types import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.utils.paths import environment_file_candidates, resolve_writable


class Settings(BaseSettings):
    """Environment-backed runtime settings.

    Secrets use ``SecretStr`` so accidental logging or repr calls redact them.
    """

    model_config = SettingsConfigDict(
        env_file=environment_file_candidates(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    app_debug: bool = False
    app_secret_key: SecretStr = Field(min_length=32)
    app_session_timeout_minutes: int = Field(default=30, ge=5, le=1440)
    app_timezone: str = "UTC"
    app_currency: str = Field(default="PKR", min_length=3, max_length=3)

    database_host: str = "127.0.0.1"
    database_port: int = Field(default=5440, ge=1, le=65535)
    database_name: str = "pesticide_shop"
    database_user: str = "pesticide_shop"
    database_password: SecretStr
    database_ssl_mode: Literal[
        "disable", "allow", "prefer", "require", "verify-ca", "verify-full"
    ] = "prefer"
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=20, ge=0, le=200)

    test_database_host: str = "127.0.0.1"
    test_database_port: int = Field(default=5441, ge=1, le=65535)
    test_database_name: str = "pesticide_shop_test"
    test_database_user: str = "pesticide_shop_test"
    test_database_password: SecretStr | None = None

    redis_host: str = "127.0.0.1"
    redis_port: int = Field(default=6380, ge=1, le=65535)
    redis_db: int = Field(default=0, ge=0)
    redis_password: SecretStr | None = None

    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from_email: str | None = None
    smtp_use_tls: bool = True
    smtp_timeout_seconds: int = Field(default=30, ge=1, le=120)
    owner_email: str | None = None

    daily_report_time: str = "21:00"
    backup_directory: Path = Path("backups")
    backup_retention_days: int = Field(default=30, ge=1, le=3650)
    backup_compress: bool = True
    postgres_tools_directory: Path | None = None
    log_directory: Path = Path("logs")

    @field_validator("backup_directory", "log_directory")
    @classmethod
    def writable_runtime_directory(cls, value: Path) -> Path:
        """Anchor relative runtime directories so a frozen Windows build stays writable."""

        return resolve_writable(value)

    @field_validator("app_currency")
    @classmethod
    def uppercase_currency(cls, value: str) -> str:
        return value.upper()

    @field_validator("postgres_tools_directory", mode="before")
    @classmethod
    def empty_tools_directory_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("app_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown IANA timezone: {value}") from exc
        return value

    @field_validator("daily_report_time")
    @classmethod
    def valid_report_time(cls, value: str) -> str:
        parts = value.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError("daily_report_time must use HH:MM format")
        hour, minute = (int(part) for part in parts)
        if hour not in range(24) or minute not in range(60):
            raise ValueError("daily_report_time must be a valid 24-hour time")
        return f"{hour:02d}:{minute:02d}"

    @model_validator(mode="after")
    def reject_demo_secret_in_production(self) -> Settings:
        secret = self.app_secret_key.get_secret_value().lower()
        if self.app_env == "production" and ("replace" in secret or "change" in secret):
            raise ValueError("APP_SECRET_KEY must be replaced in production")
        return self

    @property
    def database_url(self) -> str:
        password = quote_plus(self.database_password.get_secret_value())
        user = quote_plus(self.database_user)
        return (
            f"postgresql+psycopg://{user}:{password}@{self.database_host}:"
            f"{self.database_port}/{self.database_name}?sslmode={self.database_ssl_mode}"
        )

    @property
    def test_database_url(self) -> str:
        password_secret = self.test_database_password or self.database_password
        password = quote_plus(password_secret.get_secret_value())
        user = quote_plus(self.test_database_user)
        return (
            f"postgresql+psycopg://{user}:{password}@{self.test_database_host}:"
            f"{self.test_database_port}/{self.test_database_name}?sslmode={self.database_ssl_mode}"
        )

    @property
    def redis_url(self) -> str:
        password = ""
        if self.redis_password and self.redis_password.get_secret_value():
            password = f":{quote_plus(self.redis_password.get_secret_value())}@"
        return f"redis://{password}{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one validated settings object per process."""

    return Settings()
