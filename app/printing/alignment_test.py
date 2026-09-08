"""A calibration strip for checking what a thermal printer can actually mark."""

from __future__ import annotations

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


def alignment_test_page(width_mm: int, print_width_mm: int) -> bytes:
    """Render a ruler strip that shows where the printable area starts and ends.

    Everything is drawn from the paper edge, so whatever the printer drops tells the
    operator how wide its head really is: if the ``R`` marker is missing, the
    printable width is narrower than configured and should be reduced.
    """

    if width_mm <= 0 or not 0 < print_width_mm <= width_mm:
        raise ValueError("Printable width must be positive and fit inside the paper.")
    height_mm = 60
    buffer = BytesIO()
    page = canvas.Canvas(buffer, pagesize=(width_mm * mm, height_mm * mm))
    page.setTitle(f"Printer alignment test {print_width_mm} of {width_mm} mm")
    left = (width_mm - print_width_mm) / 2
    right = left + print_width_mm
    top = height_mm - 6

    page.setFont("Helvetica-Bold", 8)
    page.drawCentredString(width_mm / 2 * mm, top * mm, "PRINTER ALIGNMENT TEST")
    page.setFont("Helvetica", 6)
    page.drawCentredString(
        width_mm / 2 * mm, (top - 5) * mm, f"Paper {width_mm} mm - printable {print_width_mm} mm"
    )

    # Millimetre ruler measured from the left paper edge.
    ruler = top - 13
    page.setLineWidth(0.4)
    for position in range(0, width_mm + 1):
        tall = position % 10 == 0
        length = 3.5 if tall else (2.0 if position % 5 == 0 else 1.0)
        page.line(position * mm, ruler * mm, position * mm, (ruler + length) * mm)
        if tall:
            page.setFont("Helvetica", 4.5)
            page.drawCentredString(position * mm, (ruler - 3.2) * mm, str(position))
    page.line(0, ruler * mm, width_mm * mm, ruler * mm)

    # Edge markers: both must appear for the configured width to be correct.
    band = ruler - 14
    page.setFont("Helvetica-Bold", 9)
    page.drawString((left + 0.5) * mm, band * mm, "L")
    page.drawRightString((right - 0.5) * mm, band * mm, "R")
    page.setLineWidth(1.2)
    page.line(left * mm, (band - 2) * mm, left * mm, (band + 7) * mm)
    page.line(right * mm, (band - 2) * mm, right * mm, (band + 7) * mm)

    # A solid bar spanning the printable strip; any missing end is being clipped.
    bar = band - 10
    page.setFillColor(colors.black)
    page.rect(left * mm, bar * mm, print_width_mm * mm, 4 * mm, stroke=0, fill=1)
    page.setFillColor(colors.white)
    page.setFont("Helvetica-Bold", 6)
    page.drawString((left + 1) * mm, (bar + 1.2) * mm, "START")
    page.drawRightString((right - 1) * mm, (bar + 1.2) * mm, "END")

    page.setFillColor(colors.black)
    page.setFont("Helvetica", 5.5)
    page.drawCentredString(
        width_mm / 2 * mm,
        (bar - 6) * mm,
        "Both L and R visible? Width is correct.",
    )
    page.drawCentredString(
        width_mm / 2 * mm,
        (bar - 11) * mm,
        "R or END missing? Lower the printable width.",
    )
    page.showPage()
    page.save()
    return buffer.getvalue()
