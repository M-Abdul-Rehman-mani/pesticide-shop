from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.email.email_service import EmailAttachment, OutgoingEmail
from app.email.smtp_client import SMTPConfig, SMTPEmailService
from app.printing.receipt_generator import (
    ReceiptGenerator,
    ReceiptLine,
    ReturnReceiptData,
    SaleReceiptData,
    ShopProfile,
)


def _receipt() -> SaleReceiptData:
    return SaleReceiptData(
        invoice_number="INV-2026-000001",
        sold_at=datetime.now(UTC),
        customer_name="Fictional Customer",
        customer_phone="+92 300 0000000",
        lines=(
            ReceiptLine(
                product="Test Phone 128GB",
                imei="350000000000301",
                quantity=1,
                price=Decimal("125000.00"),
                discount=Decimal("5000.00"),
                total=Decimal("120000.00"),
            ),
        ),
        subtotal=Decimal("125000.00"),
        discount=Decimal("5000.00"),
        tax=Decimal("0.00"),
        total=Decimal("120000.00"),
        paid=Decimal("120000.00"),
        remaining=Decimal("0.00"),
        payment_methods="CASH",
        salesperson="Test Salesperson",
    )


def _return_receipt() -> ReturnReceiptData:
    return ReturnReceiptData(
        return_number="RET-2026-000001",
        invoice_number="INV-2026-000001",
        returned_at=datetime.now(UTC),
        customer_name="Fictional Customer",
        customer_phone="+92 300 0000000",
        product="Test Phone 128GB",
        imei="350000000000301",
        reason="Defective",
        condition="Damaged",
        refund=Decimal("120000.00"),
        refund_method="Cash",
        approved_by="Test Owner",
    )


@pytest.mark.parametrize("width", [58, 80])
def test_thermal_receipts_are_valid_pdfs(width: int) -> None:
    payload = ReceiptGenerator().generate_thermal(_receipt(), ShopProfile(name="Test Shop"), width)
    assert payload.startswith(b"%PDF")
    assert len(payload) > 1000


def test_a4_invoice_is_valid_pdf() -> None:
    payload = ReceiptGenerator().generate_a4(_receipt(), ShopProfile(name="Test Shop"))
    assert payload.startswith(b"%PDF")
    assert len(payload) > 1000


@pytest.mark.parametrize("width", [58, 80])
def test_thermal_return_receipts_are_valid_pdfs(width: int) -> None:
    payload = ReceiptGenerator().generate_return_thermal(
        _return_receipt(), ShopProfile(name="Test Shop"), width
    )
    assert payload.startswith(b"%PDF")
    assert len(payload) > 1000


def test_a4_return_receipt_is_valid_pdf() -> None:
    payload = ReceiptGenerator().generate_return_a4(
        _return_receipt(), ShopProfile(name="Test Shop")
    )
    assert payload.startswith(b"%PDF")
    assert len(payload) > 1000


@patch("app.email.smtp_client.smtplib.SMTP")
def test_smtp_message_and_attachment_generation(smtp: MagicMock) -> None:
    client = smtp.return_value.__enter__.return_value
    service = SMTPEmailService(
        SMTPConfig(
            host="smtp.test.invalid",
            port=587,
            from_email="shop@test.invalid",
            username="shop",
            password="secret",
            use_tls=True,
        )
    )
    service.send(
        OutgoingEmail(
            recipient="customer@test.invalid",
            subject="Receipt",
            text_body="Attached receipt",
            attachments=(EmailAttachment("receipt.pdf", b"%PDF-test"),),
        )
    )
    client.starttls.assert_called_once()
    client.login.assert_called_once_with("shop", "secret")
    sent_message = client.send_message.call_args.args[0]
    assert sent_message["To"] == "customer@test.invalid"
    assert any(part.get_filename() == "receipt.pdf" for part in sent_message.iter_attachments())
