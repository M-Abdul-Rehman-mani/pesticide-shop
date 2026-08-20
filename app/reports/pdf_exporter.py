"""Generic paginated business report PDFs."""

from __future__ import annotations

from collections.abc import Sequence
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


class PDFReportExporter:
    def render(
        self,
        *,
        title: str,
        headers: Sequence[str],
        rows: Sequence[Sequence[object]],
        subtitle: str | None = None,
        landscape_page: bool = True,
    ) -> bytes:
        buffer = BytesIO()
        page_size = landscape(A4) if landscape_page else A4
        document = SimpleDocTemplate(
            buffer,
            pagesize=page_size,
            leftMargin=12 * mm,
            rightMargin=12 * mm,
            topMargin=12 * mm,
            bottomMargin=12 * mm,
            title=title,
        )
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "ReportTitle",
            parent=styles["Title"],
            alignment=TA_CENTER,
            textColor=colors.HexColor("#17324D"),
        )
        story: list[Flowable] = [Paragraph(title, title_style)]
        if subtitle:
            story.extend([Paragraph(subtitle, styles["Normal"]), Spacer(1, 5 * mm)])
        data = [[Paragraph(str(value), styles["BodyText"]) for value in headers]]
        data.extend([Paragraph(str(value), styles["BodyText"]) for value in row] for row in rows)
        table = Table(data, repeatRows=1, hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B8C2CC")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#F4F7FA")],
                    ),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(table)
        document.build(story)
        return buffer.getvalue()

    def export(self, path: Path, **kwargs: object) -> Path:
        payload = self.render(**kwargs)  # type: ignore[arg-type]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path
