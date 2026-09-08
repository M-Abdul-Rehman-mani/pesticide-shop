"""A representative invoice used to preview printer and paper choices."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.printing.receipt_generator import ReceiptLine, SaleReceiptData


def sample_receipt(currency: str = "PKR") -> SaleReceiptData:
    """Return a fixed demonstration sale; nothing is read from or written to the database."""

    lines = (
        ReceiptLine(
            product="Sample Insecticide 250 EC",
            batch_number="BATCH-2401",
            quantity=4,
            price=Decimal("1250.00"),
            discount=Decimal("100.00"),
            total=Decimal("4900.00"),
            cartons=1,
            loose_packs=0,
        ),
        ReceiptLine(
            product="Sample Fungicide 80 WP",
            batch_number="BATCH-2402",
            quantity=2,
            price=Decimal("880.00"),
            discount=Decimal("0.00"),
            total=Decimal("1760.00"),
            cartons=0,
            loose_packs=2,
        ),
    )
    return SaleReceiptData(
        invoice_number="SAMPLE-0001",
        sold_at=datetime(2026, 1, 15, 11, 30),
        customer_name="Sample Customer",
        customer_phone="+92 300 0000000",
        lines=lines,
        subtotal=Decimal("6760.00"),
        discount=Decimal("100.00"),
        tax=Decimal("0.00"),
        total=Decimal("6660.00"),
        paid=Decimal("6660.00"),
        remaining=Decimal("0.00"),
        payment_methods="CASH",
        salesperson="Sample Salesperson",
        customer_address="Sample address, Sample city",
        order_number="SO-0001",
        territory="Sample territory",
        policy="NET SALE",
        store="FINISHED",
    )


__all__ = ["sample_receipt"]
