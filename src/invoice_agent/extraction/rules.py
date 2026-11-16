"""Rule-based extractor: pdfplumber text + regular expressions, no AI.

This is the baseline extractor for task 3 and the agreed fallback for task 5. It knows the three
languages' labels and number formats. Its confidence comes from a self-check: the document's own
arithmetic (line totals, sum of lines, VAT, total to pay) must add up. Anything that does not add
up lowers confidence, and low confidence sends the invoice to manual review.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pdfplumber
from pdfplumber.utils.exceptions import PdfminerException

from invoice_agent.schemas import ExtractedInvoice, ExtractedLine

MONEY_TOLERANCE = 0.011

TITLES = {
    "KREDITFAKTURA": ("sv", "credit_note"),
    "FAKTURA": ("sv", "invoice"),
    "CREDIT NOTE": ("en", "credit_note"),
    "INVOICE": ("en", "invoice"),
    "NOTA DI CREDITO": ("it", "credit_note"),
    "FATTURA": ("it", "invoice"),
}

LABELS = {
    "sv": {
        "number": "Fakturanummer",
        "invoice_date": "Fakturadatum",
        "delivery_date": "Leveransdatum",
        "note": "Följesedel",
        "credits": "Avser faktura",
        "excl": "Summa exkl. moms",
        "vat_total": "Momsbelopp",
        "incl": "Att betala",
    },
    "en": {
        "number": "Invoice number",
        "invoice_date": "Invoice date",
        "delivery_date": "Delivery date",
        "note": "Delivery note",
        "credits": "Credits invoice",
        "excl": "Total excl. VAT",
        "vat_total": "VAT amount",
        "incl": "Total to pay",
    },
    "it": {
        "number": "Numero fattura",
        "invoice_date": "Data fattura",
        "delivery_date": "Data consegna",
        "note": "DDT",
        "credits": "Rif. fattura",
        "excl": "Totale imponibile",
        "vat_total": "Totale IVA",
        "incl": "Totale da pagare",
    },
}

UNITS = {
    "kg": "kg",
    "l": "l",
    "st": "pcs",
    "pcs": "pcs",
    "pz": "pcs",
    "fl": "bottle",
    "btl": "bottle",
    "bott.": "bottle",
}

MONTHS_EN = {
    m: i
    for i, m in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
        start=1,
    )
}

_AMOUNT = r"-?\d[\d .,]*\d|-?\d"
# One table row: description, quantity, unit, unit price, VAT %, amount. The description is lazy
# and may be empty (on noisy scans it can land on the line above or below).
ROW = re.compile(
    rf"^(?P<desc>.*?)\s*(?P<qty>-?\d+(?:[.,]\d+)?) (?P<unit>kg|l|st|pcs|pz|fl|btl|bott\.) "
    rf"(?P<price>{_AMOUNT}) (?P<rate>\d{{1,2}})% (?P<amount>{_AMOUNT})$"
)
ORG_NUMBER = re.compile(r"\b(\d{6}-\d{4})\b")


def parse_number(text: str, language: str) -> float:
    """'1 234,50' (sv), '1.234,50' (it), '1,234.50' (en) -> 1234.5"""
    t = text.replace(" ", "")
    if language in ("sv", "it"):
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", "")
    return float(t)


def parse_date(text: str, language: str) -> date:
    text = text.strip()
    if language == "sv":
        return date.fromisoformat(text)
    if language == "it":
        day, month, year = text.split("/")
        return date(int(year), int(month), int(day))
    day, month, year = text.split()
    return date(int(year), MONTHS_EN[month], int(day))


def _labelled(text: str, label: str, value: str = r"\S+") -> str | None:
    match = re.search(rf"{re.escape(label)}:\s*({value})", text)
    return match.group(1).strip() if match else None


def _detect(text: str) -> tuple[str, str] | None:
    for title, result in TITLES.items():  # longer titles first where they overlap
        if re.search(rf"(^|\s){title}($|\s)", text, re.MULTILINE):
            return result
    return None


def _parse_lines(text: str, language: str) -> list[ExtractedLine]:
    raw = [line.strip() for line in text.splitlines()]
    lines: list[ExtractedLine] = []
    used: set[int] = set()
    for i, line in enumerate(raw):
        match = ROW.match(line)
        if not match:
            continue
        desc = match.group("desc").strip()
        if not desc:  # noisy scan: take the neighbouring line that is not itself a row
            for j in (i - 1, i + 1):
                if 0 <= j < len(raw) and j not in used and raw[j] and not ROW.match(raw[j]):
                    desc = raw[j]
                    used.add(j)
                    break
        used.add(i)
        lines.append(
            ExtractedLine(
                description=desc,
                qty=parse_number(match.group("qty"), language),
                unit=UNITS[match.group("unit")],
                unit_price=parse_number(match.group("price"), language),
                vat_rate=int(match.group("rate")) / 100,
                line_total=parse_number(match.group("amount"), language),
            )
        )
    return lines


def extract_text_fields(invoice_id: str, text: str) -> ExtractedInvoice:
    """Parse already-extracted PDF text. Separate from file reading so it is easy to test."""
    detected = _detect(text)
    if detected is None:
        return ExtractedInvoice(
            invoice_id=invoice_id, confidence=0.0, problems=["no readable invoice title"]
        )
    language, document_type = detected
    lab = LABELS[language]
    problems: list[str] = []

    def field(name: str, parser, value_pattern: str = r"\S+"):
        raw = _labelled(text, lab[name], value_pattern)
        if raw is None:
            return None
        try:
            return parser(raw)
        except ValueError:
            problems.append(f"cannot parse {name}: {raw!r}")
            return None

    date_pattern = r"\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}|\d{1,2} [A-Z][a-z]{2} \d{4}"
    org = ORG_NUMBER.search(text)
    result = ExtractedInvoice(
        invoice_id=invoice_id,
        language=language,
        document_type=document_type,
        supplier_org_number=org.group(1) if org else None,
        number=field("number", str),
        invoice_date=field("invoice_date", lambda s: parse_date(s, language), date_pattern),
        delivery_date=field("delivery_date", lambda s: parse_date(s, language), date_pattern),
        delivery_note_id=field("note", str, r"DN-\d+"),
        credits_invoice_number=field("credits", str),
        lines=_parse_lines(text, language),
        total_excl_vat=field("excl", lambda s: parse_number(s, language), _AMOUNT),
        vat_total=field("vat_total", lambda s: parse_number(s, language), _AMOUNT),
        total_incl_vat=field("incl", lambda s: parse_number(s, language), _AMOUNT),
        confidence=1.0,
    )
    problems += _self_check(result)
    result.problems = problems
    result.confidence = max(0.0, 1.0 - 0.25 * len(problems))
    return result


def _self_check(inv: ExtractedInvoice) -> list[str]:
    """The invoice's own arithmetic must add up; every mismatch is a reason to doubt the read."""
    problems = []
    for name in (
        "supplier_org_number",
        "number",
        "invoice_date",
        "delivery_date",
        "total_excl_vat",
        "vat_total",
        "total_incl_vat",
    ):
        if getattr(inv, name) is None:
            problems.append(f"missing {name}")
    if not inv.lines:
        problems.append("no invoice lines found")
    for i, line in enumerate(inv.lines):
        if not line.description:
            problems.append(f"line {i}: no description")
        if abs(round(line.qty * line.unit_price, 2) - line.line_total) > MONEY_TOLERANCE:
            problems.append(f"line {i}: qty x price != amount")
    lines_sum = sum(x.line_total for x in inv.lines)
    if inv.total_excl_vat is not None and abs(lines_sum - inv.total_excl_vat) > MONEY_TOLERANCE:
        problems.append("sum of lines != total excl. VAT")
    totals = (inv.total_excl_vat, inv.vat_total, inv.total_incl_vat)
    if None not in totals and abs(totals[0] + totals[1] - totals[2]) > MONEY_TOLERANCE:
        problems.append("total excl. VAT + VAT != total to pay")
    return problems


def extract_pdf(path: Path, invoice_id: str) -> ExtractedInvoice:
    try:
        with pdfplumber.open(path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except PdfminerException as error:
        return ExtractedInvoice(
            invoice_id=invoice_id, confidence=0.0, problems=[f"PDF cannot be parsed: {error}"]
        )
    if not text.strip():
        return ExtractedInvoice(
            invoice_id=invoice_id, confidence=0.0, problems=["no text layer (image-only scan)"]
        )
    return extract_text_fields(invoice_id, text)
