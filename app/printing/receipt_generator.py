"""A4, 58 mm, and 80 mm PDF receipt generation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path

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

from app.models.return_record import SaleReturn
from app.models.sale import Sale


@dataclass(frozen=True, slots=True)
class ShopProfile:
    name: str
    address: str = ""
    phone: str = ""
    email: str = ""
    tax_information: str = ""
    currency: str = "PKR"
    footer: str = "Thank you for your business."
    logo_path: Path | None = None


@dataclass(frozen=True, slots=True)
class ReceiptLine:
    product: str
    imei: str
    quantity: int
    price: Decimal
    discount: Decimal
    total: Decimal


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

    @classmethod
    def from_sale(cls, sale: Sale, payment_methods: str = "See payment record") -> SaleReceiptData:
        return cls(
            invoice_number=sale.invoice_number,
            sold_at=sale.sale_date,
            customer_name=sale.customer.name if sale.customer else "Walk-in Customer",
            customer_phone=sale.customer.phone if sale.customer else "",
            lines=tuple(
                ReceiptLine(
                    product=item.product.display_name,
                    imei=item.imei,
                    quantity=1,
                    price=item.price,
                    discount=item.discount,
                    total=item.total,
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
        )


@dataclass(frozen=True, slots=True)
class ReturnReceiptData:
    return_number: str
    invoice_number: str
    returned_at: datetime
    customer_name: str
    customer_phone: str
    product: str
    imei: str
    reason: str
    condition: str
    refund: Decimal
    refund_method: str
    approved_by: str

    @classmethod
    def from_return(cls, document: SaleReturn) -> ReturnReceiptData:
        if len(document.items) != 1:
            raise ValueError("A return receipt requires exactly one serialized return item")
        item = document.items[0]
        return cls(
            return_number=document.return_number,
            invoice_number=document.sale.invoice_number,
            returned_at=document.return_date,
            customer_name=document.customer.name if document.customer else "Walk-in Customer",
            customer_phone=document.customer.phone if document.customer else "",
            product=item.sale_item.product.display_name,
            imei=item.sale_item.imei,
            reason=document.reason.value.replace("_", " ").title(),
            condition=document.condition.value.title(),
            refund=item.refund_amount,
            refund_method=document.refund_method.value.replace("_", " ").title(),
            approved_by=document.approver.full_name,
        )


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
            author=shop.name,
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
        story.extend(
            [
                Paragraph(shop.name, brand_style),
                Paragraph(
                    "<br/>".join(filter(None, (shop.address, shop.phone, shop.email))),
                    styles["Normal"],
                ),
                Spacer(1, 7 * mm),
                Table(
                    [
                        [
                            Paragraph(
                                f"<b>Bill To</b><br/>{receipt.customer_name}"
                                f"<br/>{receipt.customer_phone}",
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
            ]
        )
        table_data: list[list[object]] = [["Product", "IMEI", "Qty", "Price", "Discount", "Total"]]
        table_data.extend(
            [
                line.product,
                line.imei,
                str(line.quantity),
                self._amount(line.price, shop.currency),
                self._amount(line.discount, shop.currency),
                self._amount(line.total, shop.currency),
            ]
            for line in receipt.lines
        )
        item_table = Table(
            table_data,
            repeatRows=1,
            colWidths=[50 * mm, 35 * mm, 10 * mm, 25 * mm, 25 * mm, 25 * mm],
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
                Paragraph(shop.tax_information, styles["Normal"]),
                Paragraph(shop.footer, styles["Italic"]),
            ]
        )
        document.build(story)
        return buffer.getvalue()

    def generate_thermal(
        self, receipt: SaleReceiptData, shop: ShopProfile, width_mm: int = 80
    ) -> bytes:
        if width_mm not in {58, 80}:
            raise ValueError("Thermal receipt width must be 58 mm or 80 mm")
        buffer = BytesIO()
        page_height = max(120, 95 + len(receipt.lines) * 25) * mm
        width = width_mm * mm
        document = SimpleDocTemplate(
            buffer,
            pagesize=(width, page_height),
            leftMargin=3 * mm,
            rightMargin=3 * mm,
            topMargin=3 * mm,
            bottomMargin=3 * mm,
            title=f"Receipt {receipt.invoice_number}",
            author=shop.name,
        )
        base_size = 7 if width_mm == 58 else 8
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
        usable_width = width - 6 * mm
        story: list[Flowable] = [
            Paragraph(shop.name, heading),
            Paragraph("<br/>".join(filter(None, (shop.address, shop.phone, shop.email))), center),
            Spacer(1, 2 * mm),
            Paragraph(f"Invoice: {receipt.invoice_number}", left),
            Paragraph(f"Date: {receipt.sold_at:%d-%b-%Y %H:%M}", left),
            Paragraph(f"Customer: {receipt.customer_name}", left),
            Spacer(1, 2 * mm),
        ]
        for line in receipt.lines:
            story.extend(
                [
                    Paragraph(line.product, left),
                    Paragraph(f"IMEI: {line.imei}", left),
                    Table(
                        [[f"1 x {line.price:,.2f}", f"{line.total:,.2f}"]],
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
                Paragraph(f"Payment: {receipt.payment_methods}", left),
                Spacer(1, 3 * mm),
                Paragraph(shop.footer, center),
            ]
        )
        document.build(story)
        return buffer.getvalue()

    def generate_return_a4(self, receipt: ReturnReceiptData, shop: ShopProfile) -> bytes:
        """Render a customer-safe A4 return/refund receipt."""

        buffer = BytesIO()
        document = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=14 * mm,
            bottomMargin=14 * mm,
            title=f"Return {receipt.return_number}",
            author=shop.name,
        )
        styles = getSampleStyleSheet()
        heading = ParagraphStyle(
            "ReturnBrand",
            parent=styles["Title"],
            textColor=colors.HexColor("#17324D"),
            alignment=TA_CENTER,
            fontSize=20,
        )
        story: list[Flowable] = []
        if shop.logo_path and shop.logo_path.is_file():
            story.append(Image(str(shop.logo_path), width=30 * mm, height=30 * mm))
        story.extend(
            [
                Paragraph(shop.name, heading),
                Paragraph(
                    "<br/>".join(filter(None, (shop.address, shop.phone, shop.email))),
                    styles["Normal"],
                ),
                Spacer(1, 5 * mm),
                Paragraph("<b>RETURN / REFUND RECEIPT</b>", styles["Heading2"]),
                Table(
                    [
                        ["Return number", receipt.return_number],
                        ["Original invoice", receipt.invoice_number],
                        ["Date", receipt.returned_at.strftime("%d-%b-%Y %H:%M")],
                        ["Customer", receipt.customer_name],
                        ["Customer phone", receipt.customer_phone],
                        ["Approved by", receipt.approved_by],
                    ],
                    colWidths=[45 * mm, 125 * mm],
                ),
                Spacer(1, 6 * mm),
            ]
        )
        item_table = Table(
            [
                ["Product", "IMEI", "Reason", "Condition", "Refund"],
                [
                    receipt.product,
                    receipt.imei,
                    receipt.reason,
                    receipt.condition,
                    self._amount(receipt.refund, shop.currency),
                ],
            ],
            colWidths=[45 * mm, 35 * mm, 35 * mm, 25 * mm, 30 * mm],
        )
        item_table.setStyle(self._invoice_table_style())
        story.extend(
            [
                item_table,
                Spacer(1, 7 * mm),
                Paragraph(f"<b>Refund method:</b> {receipt.refund_method}", styles["Normal"]),
                Spacer(1, 12 * mm),
                Paragraph(shop.footer, styles["Italic"]),
            ]
        )
        document.build(story)
        return buffer.getvalue()

    def generate_return_thermal(
        self,
        receipt: ReturnReceiptData,
        shop: ShopProfile,
        width_mm: int = 80,
    ) -> bytes:
        """Render a 58 mm or 80 mm thermal return receipt."""

        if width_mm not in {58, 80}:
            raise ValueError("Thermal receipt width must be 58 mm or 80 mm")
        buffer = BytesIO()
        width = width_mm * mm
        document = SimpleDocTemplate(
            buffer,
            pagesize=(width, 145 * mm),
            leftMargin=3 * mm,
            rightMargin=3 * mm,
            topMargin=3 * mm,
            bottomMargin=3 * mm,
            title=f"Return {receipt.return_number}",
            author=shop.name,
        )
        size = 7 if width_mm == 58 else 8
        normal = ParagraphStyle(
            "ReturnThermalNormal", fontName="Helvetica", fontSize=size, leading=size + 2
        )
        center = ParagraphStyle("ReturnThermalCenter", parent=normal, alignment=TA_CENTER)
        heading = ParagraphStyle(
            "ReturnThermalHeading",
            parent=center,
            fontName="Helvetica-Bold",
            fontSize=size + 4,
            leading=size + 6,
        )
        usable_width = width - 6 * mm
        story: list[Flowable] = [
            Paragraph(shop.name, heading),
            Paragraph("RETURN / REFUND RECEIPT", center),
            Paragraph(f"Return: {receipt.return_number}", normal),
            Paragraph(f"Invoice: {receipt.invoice_number}", normal),
            Paragraph(f"Date: {receipt.returned_at:%d-%b-%Y %H:%M}", normal),
            Paragraph(f"Customer: {receipt.customer_name}", normal),
            self._thermal_rule(usable_width),
            Paragraph(receipt.product, normal),
            Paragraph(f"IMEI: {receipt.imei}", normal),
            Paragraph(f"Reason: {receipt.reason}", normal),
            Paragraph(f"Condition: {receipt.condition}", normal),
            self._thermal_rule(usable_width),
            Paragraph(f"REFUND: {shop.currency} {receipt.refund:,.2f}", heading),
            Paragraph(f"Method: {receipt.refund_method}", normal),
            Paragraph(f"Approved by: {receipt.approved_by}", normal),
            Spacer(1, 3 * mm),
            Paragraph(shop.footer, center),
        ]
        document.build(story)
        return buffer.getvalue()

    @staticmethod
    def _amount(value: Decimal, currency: str) -> str:
        return f"{currency} {value:,.2f}"

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
