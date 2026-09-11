"""Invoice emails sent at the counter instead of waiting for a background worker."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings
from app.email.email_service import OutgoingEmail
from app.email.outbox import deliver_pending_for
from app.models.customer import Customer
from app.models.email_history import EmailHistory
from app.models.enums import EmailStatus, SettingCategory
from app.models.inventory import StockBatch
from app.models.product import Product
from app.models.sale import Sale
from app.models.supplier import Supplier
from app.security.authentication import AuthenticatedUser
from app.services.dto import (
    CreatePesticideSaleCommand,
    CreateStockPurchaseCommand,
    PesticideSaleLineInput,
    PurchasedBatchInput,
)
from app.services.pesticide_sale_service import PesticideSaleService
from app.services.settings_service import SettingsService
from app.services.stock_purchase_service import StockPurchaseService
from app.utils.exceptions import InfrastructureError

OWNERS = ("owner@shop.invalid", "partner@shop.invalid")


class RecordingTransport:
    """Stands in for SMTP so a test never touches the network."""

    def __init__(self, failure: Exception | None = None) -> None:
        self.sent: list[OutgoingEmail] = []
        self._failure = failure

    def send(self, message: OutgoingEmail) -> None:
        if self._failure is not None:
            raise self._failure
        self.sent.append(message)


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> RecordingTransport:
    recorder = RecordingTransport()
    monkeypatch.setattr("app.email.smtp_client.SMTPEmailService", lambda _config: recorder)
    return recorder


def _sale_with_emails(
    session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
    *,
    configure_smtp: bool = True,
) -> Sale:
    settings = get_settings()
    stored = SettingsService(session, settings.app_secret_key.get_secret_value())
    if configure_smtp:
        stored.set(
            actor=owner, category=SettingCategory.EMAIL, key="smtp_host", value="smtp.invalid"
        )
        stored.set(
            actor=owner,
            category=SettingCategory.EMAIL,
            key="smtp_from_email",
            value="shop@shop.invalid",
        )
    purchase = StockPurchaseService(session).create(
        CreateStockPurchaseCommand(
            supplier_id=supplier.id,
            batches=(
                PurchasedBatchInput(
                    product_id=product.id,
                    batch_number="MAIL-1",
                    quantity=20,
                    purchase_price=Decimal("100.00"),
                    selling_price=Decimal("150.00"),
                    manufacture_date=date(2025, 1, 1),
                    expiry_date=date(2099, 1, 1),
                ),
            ),
            payments=(),
        ),
        owner,
    )
    session.flush()
    batch = session.scalar(select(StockBatch).where(StockBatch.purchase_id == purchase.id))
    assert batch is not None
    sale = PesticideSaleService(session).create(
        CreatePesticideSaleCommand(
            lines=(PesticideSaleLineInput(batch.id, 2),),
            payments=(),
            customer_id=customer.id,
        ),
        owner,
        owner_emails=OWNERS,
    )
    session.flush()
    return sale


def test_completing_a_sale_sends_its_emails_immediately(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
    transport: RecordingTransport,
) -> None:
    """Without a background worker these would otherwise sit unsent forever."""

    sale = _sale_with_emails(db_session, owner, supplier, product, customer)
    factory = sessionmaker[Session](
        bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
    )

    outcome = deliver_pending_for(factory, get_settings(), entity_type="Sale", entity_id=sale.id)

    assert (outcome.sent, outcome.failed, outcome.skipped) == (3, 0, False)
    assert {message.recipient for message in transport.sent} == {customer.email, *OWNERS}
    # The invoice PDF rides along with each copy.
    for message in transport.sent:
        assert len(message.attachments) == 1
        assert message.attachments[0].filename == f"{sale.invoice_number}.pdf"
        assert message.attachments[0].content.startswith(b"%PDF")

    statuses = set(
        db_session.scalars(select(EmailHistory.status).where(EmailHistory.entity_id == sale.id))
    )
    assert statuses == {EmailStatus.SENT}


def test_a_refused_mail_server_leaves_the_sale_alone(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sale is committed before anything is sent, so email can never undo it."""

    refused = RecordingTransport(InfrastructureError("535 Username and Password not accepted"))
    monkeypatch.setattr("app.email.smtp_client.SMTPEmailService", lambda _config: refused)
    sale = _sale_with_emails(db_session, owner, supplier, product, customer)
    factory = sessionmaker[Session](
        bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
    )

    outcome = deliver_pending_for(factory, get_settings(), entity_type="Sale", entity_id=sale.id)

    assert (outcome.sent, outcome.failed) == (0, 3)
    assert refused.sent == []
    db_session.expire_all()
    kept = db_session.get(Sale, sale.id)
    assert kept is not None, "the invoice survives a mail failure"
    assert kept.total == sale.total
    rows = list(db_session.scalars(select(EmailHistory).where(EmailHistory.entity_id == sale.id)))
    assert {row.status for row in rows} == {EmailStatus.FAILED}
    assert all("535" in (row.last_error or "") for row in rows), "the reason is kept to show"
    assert all(row.attempts == 1 for row in rows), "still retryable"


def test_messages_stay_queued_when_no_mail_server_is_configured(
    db_session: Session,
    owner: AuthenticatedUser,
    supplier: Supplier,
    product: Product,
    customer: Customer,
    transport: RecordingTransport,
) -> None:
    """An unconfigured shop must not mark its invoices failed for nothing."""

    sale = _sale_with_emails(db_session, owner, supplier, product, customer, configure_smtp=False)
    factory = sessionmaker[Session](
        bind=db_session.get_bind(), join_transaction_mode="create_savepoint"
    )

    outcome = deliver_pending_for(factory, get_settings(), entity_type="Sale", entity_id=sale.id)

    assert outcome.skipped is True
    assert outcome.attempted == 0
    assert transport.sent == []
    statuses = set(
        db_session.scalars(select(EmailHistory.status).where(EmailHistory.entity_id == sale.id))
    )
    assert statuses == {EmailStatus.PENDING}, "they wait, ready to send once SMTP is set up"
