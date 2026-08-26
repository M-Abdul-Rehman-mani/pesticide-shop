"""Database engine and transaction lifecycle management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import Settings, get_settings


def create_database_engine(settings: Settings | None = None) -> Engine:
    """Create a production PostgreSQL engine with connection health checks."""

    config = settings or get_settings()
    return create_engine(
        config.database_url,
        pool_pre_ping=True,
        pool_size=config.database_pool_size,
        max_overflow=config.database_max_overflow,
        pool_recycle=1800,
        connect_args={"connect_timeout": 10, "application_name": "pesticide_shop_desktop"},
    )


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
