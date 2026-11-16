"""Parsing rules of the pdfplumber extractor, on small text samples."""

from __future__ import annotations

from datetime import date

import pytest

from invoice_agent.extraction.rules import extract_text_fields, parse_date, parse_number


@pytest.mark.parametrize(
    ("text", "language", "value"),
    [
        ("1 234,50", "sv", 1234.5),
        ("1.234,50", "it", 1234.5),
        ("1,234.50", "en", 1234.5),
        ("-952.00", "en", -952.0),
        ("28,5", "sv", 28.5),
    ],
)
def test_parse_number_in_three_locales(text, language, value):
    assert parse_number(text, language) == value


@pytest.mark.parametrize(
    ("text", "language"),
    [("2026-04-09", "sv"), ("09/04/2026", "it"), ("9 Apr 2026", "en")],
)
def test_parse_date_in_three_locales(text, language):
    assert parse_date(text, language) == date(2026, 4, 9)


SWEDISH = """FAKTURA
Kallgrund Fiskhandel AB
Org.nr 000000-0001 Fakturanummer: KF-2026-0138
Fakturadatum: 2026-01-14
Leveransdatum: 2026-01-13
Följesedel: DN-0003
Artikel Antal Enhet À-pris Moms Belopp
Stenbitsrom 100 g 10 st 34,50 12% 345,00
Fiskfond 29,5 l 59,00 12% 1 740,50
Summa exkl. moms: 2 085,50
Momsbelopp: 250,26
Att betala: 2 335,76 SEK
"""


def test_swedish_invoice_is_read_with_full_confidence():
    inv = extract_text_fields("INV-X", SWEDISH)
    assert inv.problems == [] and inv.confidence == 1.0
    assert inv.number == "KF-2026-0138"
    assert inv.delivery_date == date(2026, 1, 13)
    assert inv.supplier_org_number == "000000-0001"
    assert [x.description for x in inv.lines] == ["Stenbitsrom 100 g", "Fiskfond"]
    assert inv.lines[0].unit == "pcs" and inv.lines[1].line_total == 1740.5


def test_description_on_its_own_line_is_recovered():
    noisy = SWEDISH.replace("Fiskfond 29,5 l", "Fiskfond\n29,5 l")
    inv = extract_text_fields("INV-X", noisy)
    assert [x.description for x in inv.lines] == ["Stenbitsrom 100 g", "Fiskfond"]


def test_arithmetic_that_does_not_add_up_lowers_confidence():
    inv = extract_text_fields(
        "INV-X", SWEDISH.replace("Att betala: 2 335,76", "Att betala: 2 999,00")
    )
    assert inv.confidence < 1.0
    assert "total excl. VAT + VAT != total to pay" in inv.problems


def test_a_dropped_line_is_caught_by_the_sum_check():
    inv = extract_text_fields("INV-X", SWEDISH.replace("Fiskfond 29,5 l 59,00 12% 1 740,50\n", ""))
    assert "sum of lines != total excl. VAT" in inv.problems


def test_text_without_invoice_title_gets_zero_confidence():
    assert extract_text_fields("INV-X", "hello world").confidence == 0.0
