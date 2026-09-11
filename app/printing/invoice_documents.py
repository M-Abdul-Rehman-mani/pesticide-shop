"""Build the printable document for a saved sale, for any screen that needs it."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace

from sqlalchemy import select, update
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
    is_duplicate: bool = False


def build_sale_document(
    session_factory: sessionmaker[Session],
    settings: Settings,
    sale_id: uuid.UUID,
    *,
    thermal: bool = False,
    with_amounts: bool = False,
    record_issue: bool = False,
) -> SaleDocument:
    """Render one sale's invoice.

    The A4 layout is used for previews and saved PDFs; ``thermal`` renders the same
    sale at the roll width chosen in Settings > Printer so it prints on the
    counter's receipt printer. ``record_issue`` counts the copy as issued, which is
    what makes every later copy print as a duplicate; a preview leaves it alone.
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
        shop = load_shop_profile(session, settings)
        preferences = load_print_preferences(session, settings)
        invoice_number = sale.invoice_number
        sale_data = SaleReceiptData.from_sale(sale, method_text)
        already_issued = sale.print_count > 0
    if record_issue:
        # The copy is claimed before it is rendered, and claimed by the database
        # rather than by a read-then-write here, so two counters printing the same
        # invoice at once cannot both believe theirs is the original.
        already_issued = _claim_copy(session_factory, sale_id) > 1
    receipt = replace(sale_data, is_duplicate=already_issued)
    generator = ReceiptGenerator()
    payload = (
        generator.generate_thermal(
            receipt,
            shop,
            preferences.receipt.width_mm,
            preferences.effective_print_width_mm,
        )
        if thermal and preferences.receipt.width_mm is not None
        else generator.generate_a4(receipt, shop, with_amounts=with_amounts)
    )
    return SaleDocument(payload, invoice_number, preferences, is_duplicate=already_issued)


def _claim_copy(session_factory: sessionmaker[Session], sale_id: uuid.UUID) -> int:
    """Count one issued copy atomically and report which copy this is."""

    with session_factory.begin() as session:
        issued = session.execute(
            update(Sale)
            .where(Sale.id == sale_id)
            .values(print_count=Sale.print_count + 1)
            .returning(Sale.print_count)
        ).scalar_one_or_none()
        if issued is None:
            raise NotFoundError("The invoice was not found.")
        return int(issued)
