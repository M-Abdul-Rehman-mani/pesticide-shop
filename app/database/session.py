"""Database engine and transaction lifecycle management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from weakref import WeakKeyDictionary

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import Settings, get_settings

#: The time zone each engine's connections are set to, read as each one connects.
_session_timezones: WeakKeyDictionary[Engine, str] = WeakKeyDictionary()


def create_database_engine(settings: Settings | None = None) -> Engine:
    """Create a production PostgreSQL engine with connection health checks."""

    config = settings or get_settings()
    created = create_engine(
        config.database_url,
        pool_pre_ping=True,
        pool_size=config.database_pool_size,
        max_overflow=config.database_max_overflow,
        pool_recycle=1800,
        connect_args={"connect_timeout": 10, "application_name": "pesticide_shop_desktop"},
    )
    set_session_timezone(created, config.app_timezone)
    return created


def set_session_timezone(target: Engine, timezone: str) -> None:
    """Make every connection read timestamps in the shop's time zone.

    PostgreSQL otherwise uses whatever zone the server was initialised with, so a
    database created on UTC would show and print every sale five hours off.
    Existing pooled connections are dropped so the new zone applies at once.
    """

    if target not in _session_timezones:

        @event.listens_for(target, "connect")
        def _apply_timezone(connection: Any, _record: object) -> None:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('TimeZone', %s, false)", (_session_timezones[target],)
                )

    _session_timezones[target] = timezone
    target.dispose()


engine = create_database_engine()
SessionFactory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@contextmanager
def session_scope(factory: sessionmaker[Session] = SessionFactory) -> Iterator[Session]:
    """Provide one atomic transaction and guarantee rollback on failure."""

    session = factory()
    try:
        with session.begin():
            yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
