from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.enums import PaymentMethod, PaymentStatus, PhoneStatus
from app.models.inventory import PhoneInventory
from app.models.payment import Payment
from app.reports.report_service import DateRange, ReportService
from app.security.authentication import AuthenticatedUser
from app.services.dto import CreateSaleCommand, PaymentInput, SaleLineInput
from app.services.sale_service import SaleService
from app.utils.exceptions import ConflictError, ValidationError


def test_create_sale_with_split_payments_and_profit(
    db_session: Session,
    owner: AuthenticatedUser,
    customer: Customer,
    purchase_phone: object,
) -> None:
    phone = purchase_phone("350000000000101", None)  # type: ignore[operator]
    sale = SaleService(db_session).create(
        CreateSaleCommand(
            customer_id=customer.id,
            lines=(
                SaleLineInput(
                    phone.imei_1,
                    price=Decimal("125000.00"),
                    discount=Decimal("5000.00"),
                    other_cost=Decimal("1000.00"),
                ),
            ),
            payments=(
                PaymentInput(PaymentMethod.CASH, Decimal("70000.00")),
                PaymentInput(PaymentMethod.BANK_TRANSFER, Decimal("50000.00"), "BANK-1"),
            ),
        ),
        owner,
    )
    db_session.flush()
    assert sale.total == Decimal("120000.00")
    assert sale.payment_status is PaymentStatus.PAID
    assert phone.status is PhoneStatus.SOLD
    assert len(db_session.scalars(select(Payment).where(Payment.sale_id == sale.id)).all()) == 2
    period = DateRange(datetime.now(UTC) - timedelta(days=1), datetime.now(UTC) + timedelta(days=1))
    assert ReportService(db_session).profit(period) == Decimal("19000.00")


def test_cannot_sell_unavailable_phone(
    db_session: Session,
    owner: AuthenticatedUser,
    purchase_phone: object,
) -> None:
    phone: PhoneInventory = purchase_phone("350000000000102", None)  # type: ignore[operator]
    command = CreateSaleCommand(
        lines=(SaleLineInput(phone.imei_1),),
        payments=(PaymentInput(PaymentMethod.CASH, Decimal("125000.00")),),
    )
    SaleService(db_session).create(command, owner)
    db_session.flush()
    with pytest.raises(ConflictError, match="cannot be sold"):
        SaleService(db_session).create(command, owner)


def test_payment_cannot_exceed_total(
    db_session: Session,
    owner: AuthenticatedUser,
    purchase_phone: object,
) -> None:
    phone: PhoneInventory = purchase_phone("350000000000103", None)  # type: ignore[operator]
    with pytest.raises(ValidationError, match="exceed"):
        SaleService(db_session).create(
            CreateSaleCommand(
                lines=(SaleLineInput(phone.imei_1),),
                payments=(PaymentInput(PaymentMethod.CASH, Decimal("125000.01")),),
            ),
            owner,
        )


def test_sale_price_can_exceed_planned_selling_price(
    db_session: Session,
    owner: AuthenticatedUser,
    purchase_phone: object,
) -> None:
    phone: PhoneInventory = purchase_phone("350000000000105", None)  # type: ignore[operator]
    actual_price = phone.selling_price + Decimal("25000.00")

    sale = SaleService(db_session).create(
        CreateSaleCommand(
            lines=(SaleLineInput(phone.imei_1, price=actual_price),),
            payments=(PaymentInput(PaymentMethod.CASH, actual_price),),
        ),
        owner,
    )

    assert sale.total == actual_price
    assert sale.items[0].price == actual_price


def test_sale_queues_customer_and_owner_email(
    db_session: Session,
    owner: AuthenticatedUser,
    customer: Customer,
    purchase_phone: object,
) -> None:
    from app.models.email_history import EmailHistory

    phone: PhoneInventory = purchase_phone("350000000000104", None)  # type: ignore[operator]
    sale = SaleService(db_session).create(
        CreateSaleCommand(
            customer_id=customer.id,
            lines=(SaleLineInput(phone.imei_1),),
            payments=(PaymentInput(PaymentMethod.CASH, Decimal("125000.00")),),
        ),
        owner,
        owner_email="notifications@test.invalid",
    )
    db_session.flush()
    messages = db_session.scalars(
        select(EmailHistory).where(EmailHistory.entity_id == sale.id)
    ).all()
    assert {message.recipient for message in messages} == {
        "customer@test.invalid",
        "notifications@test.invalid",
    }
