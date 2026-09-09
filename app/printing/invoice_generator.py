"""Named A4 invoice facade used by email and UI services."""

from app.printing.receipt_generator import ReceiptGenerator, SaleReceiptData, ShopProfile


class InvoiceGenerator:
    def __init__(self) -> None:
        self._renderer = ReceiptGenerator()

    def generate(self, invoice: SaleReceiptData, shop: ShopProfile) -> bytes:
        """Render the copy sent by email, which carries the amounts.

        The printed challan is a goods document, but a recipient reading it in their
        inbox needs to see what they are being charged.
        """

        return self._renderer.generate_a4(invoice, shop, with_amounts=True)
