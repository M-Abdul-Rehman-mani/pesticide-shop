"""A4, 58 mm, and 80 mm PDF receipt generation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.models.sale import Sale


@dataclass(frozen=True, slots=True)
class ShopProfile:
    name: str = ""
    owner_name: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    website: str = ""
    tax_information: str = ""
    currency: str = "PKR"
    footer: str = "Thank you for your business."
    logo_path: Path | None = None


@dataclass(frozen=True, slots=True)
class ReceiptLine:
    product: str
    batch_number: str
    quantity: int
    price: Decimal
    discount: Decimal
    total: Decimal
    cartons: int = 0
    loose_packs: int = 0


@dataclass(frozen=True, slots=True)
class SaleReceiptData:
    invoice_number: str
    sold_at: datetime
    customer_name: str
    customer_phone: str
    lines: tuple[ReceiptLine, ...]
    subtotal: Decimal
    discount: Decimal
    tax: Decimal
    total: Decimal
    paid: Decimal
    remaining: Decimal
    payment_methods: str
    salesperson: str
    recipient_type: str = "Customer"
    customer_address: str = ""
    customer_identity: str = ""
    order_number: str = ""
    territory: str = ""
    policy: str = ""
    store: str = ""

    @classmethod
    def from_sale(cls, sale: Sale, payment_methods: str = "See payment record") -> SaleReceiptData:
        recipient = sale.dealer or sale.customer
        return cls(
            invoice_number=sale.invoice_number,
            sold_at=sale.sale_date,
            customer_name=(
                sale.dealer.display_name
                if sale.dealer
                else sale.customer.name
                if sale.customer
                else "Walk-in Customer"
            ),
            customer_phone=recipient.phone if recipient else "",
            lines=tuple(
                ReceiptLine(
                    product=item.product.display_name,
                    batch_number=item.batch_number,
                    quantity=item.quantity,
                    price=item.unit_price,
                    discount=item.discount,
                    total=item.total,
                    cartons=(
                        item.quantity // item.stock_batch.packs_per_carton
                        if item.stock_batch and item.stock_batch.packs_per_carton
                        else 0
                    ),
                    loose_packs=(
                        item.quantity % item.stock_batch.packs_per_carton
                        if item.stock_batch and item.stock_batch.packs_per_carton
                        else item.quantity
                    ),
                )
                for item in sale.items
            ),
            subtotal=sale.subtotal,
            discount=sale.discount,
            tax=sale.tax,
            total=sale.total,
            paid=sale.paid_amount,
            remaining=sale.remaining_amount,
            payment_methods=payment_methods,
            salesperson=sale.creator.full_name,
            recipient_type=sale.recipient_type.title(),
            customer_address=sale.delivery_address
            or (recipient.address if recipient else "")
            or "",
            customer_identity=(
                (sale.dealer.cnic or sale.dealer.tax_number or "")
                if sale.dealer
                else (sale.customer.cnic or "")
                if sale.customer
                else ""
            ),
            order_number=sale.order_number or "",
            territory=sale.territory or "",
            policy=sale.policy or "",
            store=sale.store or "",
        )


#: ReportLab frames inset their contents by 6 points on every side, which the
#: roll-width and page-height calculations must both account for.
_FRAME_PADDING = 6.0


class ReceiptGenerator:
    """Render receipt data without communicating with any printer."""

    def generate_a4(self, receipt: SaleReceiptData, shop: ShopProfile) -> bytes:
        buffer = BytesIO()
        document = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=14 * mm,
            bottomMargin=14 * mm,
            title=f"Invoice {receipt.invoice_number}",
            author=shop.name or "Pesticide Shop Manager",
        )
        styles = getSampleStyleSheet()
        brand_style = ParagraphStyle(
            "Brand",
            parent=styles["Title"],
            textColor=colors.HexColor("#17324D"),
            alignment=TA_CENTER,
            fontSize=20,
            leading=24,
        )
        right_style = ParagraphStyle("Right", parent=styles["Normal"], alignment=TA_RIGHT)
        story: list[Flowable] = []
        if shop.logo_path and shop.logo_path.is_file():
            story.append(Image(str(shop.logo_path), width=30 * mm, height=30 * mm))
        if shop.name:
            story.append(Paragraph(escape(shop.name), brand_style))
        if details := self._shop_details(shop):
            story.append(Paragraph(details, styles["Normal"]))
        story.extend(
            [
                Spacer(1, 3 * mm),
                Paragraph("<b><u>DELIVERY CHALLAN / INVOICE</u></b>", brand_style),
                Spacer(1, 5 * mm),
                Table(
                    [
                        [
                            Paragraph(
                                f"<b>{escape(receipt.recipient_type)}:</b> "
                                f"{escape(receipt.customer_name)}"
                                f"<br/><b>Phone:</b> {escape(receipt.customer_phone)}"
                                f"<br/><b>Address:</b> {escape(receipt.customer_address)}"
                                f"<br/><b>NIC/Tax No:</b> {escape(receipt.customer_identity)}",
                                styles["Normal"],
                            ),
                            Paragraph(
                                f"<b>Invoice:</b> {receipt.invoice_number}<br/>"
                                f"<b>Date:</b> {receipt.sold_at:%d-%b-%Y %H:%M}<br/>"
                                f"<b>Salesperson:</b> {receipt.salesperson}",
                                right_style,
                            ),
                        ]
                    ],
                    colWidths=[85 * mm, 85 * mm],
                ),
                Spacer(1, 7 * mm),
                Table(
                    [
                        [
                            "Territory",
                            receipt.territory or "—",
                            "Order #",
                            receipt.order_number or "—",
                            "Store",
                            receipt.store or "—",
                        ]
                    ],
                    colWidths=[20 * mm, 38 * mm, 20 * mm, 35 * mm, 16 * mm, 35 * mm],
                ),
                Spacer(1, 4 * mm),
            ]
        )
        item_cell = ParagraphStyle(
            "InvoiceItemCell", parent=styles["Normal"], fontSize=6.5, leading=8
        )
        item_header = ParagraphStyle(
            "InvoiceItemHeader",
            parent=item_cell,
            fontName="Helvetica-Bold",
            textColor=colors.white,
            alignment=TA_CENTER,
        )
        table_data: list[list[object]] = [
            [
                Paragraph(label, item_header)
                for label in (
                    "Product",
                    "Policy",
                    "Batch No.",
                    "Qty",
                    "Cartons / Packs",
                    "Unit Price",
                    "Discount",
                    "Total",
                )
            ]
        ]
        table_data.extend(
            [
                Paragraph(escape(line.product), item_cell),
                Paragraph(escape(receipt.policy or "NET SALE"), item_cell),
                Paragraph(escape(line.batch_number), item_cell),
                Paragraph(str(line.quantity), item_cell),
                Paragraph(f"{line.cartons} / {line.loose_packs}", item_cell),
                Paragraph(self._amount(line.price, shop.currency), item_cell),
                Paragraph(self._amount(line.discount, shop.currency), item_cell),
                Paragraph(self._amount(line.total, shop.currency), item_cell),
            ]
            for line in receipt.lines
        )
        item_table = Table(
            table_data,
            repeatRows=1,
            colWidths=[35 * mm, 20 * mm, 24 * mm, 10 * mm, 20 * mm, 22 * mm, 20 * mm, 24 * mm],
        )
        item_table.setStyle(self._invoice_table_style())
        story.extend([item_table, Spacer(1, 7 * mm)])
        totals = Table(
            [
                ["Subtotal", self._amount(receipt.subtotal, shop.currency)],
                ["Discount", self._amount(receipt.discount, shop.currency)],
                ["Tax", self._amount(receipt.tax, shop.currency)],
                ["TOTAL", self._amount(receipt.total, shop.currency)],
                ["Paid", self._amount(receipt.paid, shop.currency)],
                ["Remaining", self._amount(receipt.remaining, shop.currency)],
                ["Payment", receipt.payment_methods],
            ],
            colWidths=[35 * mm, 45 * mm],
            hAlign="RIGHT",
        )
        totals.setStyle(
            TableStyle(
                [
                    ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                    ("FONTNAME", (0, 3), (-1, 3), "Helvetica-Bold"),
                    ("LINEABOVE", (0, 3), (-1, 3), 1, colors.HexColor("#17324D")),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.extend(
            [
                totals,
                Spacer(1, 12 * mm),
                Table(
                    [["Prepared By", "Approved By", "Dealer / Customer Signature & Stamp"]],
                    colWidths=[50 * mm, 50 * mm, 70 * mm],
                    style=TableStyle(
                        [
                            ("LINEABOVE", (0, 0), (-1, 0), 0.8, colors.black),
                            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                            ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Oblique"),
                            ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ]
                    ),
                ),
                Spacer(1, 5 * mm),
            ]
        )
        if shop.footer:
            story.append(Paragraph(escape(shop.footer), styles["Italic"]))
        document.build(story)
        return buffer.getvalue()

    def generate_thermal(
        self, receipt: SaleReceiptData, shop: ShopProfile, width_mm: int = 80
    ) -> bytes:
        if width_mm not in {58, 80}:
            raise ValueError("Thermal receipt width must be 58 mm or 80 mm")
        width = width_mm * mm
        margin = 3 * mm
        usable_width = width - 2 * margin - 2 * _FRAME_PADDING
        base_size = 7 if width_mm == 58 else 8
        page_height = self._thermal_page_height(
            self._thermal_story(receipt, shop, base_size, usable_width),
            usable_width,
            margin,
        )
        buffer = BytesIO()
        document = SimpleDocTemplate(
            buffer,
            pagesize=(width, page_height),
            leftMargin=margin,
            rightMargin=margin,
            topMargin=margin,
            bottomMargin=margin,
            title=f"Receipt {receipt.invoice_number}",
            author=shop.name or "Pesticide Shop Manager",
        )
        document.build(self._thermal_story(receipt, shop, base_size, usable_width))
        return buffer.getvalue()

    @staticmethod
    def _thermal_page_height(story: list[Flowable], usable_width: float, margin: float) -> float:
        """Size the roll page to its content so a receipt always prints on one page.

        A fixed page estimate either wastes several centimetres of thermal roll on a
        short sale or spills a long one onto a second page. Each flowable is measured
        at the roll width instead, and the gaps between them are collapsed the same
        way ReportLab's frame does -- the larger of the preceding space-after and the
        following space-before.
        """

        content = 0.0
        previous_space_after = 0.0
        for index, flowable in enumerate(story):
            space_before = flowable.getSpaceBefore()
            content += space_before if index == 0 else max(previous_space_after, space_before)
            content += flowable.wrap(usable_width, 0)[1]
            previous_space_after = flowable.getSpaceAfter()
        return max(60 * mm, content + 2 * margin + 2 * _FRAME_PADDING + 2 * mm)

    def _thermal_story(
        self,
        receipt: SaleReceiptData,
        shop: ShopProfile,
        base_size: int,
        usable_width: float,
    ) -> list[Flowable]:
        normal = ParagraphStyle(
            "ThermalNormal", fontName="Helvetica", fontSize=base_size, leading=base_size + 2
        )
        center = ParagraphStyle("ThermalCenter", parent=normal, alignment=TA_CENTER)
        left = ParagraphStyle("ThermalLeft", parent=normal, alignment=TA_LEFT)
        right = ParagraphStyle("ThermalRight", parent=normal, alignment=TA_RIGHT)
        heading = ParagraphStyle(
            "ThermalHeading",
            parent=center,
            fontName="Helvetica-Bold",
            fontSize=base_size + 4,
            leading=base_size + 6,
        )
        story: list[Flowable] = []
        if shop.logo_path and shop.logo_path.is_file():
            story.append(Image(str(shop.logo_path), width=18 * mm, height=18 * mm))
        if shop.name:
            story.append(Paragraph(escape(shop.name), heading))
        if details := self._shop_details(shop):
            story.append(Paragraph(details, center))
        story.extend(
            [
                Spacer(1, 2 * mm),
                Paragraph(f"Invoice: {escape(receipt.invoice_number)}", left),
                Paragraph(f"Date: {receipt.sold_at:%d-%b-%Y %H:%M}", left),
                Paragraph(f"Customer: {escape(receipt.customer_name)}", left),
                Spacer(1, 2 * mm),
            ]
        )
        for line in receipt.lines:
            story.extend(
                [
                    Paragraph(escape(line.product), left),
                    Paragraph(f"Batch: {escape(line.batch_number)}", left),
                    Table(
                        [[f"{line.quantity} x {line.price:,.2f}", f"{line.total:,.2f}"]],
                        colWidths=[usable_width * 0.6, usable_width * 0.4],
                        style=TableStyle(
                            [
                                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                                ("FONTSIZE", (0, 0), (-1, -1), base_size),
                                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                            ]
                        ),
                    ),
                    Spacer(1, 1 * mm),
                ]
            )
        story.append(self._thermal_rule(usable_width))
        for label, value, bold in (
            ("Subtotal", receipt.subtotal, False),
            ("Discount", receipt.discount, False),
            ("Tax", receipt.tax, False),
            ("TOTAL", receipt.total, True),
            ("Paid", receipt.paid, False),
            ("Remaining", receipt.remaining, False),
        ):
            style = ParagraphStyle(
                f"{label}Style",
                parent=right,
                fontName="Helvetica-Bold" if bold else "Helvetica",
            )
            story.append(Paragraph(f"{label}: {shop.currency} {value:,.2f}", style))
        story.extend(
            [
                Paragraph(f"Payment: {escape(receipt.payment_methods)}", left),
                Spacer(1, 3 * mm),
            ]
        )
        if shop.footer:
            story.append(Paragraph(escape(shop.footer), center))
        return story

    @staticmethod
    def _amount(value: Decimal, currency: str) -> str:
        return f"{currency} {value:,.2f}"

    @staticmethod
    def _shop_details(shop: ShopProfile) -> str:
        lines: list[str] = []
        if shop.owner_name:
            lines.append(f"Owner: {shop.owner_name}")
        lines.extend(line for line in shop.address.splitlines() if line.strip())
        if shop.phone:
            lines.append(f"Contact: {shop.phone}")
        if shop.email:
            lines.append(f"Email: {shop.email}")
        if shop.website:
            lines.append(f"Website: {shop.website}")
        if shop.tax_information:
            lines.append(f"Tax / Registration: {shop.tax_information}")
        return "<br/>".join(escape(line.strip()) for line in lines)

    @staticmethod
    def _invoice_table_style() -> TableStyle:
        return TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B8C2CC")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F7FA")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )

    @staticmethod
    def _thermal_rule(width: float) -> Table:
        table = Table([[""]], colWidths=[width], rowHeights=[1 * mm])
        table.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.5, colors.black)]))
        return table
