"""PostgreSQL fixtures with transaction isolation for pesticide-shop tests."""

from __future__ import annotations

import os
from collections.abc import Generator
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from alembic import command

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.update(
    {
        "APP_ENV": "test",
        "APP_SECRET_KEY": "test-pesticide-shop-secret-at-least-thirty-two-chars",
        "DATABASE_HOST": os.environ.get("TEST_DATABASE_HOST", "127.0.0.1"),
        "DATABASE_PORT": os.environ.get("TEST_DATABASE_PORT", "5441"),
        "DATABASE_NAME": os.environ.get("TEST_DATABASE_NAME", "pesticide_shop_test"),
        "DATABASE_USER": os.environ.get("TEST_DATABASE_USER", "pesticide_shop_test"),
        "DATABASE_PASSWORD": os.environ.get(
            "TEST_DATABASE_PASSWORD", "local-pesticide-shop-test-password"
        ),
        "DATABASE_SSL_MODE": "disable",
    }
)

from app.config.settings import get_settings
from app.models.customer import Customer
from app.models.enums import UserRole
from app.models.product import Product
from app.models.supplier import Supplier
from app.models.user import User
from app.security.authentication import AuthenticatedUser


def pytest_sessionstart(session: pytest.Session) -> None:
    settings = get_settings()
    if not settings.database_name.endswith("_test"):
        pytest.exit("Refusing to run tests against a database not ending in '_test'.")
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def database_engine() -> Generator[Engine, None, None]:
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(database_engine: Engine) -> Generator[Session, None, None]:
    connection = database_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture
def owner_model(db_session: Session) -> User:
    owner = User(
        username="test-owner",
        full_name="Test Owner",
        email="owner@test.invalid",
        password_hash="test-only-unused-hash",
        role=UserRole.OWNER,
        is_active=True,
        must_change_password=False,
    )
    db_session.add(owner)
    db_session.flush()
    return owner


@pytest.fixture
def owner(owner_model: User) -> AuthenticatedUser:
    return AuthenticatedUser.from_model(owner_model)


@pytest.fixture
def supplier(db_session: Session) -> Supplier:
    supplier = Supplier(
        name="Test Supplier",
        phone="+92 300 1111111",
        email="supplier@test.invalid",
        is_active=True,
        balance=Decimal("0.00"),
    )
    db_session.add(supplier)
    db_session.flush()
    return supplier


@pytest.fixture
def product(db_session: Session) -> Product:
    product = Product(
        manufacturer="Test Crop Sciences",
        name="Test Herbicide",
        active_ingredient="Quizalofop-P-Ethyl",
        formulation="15% EC",
        pack_size="500-ML",
        registration_number="REG-TEST-1",
        unit="PACK",
        category="HERBICIDE",
        default_purchase_price=Decimal("100.00"),
        default_sale_price=Decimal("150.00"),
        minimum_stock=2,
        is_active=True,
    )
    db_session.add(product)
    db_session.flush()
    return product


@pytest.fixture
def customer(db_session: Session) -> Customer:
    customer = Customer(
        name="Test Customer",
        phone="+92 311 2222222",
        email="customer@test.invalid",
    )
    db_session.add(customer)
    db_session.flush()
    return customer
