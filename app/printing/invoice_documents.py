"""Build the printable document for a saved sale, for any screen that needs it."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.config.settings import Settings
from app.models.enums import PaymentDirection
from app.models.payment import Payment
from app.models.sale import Sale
from app.printing.preferences import PrintPreferences, load_print_preferences
from app.printing.receipt_generator import ReceiptGenerator, SaleReceiptData
from app.printing.shop_profile import load_shop_profile
from app.utils.exceptions import NotFoundError


@dataclass(frozen=True, slots=True)
class SaleDocument:
    """A rendered invoice together with the printer it is destined for."""

    payload: bytes
    invoice_number: str
    preferences: PrintPreferences


def build_sale_document(
    session_factory: sessionmaker[Session],
    settings: Settings,
    sale_id: uuid.UUID,
    *,
    thermal: bool = False,
) -> SaleDocument:
    """Render one sale's invoice.

    The A4 layout is used for previews and saved PDFs; ``thermal`` renders the same
    sale at the roll width chosen in Settings > Printer so it prints on the
    counter's receipt printer.
    """

    with session_factory() as session:
        sale = session.execute(
            select(Sale).options(selectinload(Sale.items)).where(Sale.id == sale_id)
        ).scalar_one_or_none()
        if sale is None:
            raise NotFoundError("The invoice was not found.")
        methods = session.scalars(
            select(Payment.method).where(
                Payment.sale_id == sale.id, Payment.direction == PaymentDirection.INCOMING
            )
        )
        method_text = ", ".join(dict.fromkeys(method.value for method in methods)) or "UNPAID"
        receipt = SaleReceiptData.from_sale(sale, method_text)
        shop = load_shop_profile(session, settings)
        preferences = load_print_preferences(session, settings)
        invoice_number = sale.invoice_number
    generator = ReceiptGenerator()
    payload = (
        generator.generate_thermal(
            receipt,
            shop,
            preferences.receipt.width_mm,
            preferences.effective_print_width_mm,
        )
        if thermal and preferences.receipt.width_mm is not None
        else generator.generate_a4(receipt, shop)
    )
    return SaleDocument(payload, invoice_number, preferences)
