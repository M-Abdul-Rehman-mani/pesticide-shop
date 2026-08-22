"""PostgreSQL connectivity and migration checks used during startup."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError


@dataclass(frozen=True, slots=True)
class DatabaseHealth:
    available: bool
    server_version: str | None = None
    migration_revision: str | None = None
    error: str | None = None


def check_database(engine: Engine) -> DatabaseHealth:
    """Check connectivity without leaking connection details or credentials."""

    try:
        with engine.connect() as connection:
            server_version = str(connection.execute(text("SHOW server_version")).scalar_one())
            revision = None
            if inspect(connection).has_table("alembic_version"):
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version LIMIT 1")
                ).scalar_one_or_none()
        return DatabaseHealth(True, server_version, revision)
    except SQLAlchemyError as exc:
        return DatabaseHealth(False, error=exc.__class__.__name__)
