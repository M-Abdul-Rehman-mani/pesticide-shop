from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings
from app.email.email_service import OutgoingEmail
from app.email.outbox import OutboxDeliveryService
from app.models.customer import Customer
from app.models.email_history import EmailHistory
from app.models.enums import (
    EmailStatus,
    PaymentMethod,
    ReturnCondition,
    ReturnReason,
)
from app.models.inventory import PhoneInventory
from app.security.authentication import AuthenticatedUser
from app.services.dto import (
    CreateReturnCommand,
    CreateSaleCommand,
    PaymentInput,
    SaleLineInput,
)
from app.services.return_service import ReturnService
from app.services.sale_service import SaleService
from app.utils.exceptions import ConflictError


class RecordingTransport:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.messages: list[OutgoingEmail] = []

    def send(self, message: OutgoingEmail) -> None:
        if self.error:
            raise self.error
        self.messages.append(message)


def _factory(db_session: Session) -> sessionmaker[Session]:
    return sessionmaker(
        bind=db_session.connection(),
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )


def _queued_email(db_session: Session) -> EmailHistory:
    row = EmailHistory(
        recipient="recipient@test.invalid",
        subject="Test",
        template="test",
        entity_type="Other",
        entity_id=uuid.uuid4(),
        status=EmailStatus.PENDING,
        attempts=0,
        body_text="Test message",
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_outbox_marks_success_and_is_idempotently_protected(db_session: Session) -> None:
    row = _queued_email(db_session)
    transport = RecordingTransport()
    delivery = OutboxDeliveryService(_factory(db_session), transport, get_settings())
    delivery.deliver(row.id)
    db_session.expire(row)
    assert row.status is EmailStatus.SENT
    assert row.attempts == 1
    assert len(transport.messages) == 1
    with pytest.raises(ConflictError, match="already been sent"):
        delivery.deliver(row.id)


def test_outbox_preserves_failure_for_retry(db_session: Session) -> None:
    row = _queued_email(db_session)
    delivery = OutboxDeliveryService(
        _factory(db_session),
        RecordingTransport(error=RuntimeError("SMTP unavailable")),
        get_settings(),
    )
    with pytest.raises(RuntimeError, match="SMTP unavailable"):
        delivery.deliver(row.id)
    db_session.expire(row)
    assert row.status is EmailStatus.FAILED
    assert row.attempts == 1
    assert "SMTP unavailable" in (row.last_error or "")


def test_sale_email_delivery_generates_pdf_attachment(
    db_session: Session,
    owner: AuthenticatedUser,
    customer: Customer,
    purchase_phone: object,
) -> None:
    phone: PhoneInventory = purchase_phone("350000000000701", None)  # type: ignore[operator]
    sale = SaleService(db_session).create(
        CreateSaleCommand(
            customer_id=customer.id,
            lines=(SaleLineInput(phone.imei_1),),
            payments=(PaymentInput(PaymentMethod.CASH, phone.selling_price),),
        ),
        owner,
    )
    db_session.flush()
    queued = db_session.scalar(
        select(EmailHistory).where(
            EmailHistory.entity_id == sale.id,
            EmailHistory.template == "sale_customer",
        )
    )
    assert queued is not None
    transport = RecordingTransport()
    OutboxDeliveryService(_factory(db_session), transport, get_settings()).deliver(queued.id)
    attachment = transport.messages[0].attachments[0]
    assert attachment.filename == f"{sale.invoice_number}.pdf"
    assert attachment.content.startswith(b"%PDF")


def test_return_email_delivery_generates_return_pdf_attachment(
    db_session: Session,
    owner: AuthenticatedUser,
    customer: Customer,
    purchase_phone: object,
) -> None:
    phone: PhoneInventory = purchase_phone("350000000000702", None)  # type: ignore[operator]
    sale = SaleService(db_session).create(
        CreateSaleCommand(
            customer_id=customer.id,
            lines=(SaleLineInput(phone.imei_1),),
            payments=(PaymentInput(PaymentMethod.CASH, phone.selling_price),),
        ),
        owner,
    )
    document = ReturnService(db_session).create(
        CreateReturnCommand(
            invoice_number=sale.invoice_number,
            imei=phone.imei_1,
            reason=ReturnReason.DEFECTIVE,
            condition=ReturnCondition.GOOD,
            refund_amount=phone.selling_price,
            refund_method=PaymentMethod.CASH,
            approved_by=owner.id,
        ),
        owner,
    )
    db_session.flush()
    queued = db_session.scalar(
        select(EmailHistory).where(
            EmailHistory.entity_id == document.id,
            EmailHistory.template == "return_customer",
        )
    )
    assert queued is not None
    transport = RecordingTransport()
    OutboxDeliveryService(_factory(db_session), transport, get_settings()).deliver(queued.id)
    attachment = transport.messages[0].attachments[0]
    assert attachment.filename == f"{document.return_number}.pdf"
    assert attachment.content.startswith(b"%PDF")
