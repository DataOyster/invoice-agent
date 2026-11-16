"""Render one ground-truth invoice as a PDF, in its supplier's language and layout.

Three layouts (A, B, C) and three languages. Numbers and dates are written the local way
("1 234,50" in Swedish, "1.234,50" in Italian, "1,234.50" in English), so extraction has to
normalise them. `invariant=1` removes timestamps and random ids, so the bytes are reproducible.

Unreadable cases:
- "blurred_image_only": the page is drawn as a blurred image, with no text layer at all;
- "truncated_file": a valid PDF cut short, so it cannot be parsed.
Both must end in manual review; they are not anomalies.
"""

from __future__ import annotations

import io
from datetime import date

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from invoice_agent.data_gen import catalog
from invoice_agent.data_gen.models import InvoiceGroundTruth, Supplier

LABELS = {
    "sv": {
        "invoice": "FAKTURA",
        "credit_note": "KREDITFAKTURA",
        "number": "Fakturanummer",
        "invoice_date": "Fakturadatum",
        "delivery_date": "Leveransdatum",
        "org": "Org.nr",
        "iban": "IBAN",
        "buyer": "Kund",
        "item": "Artikel",
        "qty": "Antal",
        "unit": "Enhet",
        "price": "À-pris",
        "vat": "Moms",
        "amount": "Belopp",
        "excl": "Summa exkl. moms",
        "vat_total": "Momsbelopp",
        "incl": "Att betala",
        "credits": "Avser faktura",
        "note": "Följesedel",
    },
    "en": {
        "invoice": "INVOICE",
        "credit_note": "CREDIT NOTE",
        "number": "Invoice number",
        "invoice_date": "Invoice date",
        "delivery_date": "Delivery date",
        "org": "Org. no.",
        "iban": "IBAN",
        "buyer": "Bill to",
        "item": "Item",
        "qty": "Qty",
        "unit": "Unit",
        "price": "Unit price",
        "vat": "VAT",
        "amount": "Amount",
        "excl": "Total excl. VAT",
        "vat_total": "VAT amount",
        "incl": "Total to pay",
        "credits": "Credits invoice",
        "note": "Delivery note",
    },
    "it": {
        "invoice": "FATTURA",
        "credit_note": "NOTA DI CREDITO",
        "number": "Numero fattura",
        "invoice_date": "Data fattura",
        "delivery_date": "Data consegna",
        "org": "N. org.",
        "iban": "IBAN",
        "buyer": "Cliente",
        "item": "Articolo",
        "qty": "Quantità",
        "unit": "Unità",
        "price": "Prezzo unit.",
        "vat": "IVA",
        "amount": "Importo",
        "excl": "Totale imponibile",
        "vat_total": "Totale IVA",
        "incl": "Totale da pagare",
        "credits": "Rif. fattura",
        "note": "DDT",
    },
}

# Layout D (hard split only): other wording and another row format, so the rules written for
# layouts A-C do not recognise it. Titles stay the same.
LABELS_D = {
    "sv": {
        "number": "Faktura nr",
        "invoice_date": "Datum",
        "delivery_date": "Levererat",
        "note": "Följesedel nr",
        "credits": "Kreditering av",
        "excl": "Netto",
        "vat_total": "Moms totalt",
        "incl": "Totalt att betala",
        "vat": "moms",
    },
    "en": {
        "number": "Bill no.",
        "invoice_date": "Issued",
        "delivery_date": "Goods delivered on",
        "note": "Delivery ref.",
        "credits": "Credit for bill",
        "excl": "Net amount",
        "vat_total": "Tax",
        "incl": "Amount due",
        "vat": "VAT",
    },
    "it": {
        "number": "Fattura n.",
        "invoice_date": "Emessa il",
        "delivery_date": "Consegnata il",
        "note": "Documento di trasporto",
        "credits": "Storno fattura",
        "excl": "Imponibile",
        "vat_total": "Imposta",
        "incl": "Totale dovuto",
        "vat": "IVA",
    },
}

UNIT_LABELS = {
    "sv": {"kg": "kg", "l": "l", "pcs": "st", "bottle": "fl"},
    "en": {"kg": "kg", "l": "l", "pcs": "pcs", "bottle": "btl"},
    "it": {"kg": "kg", "l": "l", "pcs": "pz", "bottle": "bott."},
}

MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def format_number(value: float, language: str, decimals: int = 2) -> str:
    text = f"{value:,.{decimals}f}"  # e.g. "1,234.50"
    if language == "sv":
        return text.replace(",", " ").replace(".", ",")
    if language == "it":
        return text.replace(",", "_").replace(".", ",").replace("_", ".")
    return text


def format_qty(value: float, language: str) -> str:
    decimals = 0 if value == int(value) else 1
    return format_number(value, language, decimals)


def format_date(day: date, language: str) -> str:
    if language == "sv":
        return day.isoformat()
    if language == "it":
        return day.strftime("%d/%m/%Y")
    return f"{day.day} {MONTHS_EN[day.month - 1]} {day.year}"


def format_rate(rate: float) -> str:
    return f"{round(rate * 100)}%"


def _text_rows(inv: InvoiceGroundTruth, supplier: Supplier) -> dict:
    """All strings printed on the invoice, already formatted for its language."""
    lang = inv.language
    lab = LABELS[lang]
    meta = [
        (lab["number"], inv.number),
        (lab["invoice_date"], format_date(inv.invoice_date, lang)),
        (lab["delivery_date"], format_date(inv.delivery_date, lang)),
    ]
    if inv.delivery_note_id:
        meta.append((lab["note"], inv.delivery_note_id))
    if inv.credits_invoice_number:
        meta.append((lab["credits"], inv.credits_invoice_number))
    rows = [
        (
            line.description,
            format_qty(line.qty, lang),
            UNIT_LABELS[lang][line.unit],
            format_number(line.unit_price, lang),
            format_rate(line.vat_rate),
            format_number(line.line_total, lang),
        )
        for line in inv.lines
    ]
    return {
        "title": lab[inv.document_type],
        "meta": meta,
        "header": (lab["item"], lab["qty"], lab["unit"], lab["price"], lab["vat"], lab["amount"]),
        "rows": rows,
        "totals": [
            (lab["excl"], format_number(inv.total_excl_vat, lang)),
            (lab["vat_total"], format_number(inv.vat_total, lang)),
            (lab["incl"], format_number(inv.total_incl_vat, lang) + " SEK"),
        ],
        "supplier": [
            supplier.name,
            supplier.address,
            f"{lab['org']} {supplier.org_number}",
            f"{lab['iban']} {supplier.iban}",
        ],
        "buyer": [lab["buyer"] + ":", catalog.BUYER["name"], catalog.BUYER["address"]],
    }


# Column x positions (points) per layout: item, qty, unit, price, vat, amount.
_COLUMNS = {
    "A": (50, 290, 340, 400, 470, 520),
    "B": (60, 300, 345, 405, 470, 525),
    "C": (40, 280, 330, 395, 465, 525),
}


def _draw_table(
    c: canvas.Canvas, layout: str, header: tuple, rows: list[tuple], top: float
) -> float:
    cols = _COLUMNS[layout]
    c.setFont("Helvetica-Bold", 9)
    for x, text in zip(cols, header):
        c.drawString(x, top, text)
    c.line(cols[0], top - 4, 560, top - 4)
    c.setFont("Helvetica", 9)
    y = top - 18
    for row in rows:
        for x, text in zip(cols, row):
            c.drawString(x, y, text)
        y -= 14
    c.line(cols[0], y + 6, 560, y + 6)
    return y - 10


def _draw_lines(c: canvas.Canvas, x: float, y: float, lines: list[str], size: int = 9) -> None:
    c.setFont("Helvetica", size)
    for i, text in enumerate(lines):
        c.drawString(x, y - i * 12, text)


def _draw_page(c: canvas.Canvas, layout: str, t: dict) -> None:
    meta_lines = [f"{k}: {v}" for k, v in t["meta"]]
    total_lines = [f"{k}: {v}" for k, v in t["totals"]]
    if layout == "A":  # supplier top-left, title top-right, table, totals bottom-right
        c.setFont("Helvetica-Bold", 13)
        c.drawString(50, 790, t["supplier"][0])
        _draw_lines(c, 50, 774, t["supplier"][1:])
        c.setFont("Helvetica-Bold", 18)
        c.drawRightString(560, 790, t["title"])
        _draw_lines(c, 360, 760, meta_lines)
        _draw_lines(c, 50, 700, t["buyer"])
        y = _draw_table(c, layout, t["header"], t["rows"], 630)
        _draw_lines(c, 360, y, total_lines, 10)
    elif layout == "B":  # title banner, meta in a box, supplier details in the footer
        c.setFont("Helvetica-Bold", 20)
        c.drawString(60, 790, t["title"])
        c.setFont("Helvetica-Bold", 11)
        c.drawString(60, 768, t["supplier"][0])
        c.rect(330, 700, 230, 90)
        _draw_lines(c, 340, 775, meta_lines)
        _draw_lines(c, 60, 740, t["buyer"])
        y = _draw_table(c, layout, t["header"], t["rows"], 660)
        _draw_lines(c, 330, y, total_lines, 10)
        _draw_lines(c, 60, 90, t["supplier"], 8)
    else:  # C: buyer left, supplier block right, meta under the title, totals left
        _draw_lines(c, 40, 790, t["buyer"])
        c.setFont("Helvetica-Bold", 12)
        c.drawRightString(560, 790, t["supplier"][0])
        c.setFont("Helvetica", 8)
        for i, text in enumerate(t["supplier"][1:]):
            c.drawRightString(560, 776 - i * 11, text)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(40, 720, t["title"])
        _draw_lines(c, 40, 702, meta_lines)
        y = _draw_table(c, layout, t["header"], t["rows"], 620)
        _draw_lines(c, 40, y, total_lines, 10)


def _draw_layout_d(c: canvas.Canvas, inv: InvoiceGroundTruth, supplier: Supplier) -> None:
    """Rows read 'qty unit x description at price | VAT rate | amount'; labels use other words."""
    lang = inv.language
    lab = LABELS_D[lang]
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(A4[0] / 2, 800, LABELS[lang][inv.document_type])
    c.setFont("Helvetica", 9)
    c.drawCentredString(A4[0] / 2, 784, f"{supplier.name} - {supplier.address}")
    c.drawCentredString(A4[0] / 2, 772, f"{supplier.org_number} / {supplier.iban}")
    meta = [
        f"{lab['number']} {inv.number}",
        f"{lab['invoice_date']} {format_date(inv.invoice_date, lang)}",
        f"{lab['delivery_date']} {format_date(inv.delivery_date, lang)}",
    ]
    if inv.delivery_note_id:
        meta.append(f"{lab['note']} {inv.delivery_note_id}")
    if inv.credits_invoice_number:
        meta.append(f"{lab['credits']} {inv.credits_invoice_number}")
    _draw_lines(c, 50, 740, meta)
    _draw_lines(c, 380, 740, [catalog.BUYER["name"], catalog.BUYER["address"]])
    y = 660
    for line in inv.lines:
        unit = UNIT_LABELS[lang][line.unit]
        c.drawString(
            50,
            y,
            f"{format_qty(line.qty, lang)} {unit} x {line.description} "
            f"@ {format_number(line.unit_price, lang)} | {lab['vat']} {format_rate(line.vat_rate)}",
        )
        c.drawRightString(550, y, format_number(line.line_total, lang))
        y -= 16
    totals = [
        (lab["excl"], format_number(inv.total_excl_vat, lang)),
        (lab["vat_total"], format_number(inv.vat_total, lang)),
        (lab["incl"], "SEK " + format_number(inv.total_incl_vat, lang)),
    ]
    for label, value in totals:
        y -= 14
        c.drawString(330, y, label)
        c.drawRightString(550, y, value)


def render_pdf(
    inv: InvoiceGroundTruth, supplier: Supplier, noise_angle: float = 0.0, mode: str = "normal"
) -> bytes:
    """Return the PDF bytes for one invoice.

    `mode`: normal, blurred_image_only, truncated_file, or image_only_readable (a clean scan:
    the normal page rasterised, so it has no text layer but is easy to read by eye).
    """
    if mode == "image_only_readable":
        return _scanned(render_pdf(inv, supplier, noise_angle, "normal"), inv)
    t = _text_rows(inv, supplier)
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    c.setTitle(f"{t['title']} {inv.number}")
    if mode == "blurred_image_only":
        c.drawImage(ImageReader(_blurred_image(t)), 0, 0, width=A4[0], height=A4[1])
    else:
        if noise_angle:
            # Scan-like noise: rotate the page content slightly around the page centre.
            c.translate(A4[0] / 2, A4[1] / 2)
            c.rotate(noise_angle)
            c.translate(-A4[0] / 2 + 6, -A4[1] / 2 - 4)
        if inv.layout == "D":
            _draw_layout_d(c, inv, supplier)
        else:
            _draw_page(c, inv.layout, t)
    c.showPage()
    c.save()
    data = buffer.getvalue()
    if mode == "truncated_file":
        return data[: len(data) * 3 // 5]
    return data


def _scanned(pdf_bytes: bytes, inv: InvoiceGroundTruth) -> bytes:
    """Rasterise a PDF page at 150 dpi in greyscale and wrap the image in a new PDF."""
    document = pdfium.PdfDocument(pdf_bytes)
    image = document[0].render(scale=150 / 72, grayscale=True).to_pil()
    document.close()
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    c.setTitle(f"scan {inv.number}")
    c.drawImage(ImageReader(image), 0, 0, width=A4[0], height=A4[1])
    c.showPage()
    c.save()
    return buffer.getvalue()


def _blurred_image(t: dict) -> Image.Image:
    """A low-resolution, blurred 'photo' of the invoice text, with no text layer."""
    image = Image.new("L", (595, 842), color=255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    lines = [t["title"], *t["supplier"], ""]
    lines += [f"{k}: {v}" for k, v in t["meta"]] + [""]
    lines += ["  ".join(row) for row in t["rows"]] + [""]
    lines += [f"{k}: {v}" for k, v in t["totals"]]
    for i, text in enumerate(lines):
        draw.text((40, 40 + i * 16), text, fill=0, font=font)
    return image.filter(ImageFilter.GaussianBlur(radius=2.2))
