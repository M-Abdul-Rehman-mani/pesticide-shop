"""Named A4 invoice facade used by email and UI services."""

from app.printing.receipt_generator import ReceiptGenerator, SaleReceiptData, ShopProfile


class InvoiceGenerator:
    def __init__(self) -> None:
        self._renderer = ReceiptGenerator()

    def generate(self, invoice: SaleReceiptData, shop: ShopProfile) -> bytes:
        return self._renderer.generate_a4(invoice, shop)
