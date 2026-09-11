"""A4, 58 mm, and 80 mm PDF receipt generation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
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

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ShopProfile:
    """Shop branding, with a separate billing identity for the A4 document.

    A shop often trades from a counter but bills from a registered office, so the
    counter receipt and the delivery challan carry different addresses and contact
    details. The billing fields fall back to the counter ones when left blank.
    """

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
    billing_address: str = ""
    billing_phone: str = ""
    billing_email: str = ""

    @property
    def document_address(self) -> str:
        """Address printed on the A4 challan."""

        return self.billing_address or self.address

    @property
    def document_phone(self) -> str:
        return self.billing_phone or self.phone

    @property
    def document_email(self) -> str:
        return self.billing_email or self.email


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
    #: Set when a copy has already been issued, so the document is stamped.
    is_duplicate: bool = False

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

#: Printable strip of each thermal roll at the usual 203 dpi head: 576 dots across
#: an 80 mm roll and 384 across a 58 mm one.
_THERMAL_PRINT_WIDTHS_MM = {58: 48, 80: 72}


class ReceiptGenerator:
    """Render receipt data without communicating with any printer."""

    @staticmethod
    def _logo(shop: ShopProfile, size_mm: float) -> Image | None:
        """Return the shop logo, or ``None`` with a reason in the log."""

        if shop.logo_path is None:
            return None
        if not shop.logo_path.is_file():
            logger.warning(
                "Shop logo is configured but missing, so it is not printed: %s",
                shop.logo_path,
            )
            return None
        try:
            return Image(str(shop.logo_path), width=size_mm * mm, height=size_mm * mm)
        except Exception:
            logger.warning("Shop logo could not be read: %s", shop.logo_path, exc_info=True)
            return None

    def generate_a4(
        self,
        receipt: SaleReceiptData,
        shop: ShopProfile,
        *,
        with_amounts: bool = False,
    ) -> bytes:
        """Render the A4 delivery challan.

        The layout follows the shop's existing pre-printed challan: identity block,
        a quantity-only line table, a boxed quantity total pinned near the foot of
        the page, and the three signature lines. Money belongs on the customer's
        receipt, not on the delivery document.
        """

        buffer = BytesIO()
        document = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=14 * mm,
            rightMargin=14 * mm,
            topMargin=12 * mm,
            bottomMargin=12 * mm,
            title=f"Delivery Challan {receipt.invoice_number}",
            author=shop.name or "Pesticide Shop Manager",
        )
        # The frame insets its contents, so both the column widths and the vertical
        # budget are measured inside that padding.
        width = document.width - 2 * _FRAME_PADDING
        available = document.height - 2 * _FRAME_PADDING
        story = self._challan_story(receipt, shop, width)
        if with_amounts:
            story.extend(self._challan_amounts(receipt, shop, width))
        used = self._story_height(story, width)
        # The signature block sits at the foot of the page, so the line table keeps
        # an open area beneath it exactly as the pre-printed form does.
        footer = self._challan_footer(receipt, width)
        slack = available - used - self._story_height(footer, width)
        if slack > 0:
            story.append(Spacer(1, slack))
        story.extend(footer)
        document.build(story)
        return buffer.getvalue()

    def _challan_story(
        self, receipt: SaleReceiptData, shop: ShopProfile, width: float
    ) -> list[Flowable]:
        label = ParagraphStyle("ChallanLabel", fontName="Helvetica-Bold", fontSize=9, leading=13)
        value = ParagraphStyle("ChallanValue", fontName="Helvetica", fontSize=9, leading=13)
        right = ParagraphStyle("ChallanRight", parent=value, alignment=TA_RIGHT)
        brand = ParagraphStyle(
            "ChallanBrand",
            fontName="Times-Bold",
            fontSize=15,
            leading=18,
            alignment=TA_CENTER,
        )
        contact = ParagraphStyle(
            "ChallanContact", fontName="Helvetica", fontSize=7.5, leading=10, alignment=TA_CENTER
        )
        title = ParagraphStyle(
            "ChallanTitle",
            fontName="Times-Bold",
            fontSize=14,
            leading=18,
            alignment=TA_CENTER,
        )

        heading: list[Flowable] = [Paragraph(escape(shop.name), brand)] if shop.name else []
        if details := self._challan_contact(shop):
            heading.append(Paragraph(details, contact))
        logo: Flowable = self._logo(shop, 24) or Spacer(1, 1)
        story: list[Flowable] = [
            Table(
                [[logo, heading]],
                colWidths=[28 * mm, width - 28 * mm],
                style=TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                        ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ]
                ),
            ),
            Spacer(1, 2 * mm),
            Paragraph("<u>DELIVERY CHALLAN / INVOICE</u>", title),
            Spacer(1, 2 * mm),
        ]
        if receipt.is_duplicate:
            story.append(Paragraph("DUPLICATE COPY", self._duplicate_style(title)))

        def field(text: str, style: ParagraphStyle = value) -> Paragraph:
            return Paragraph(text, style)

        third = width / 3
        story.extend(
            [
                Table(
                    [
                        [
                            field(f"<b>Invoice # :</b> {escape(receipt.invoice_number)}"),
                            "",
                            field(f"<b>Date:</b>  {receipt.sold_at:%d-%m-%Y}", right),
                        ]
                    ],
                    colWidths=[third * 1.4, third * 0.6, width - third * 2],
                    style=self._challan_field_style(),
                ),
                Table(
                    [[field(f"<b>Customer Name:</b>  {escape(receipt.customer_name)}")]],
                    colWidths=[width],
                    style=self._challan_field_style(),
                ),
                Table(
                    [[field(f"<b>Address:</b>  {escape(self._challan_address(receipt))}")]],
                    colWidths=[width],
                    style=self._challan_field_style(),
                ),
                Table(
                    [
                        [
                            field(f"<b>NIC No.:</b>  {escape(receipt.customer_identity)}"),
                            field(f"<b>Employee:</b> {escape(receipt.salesperson)}"),
                        ]
                    ],
                    colWidths=[third * 1.4, width - third * 1.4],
                    style=self._challan_field_style(),
                ),
                Table(
                    [
                        [
                            field(f"<b>Territory:</b> {escape(receipt.territory)}"),
                            field(f"<b>Order #:</b>  {escape(receipt.order_number)}"),
                            field(f"<b>Store:</b> {escape(receipt.store)}"),
                        ]
                    ],
                    colWidths=[third * 1.4, third * 0.9, width - third * 2.3],
                    style=self._challan_field_style(),
                ),
                Table(
                    [[field("<b>POD:</b>")]],
                    colWidths=[width],
                    style=self._challan_field_style(),
                ),
                Spacer(1, 3 * mm),
                self._challan_lines(receipt, width, label, value),
            ]
        )
        return story

    def _challan_lines(
        self,
        receipt: SaleReceiptData,
        width: float,
        label: ParagraphStyle,
        value: ParagraphStyle,
    ) -> Table:
        """Build the quantity-only line table with its ruled header."""

        cell = ParagraphStyle("ChallanCell", parent=value, fontSize=8, leading=11)
        right = ParagraphStyle("ChallanCellRight", parent=cell, alignment=TA_RIGHT)
        header = ParagraphStyle("ChallanHead", parent=label, fontSize=8, leading=11)
        centred = ParagraphStyle("ChallanHeadRight", parent=header, alignment=TA_RIGHT)
        columns = [width * 0.30, width * 0.18, width * 0.24, width * 0.09, width * 0.19]
        rows: list[list[Flowable | str]] = [
            [
                Paragraph("PRODUCT", header),
                Paragraph("POLICY", header),
                Paragraph("BATCH NO.", header),
                Paragraph("QTY", centred),
                Paragraph("CARTONS - PACKS", centred),
            ]
        ]
        for line in receipt.lines:
            rows.append(
                [
                    Paragraph(escape(line.product), cell),
                    Paragraph(escape(receipt.policy or "NET SALE"), cell),
                    Paragraph(escape(line.batch_number), cell),
                    Paragraph(f"{line.quantity:,}", right),
                    Paragraph(
                        f"{line.cartons} &nbsp;&nbsp;-&nbsp;&nbsp; {line.loose_packs}", right
                    ),
                ]
            )
        table = Table(rows, colWidths=columns, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("LINEABOVE", (0, 0), (-1, 0), 0.8, colors.black),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black),
                    ("BOX", (0, 1), (-1, -1), 0.8, colors.black),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        return table

    def _challan_footer(self, receipt: SaleReceiptData, width: float) -> list[Flowable]:
        """The boxed quantity total and the three signature lines."""

        bold = ParagraphStyle("ChallanTotal", fontName="Helvetica-Bold", fontSize=10, leading=13)
        right = ParagraphStyle("ChallanTotalValue", parent=bold, alignment=TA_RIGHT)
        quantity = sum(line.quantity for line in receipt.lines)
        cartons = sum(line.cartons for line in receipt.lines)
        columns = [width * 0.30, width * 0.18, width * 0.24, width * 0.09, width * 0.19]
        totals = Table(
            [
                [
                    Paragraph("Total:", bold),
                    "",
                    "",
                    Paragraph(f"{quantity:,}", right),
                    Paragraph(f"{cartons:,}", right),
                ]
            ],
            colWidths=columns,
        )
        totals.setStyle(
            TableStyle(
                [
                    ("BOX", (3, 0), (3, 0), 0.8, colors.black),
                    ("BOX", (4, 0), (4, 0), 0.8, colors.black),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        signature = ParagraphStyle(
            "ChallanSignature",
            fontName="Helvetica-BoldOblique",
            fontSize=9.5,
            leading=12,
            alignment=TA_CENTER,
        )
        # Blank columns keep the three rules apart instead of joining into one line.
        gap = width * 0.06
        signature_width = (width - 2 * gap) / 3
        signatures = Table(
            [
                [
                    Paragraph("Prepared By", signature),
                    "",
                    Paragraph("Approved By", signature),
                    "",
                    Paragraph("Dealer's Signature &amp; Stamp", signature),
                ]
            ],
            colWidths=[signature_width, gap, signature_width, gap, signature_width],
        )
        signatures.setStyle(
            TableStyle(
                [
                    ("LINEABOVE", (0, 0), (0, 0), 0.8, colors.black),
                    ("LINEABOVE", (2, 0), (2, 0), 0.8, colors.black),
                    ("LINEABOVE", (4, 0), (4, 0), 0.8, colors.black),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        return [
            self._dashed_rule(width),
            totals,
            self._dashed_rule(width),
            Spacer(1, 16 * mm),
            signatures,
        ]

    @staticmethod
    def _duplicate_style(parent: ParagraphStyle) -> ParagraphStyle:
        return ParagraphStyle(
            "DuplicateStamp",
            parent=parent,
            fontSize=10,
            leading=13,
            textColor=colors.HexColor("#8A1F1F"),
        )

    def _challan_amounts(
        self, receipt: SaleReceiptData, shop: ShopProfile, width: float
    ) -> list[Flowable]:
        """Priced summary for the copy sent by email.

        The printed challan is a goods document and carries no money, but a dealer
        receiving it by email needs to know what they are being charged.
        """

        label = ParagraphStyle("AmountLabel", fontName="Helvetica", fontSize=9, leading=12)
        value = ParagraphStyle("AmountValue", parent=label, alignment=TA_RIGHT)
        strong = ParagraphStyle("AmountStrong", parent=label, fontName="Helvetica-Bold")
        strong_value = ParagraphStyle("AmountStrongValue", parent=strong, alignment=TA_RIGHT)
        rows = [
            ("Subtotal", receipt.subtotal, False),
            ("Discount", receipt.discount, False),
            ("Tax", receipt.tax, False),
            ("TOTAL", receipt.total, True),
            ("Paid", receipt.paid, False),
            ("Balance", receipt.remaining, True),
        ]
        table = Table(
            [
                [
                    Paragraph(name, strong if bold else label),
                    Paragraph(self._amount(amount, shop.currency), strong_value if bold else value),
                ]
                for name, amount, bold in rows
            ],
            colWidths=[width * 0.22, width * 0.22],
            hAlign="RIGHT",
        )
        table.setStyle(
            TableStyle(
                [
                    ("LINEABOVE", (0, 3), (-1, 3), 0.8, colors.black),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        return [
            Spacer(1, 4 * mm),
            table,
            Spacer(1, 2 * mm),
            Paragraph(f"Payment: {escape(receipt.payment_methods)}", label),
        ]

    @staticmethod
    def _challan_field_style() -> TableStyle:
        return TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )

    @staticmethod
    def _challan_address(receipt: SaleReceiptData) -> str:
        parts = [receipt.customer_address, receipt.territory, receipt.customer_phone]
        return "  ".join(part.strip() for part in parts if part and part.strip())

    @staticmethod
    def _challan_contact(shop: ShopProfile) -> str:
        parts: list[str] = []
        parts.extend(line.strip() for line in shop.document_address.splitlines() if line.strip())
        if shop.document_phone:
            parts.append(shop.document_phone)
        if shop.document_email:
            parts.append(shop.document_email)
        if shop.tax_information:
            parts.append(shop.tax_information)
        return escape(" ".join(parts))

    @staticmethod
    def _dashed_rule(width: float) -> Table:
        table = Table([[""]], colWidths=[width], rowHeights=[0.6 * mm])
        table.setStyle(
            TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.9, colors.black, None, (3, 2))])
        )
        return table

    @staticmethod
    def _story_height(story: list[Flowable], width: float) -> float:
        """Measure a story the way a frame lays it out, collapsing the gaps."""

        total = 0.0
        previous_space_after = 0.0
        for index, flowable in enumerate(story):
            space_before = flowable.getSpaceBefore()
            total += space_before if index == 0 else max(previous_space_after, space_before)
            total += flowable.wrap(width, 0)[1]
            previous_space_after = flowable.getSpaceAfter()
        return total

    def generate_thermal(
        self,
        receipt: SaleReceiptData,
        shop: ShopProfile,
        width_mm: int = 80,
        print_width_mm: int | None = None,
    ) -> bytes:
        """Render a receipt for a thermal roll.

        ``width_mm`` is the paper width and ``print_width_mm`` the strip the head can
        mark -- 72 mm of an 80 mm roll, 48 mm of a 58 mm roll. Content is centred
        inside that strip, so nothing falls in the dead margin the printer cannot
        reach and no column is clipped.
        """

        if width_mm not in _THERMAL_PRINT_WIDTHS_MM:
            raise ValueError("Thermal receipt width must be 58 mm or 80 mm")
        printable = _THERMAL_PRINT_WIDTHS_MM[width_mm] if print_width_mm is None else print_width_mm
        if not 0 < printable <= width_mm:
            raise ValueError("Printable width must be positive and fit inside the paper.")
        width = width_mm * mm
        margin = max((width_mm - printable) / 2, 1.0) * mm
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
        """Build the counter receipt: identity, a priced line table, then totals."""

        normal = ParagraphStyle(
            "ThermalNormal", fontName="Helvetica", fontSize=base_size, leading=base_size + 3
        )
        center = ParagraphStyle("ThermalCenter", parent=normal, alignment=TA_CENTER)
        left = ParagraphStyle("ThermalLeft", parent=normal, alignment=TA_LEFT)
        bold = ParagraphStyle("ThermalBold", parent=normal, fontName="Helvetica-Bold")
        bold_right = ParagraphStyle("ThermalBoldRight", parent=bold, alignment=TA_RIGHT)
        bold_center = ParagraphStyle("ThermalBoldCenter", parent=bold, alignment=TA_CENTER)
        heading = ParagraphStyle(
            "ThermalHeading",
            parent=center,
            fontName="Helvetica-Bold",
            fontSize=base_size + 5,
            leading=base_size + 7,
        )
        story: list[Flowable] = []
        if (logo := self._logo(shop, 18)) is not None:
            story.append(logo)
        if shop.name:
            story.append(Paragraph(escape(shop.name), heading))
        for contact_line in self._thermal_contact(shop):
            story.append(Paragraph(contact_line, center))
        if receipt.is_duplicate:
            story.append(Paragraph("*** DUPLICATE COPY ***", bold_center))
        story.extend(
            [
                Spacer(1, 1.5 * mm),
                self._thermal_rule(usable_width),
                Paragraph(f"Invoice # {escape(receipt.invoice_number)}", left),
                Paragraph(f"Customer : {escape(receipt.customer_name)}", left),
                Paragraph(f"Date : {receipt.sold_at:%d-%b-%Y %H:%M}", left),
                self._thermal_rule(usable_width),
            ]
        )

        columns = [
            usable_width * 0.44,
            usable_width * 0.12,
            usable_width * 0.20,
            usable_width * 0.24,
        ]
        rows: list[list[Flowable]] = [
            [
                Paragraph("Item Name", bold),
                Paragraph("Qty.", bold_right),
                Paragraph("Price", bold_right),
                Paragraph("Sub Total", bold_right),
            ]
        ]
        cell = ParagraphStyle("ThermalCell", parent=normal, leading=base_size + 2)
        cell_right = ParagraphStyle("ThermalCellRight", parent=cell, alignment=TA_RIGHT)
        for line in receipt.lines:
            rows.append(
                [
                    Paragraph(escape(line.product), cell),
                    Paragraph(f"{line.quantity:,}", cell_right),
                    Paragraph(f"{line.price:,.0f}", cell_right),
                    Paragraph(f"{line.total:,.0f}", cell_right),
                ]
            )
        items = Table(rows, colWidths=columns, repeatRows=1)
        items.setStyle(
            TableStyle(
                [
                    ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        story.extend([items, self._thermal_rule(usable_width)])

        # Counts on the left, money on the right, as on the counter's own receipt.
        quantity = sum(line.quantity for line in receipt.lines)
        # Paid and Balance share the summary table so every money column lines up.
        summary_rows: list[list[Flowable]] = [
            [
                Paragraph("No of Items:", normal),
                Paragraph(f"{len(receipt.lines)}", bold),
                Paragraph("Total :", normal),
                Paragraph(f"{receipt.subtotal:,.0f}", bold_right),
            ],
            [
                Paragraph("Total Qty:", normal),
                Paragraph(f"{quantity:,}", bold),
                Paragraph("Discount :", normal),
                Paragraph(f"{receipt.discount:,.0f}", cell_right),
            ],
            [
                Paragraph("", normal),
                Paragraph("", normal),
                Paragraph("Grand Total :", bold),
                Paragraph(f"{receipt.total:,.0f}", bold_right),
            ],
        ]
        if receipt.remaining:
            summary_rows.extend(
                [
                    [
                        Paragraph("", normal),
                        Paragraph("", normal),
                        Paragraph("Paid :", normal),
                        Paragraph(f"{receipt.paid:,.0f}", cell_right),
                    ],
                    [
                        Paragraph("", normal),
                        Paragraph("", normal),
                        Paragraph("Balance :", bold),
                        Paragraph(f"{receipt.remaining:,.0f}", bold_right),
                    ],
                ]
            )
        summary = Table(
            summary_rows,
            colWidths=[
                usable_width * 0.28,
                usable_width * 0.14,
                usable_width * 0.32,
                usable_width * 0.26,
            ],
        )
        summary.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 1),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ]
            )
        )
        story.append(summary)
        story.extend(
            [
                Paragraph(f"Payment: {escape(receipt.payment_methods)}", left),
                self._thermal_rule(usable_width),
            ]
        )
        for footer_line in self._thermal_footer(shop):
            story.append(Paragraph(footer_line, center))
        return story

    @staticmethod
    def _thermal_contact(shop: ShopProfile) -> list[str]:
        """Address lines printed under the shop name."""

        lines = [line.strip() for line in shop.address.splitlines() if line.strip()]
        if shop.phone:
            lines.append(shop.phone)
        return [escape(line) for line in lines]

    @staticmethod
    def _thermal_footer(shop: ShopProfile) -> list[str]:
        lines: list[str] = []
        if shop.tax_information:
            lines.append(shop.tax_information)
        if shop.email:
            lines.append(shop.email)
        if shop.website:
            lines.append(shop.website)
        if shop.footer:
            lines.append(shop.footer)
        return [escape(line) for line in lines]

    @staticmethod
    def _amount(value: Decimal, currency: str) -> str:
        return f"{currency} {value:,.0f}"

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
        # A hairline lands on well under one dot of a 203 dpi head and prints as a
        # broken dotted line, so the separator is given a full dot of weight.
        table = Table([[""]], colWidths=[width], rowHeights=[1 * mm])
        table.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 1.0, colors.black)]))
        return table
