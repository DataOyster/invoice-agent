"""Tests for the synthetic dataset generator (ROADMAP task 2)."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

import pdfplumber
import pytest
from pdfplumber.utils.exceptions import PdfminerException
from pydantic import ValidationError

from invoice_agent.data_gen import catalog
from invoice_agent.data_gen.generator import GeneratorConfig, find_price, generate
from invoice_agent.data_gen.models import ANOMALY_TYPES, InvoiceGroundTruth
from invoice_agent.data_gen.vat import vat_rate_for, vat_rule_for
from invoice_agent.data_gen.writer import write_dataset

APRIL_1 = date(2026, 4, 1)

# Real Nordic food-service and food companies. Generated names must not contain any of them.
REAL_COMPANY_DENYLIST = (
    "Menigo",
    "Martin & Servera",
    "Martin och Servera",
    "Svensk Cater",
    "Sysco",
    "Arla",
    "ICA",
    "Axfood",
    "Dagab",
    "Coop",
    "Lidl",
    "Metro",
    "Systembolaget",
    "Findus",
    "Lantmännen",
    "Orkla",
    "Barilla",
    "Scandi Standard",
    "Fiskeriet",
    "Servera",
)


def _files_digest(folder: Path) -> dict[str, str]:
    return {
        str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


@pytest.fixture(scope="module")
def dataset():
    return generate(GeneratorConfig(seed=42))


@pytest.fixture(scope="module")
def written(tmp_path_factory, dataset):
    out = tmp_path_factory.mktemp("dataset_a")
    write_dataset(dataset, out)
    return out


@pytest.fixture(scope="module")
def invoices(dataset):
    return dataset.invoices


# ---------------------------------------------------------------- determinism


def test_same_seed_gives_byte_identical_files(tmp_path, written):
    write_dataset(generate(GeneratorConfig(seed=42)), tmp_path)
    assert _files_digest(tmp_path) == _files_digest(written)


def test_different_seed_gives_different_ground_truth(tmp_path, written):
    write_dataset(generate(GeneratorConfig(seed=7)), tmp_path)
    other = (tmp_path / "ground_truth.jsonl").read_bytes()
    assert other != (written / "ground_truth.jsonl").read_bytes()


# ---------------------------------------------------------------- schema


def test_every_ground_truth_line_validates_against_schema(written):
    lines = (written / "ground_truth.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 100
    for line in lines:
        InvoiceGroundTruth.model_validate_json(line)


def test_schema_rejects_inconsistent_totals(invoices):
    record = json.loads(invoices[0].model_dump_json())
    record["total_incl_vat"] += 10
    with pytest.raises(ValidationError):
        InvoiceGroundTruth.model_validate(record)


def test_schema_rejects_unknown_fields(invoices):
    record = json.loads(invoices[0].model_dump_json())
    record["approved"] = True
    with pytest.raises(ValidationError):
        InvoiceGroundTruth.model_validate(record)


# ---------------------------------------------------------------- quotas and coverage


def test_every_anomaly_type_appears_with_its_quota(invoices):
    counts = Counter(a.type for inv in invoices for a in inv.anomalies)
    assert counts == {t: 5 for t in ANOMALY_TYPES}


def test_error_share_is_within_configured_range(invoices):
    share = sum(1 for inv in invoices if inv.anomalies) / len(invoices)
    assert 0.20 <= share <= 0.30


def test_quotas_hold_for_other_seeds():
    for seed in (1, 2, 3):
        counts = Counter(
            a.type for inv in generate(GeneratorConfig(seed=seed)).invoices for a in inv.anomalies
        )
        assert counts == {t: 5 for t in ANOMALY_TYPES}


def test_all_languages_and_layouts_appear(invoices):
    assert {inv.language for inv in invoices} == {"sv", "en", "it"}
    assert {inv.layout for inv in invoices} == {"A", "B", "C"}


# ---------------------------------------------------------------- trap cases


def test_vat_trap_cases_exist_and_charge_12_percent_on_food(invoices):
    traps = [inv for inv in invoices if inv.is_trap and "2026-04-01" in inv.trap_reason]
    assert len(traps) >= 4
    for inv in traps:
        assert inv.delivery_date < APRIL_1 <= inv.invoice_date
        assert not inv.anomalies
        food = [ln for ln in inv.lines if ln.product_id in _food_ids()]
        assert food and all(ln.vat_rate == 0.12 for ln in food)


def test_price_traps_are_within_tolerance(dataset, invoices):
    traps = [inv for inv in invoices if inv.is_trap and "tolerance" in inv.trap_reason]
    assert len(traps) == 4
    for inv in traps:
        deviations = [_price_deviation(dataset, inv, ln) for ln in inv.lines]
        assert any(d != 0 for d in deviations)
        assert all(abs(d) <= 0.005 for d in deviations)


def test_credit_notes_are_legitimate_traps(invoices):
    credit_notes = [inv for inv in invoices if inv.document_type == "credit_note"]
    assert len(credit_notes) == 2
    numbers = {(inv.supplier_id, inv.number) for inv in invoices if inv.document_type == "invoice"}
    for cn in credit_notes:
        assert cn.is_trap and not cn.anomalies
        assert cn.total_incl_vat < 0
        assert (cn.supplier_id, cn.credits_invoice_number) in numbers


# ---------------------------------------------------------------- anomalies are real anomalies


def test_price_anomalies_exceed_tolerance(dataset, invoices):
    for inv, a in _anomalies(invoices, "price_above_agreement"):
        deviation = _price_deviation(dataset, inv, inv.lines[a.line_index])
        assert deviation >= 0.03
        assert a.found > a.expected


def test_unit_confusion_charges_case_price_per_kg(dataset, invoices):
    products = {p.product_id: p for p in dataset.products}
    for inv, a in _anomalies(invoices, "unit_confusion"):
        line = inv.lines[a.line_index]
        product = products[line.product_id]
        assert line.unit == "kg"
        assert line.unit_price == pytest.approx(a.expected * product.pack_weight_kg, abs=0.01)


def test_quantity_anomalies_differ_from_delivery_note(dataset, invoices):
    notes = {n.note_id: n for n in dataset.delivery_notes}
    for inv, a in _anomalies(invoices, "quantity_mismatch"):
        line = inv.lines[a.line_index]
        delivered = {ln.product_id: ln.qty for ln in notes[a.evidence.ref].lines}
        assert abs(line.qty - delivered[line.product_id]) > 0.01 * delivered[line.product_id]


def test_wrong_vat_anomalies_differ_from_vat_table(invoices):
    products = {p.product_id: p for p in catalog.PRODUCTS}
    variants = set()
    for inv, a in _anomalies(invoices, "wrong_vat"):
        due = [
            vat_rate_for(products[ln.product_id].vat_category, inv.delivery_date)
            for ln in inv.lines
        ]
        assert any(ln.vat_rate != rate for ln, rate in zip(inv.lines, due))
        variants.add(a.detail.split(" ")[0])
    assert len(variants) == 3  # invoice-date rule, old food rate, alcohol at reduced rate


def test_clean_lines_match_price_list_vat_table_and_delivery_note(dataset, invoices):
    """Everything not labelled as an anomaly or price trap must be consistent with the reference
    data; otherwise the evaluation would count correct flags as false positives."""
    products = {p.product_id: p for p in dataset.products}
    notes = {n.note_id: n for n in dataset.delivery_notes}
    for inv in invoices:
        anomaly = inv.anomalies[0] if inv.anomalies else None
        if inv.is_trap and "tolerance" in inv.trap_reason:
            continue
        for i, line in enumerate(inv.lines):
            if anomaly and anomaly.type != "duplicate" and anomaly.line_index in (i, None):
                continue
            assert _price_deviation(dataset, inv, line) == 0, (inv.invoice_id, i)
            due = vat_rate_for(products[line.product_id].vat_category, inv.delivery_date)
            assert line.vat_rate == due, (inv.invoice_id, i)
            if inv.document_type == "invoice":
                delivered = {ln.product_id: ln.qty for ln in notes[inv.delivery_note_id].lines}
                assert line.qty == delivered[line.product_id], (inv.invoice_id, i)


# ---------------------------------------------------------------- duplicates


def test_duplicates_point_to_an_earlier_original(invoices):
    by_id = {inv.invoice_id: inv for inv in invoices}
    kinds = set()
    for dup, a in _anomalies(invoices, "duplicate"):
        original = by_id[a.evidence.ref]
        assert original.invoice_id < dup.invoice_id
        assert original.supplier_id == dup.supplier_id and not original.anomalies
        same_number = original.number == dup.number
        same_date_total = (
            original.invoice_date == dup.invoice_date
            and original.total_incl_vat == dup.total_incl_vat
        )
        assert same_number or same_date_total
        kinds.add("number" if same_number else "date_total")
    assert kinds == {"number", "date_total"}


def test_no_accidental_duplicates_among_non_duplicates(invoices):
    docs = [
        inv
        for inv in invoices
        if inv.document_type == "invoice" and not any(a.type == "duplicate" for a in inv.anomalies)
    ]
    numbers = Counter((inv.supplier_id, inv.number) for inv in docs)
    date_totals = Counter((inv.supplier_id, inv.invoice_date, inv.total_incl_vat) for inv in docs)
    assert max(numbers.values()) == 1
    assert max(date_totals.values()) == 1


# ---------------------------------------------------------------- references and identifiers


def test_every_evidence_and_clause_reference_exists(dataset, invoices):
    ids = {
        "price_list": {e.entry_id for e in dataset.price_list},
        "delivery_note": {n.note_id for n in dataset.delivery_notes},
        "vat_rule": {r.rule_id for r in dataset.vat_table},
        "invoice": {inv.invoice_id for inv in invoices},
    }
    clause_ids = {c.clause_id for clauses in dataset.contracts.values() for c in clauses}
    for inv in invoices:
        for a in inv.anomalies:
            assert a.evidence.ref in ids[a.evidence.kind]
            if a.type == "wrong_vat":
                assert a.clause_ref is None
            else:
                assert a.clause_ref in clause_ids
                assert a.clause_ref.startswith(inv.supplier_id)


def test_delivery_dates_follow_contract_weekdays(invoices):
    for inv in invoices:
        assert inv.delivery_date.weekday() in catalog.DELIVERY_WEEKDAYS[inv.supplier_id]


def test_no_generated_name_matches_a_real_company(dataset):
    names = [s.name for s in dataset.suppliers] + [catalog.BUYER["name"]]
    for name in names:
        for real in REAL_COMPANY_DENYLIST:
            assert not re.search(rf"\b{re.escape(real)}\b", name, re.IGNORECASE), (name, real)


def test_identifiers_are_impossible_on_purpose(dataset):
    for s in dataset.suppliers:
        assert s.org_number.startswith("000000-")
        assert s.iban.startswith("SE00 ")  # check digits 00 are never valid


# ---------------------------------------------------------------- VAT table


@pytest.mark.parametrize(
    ("category", "day", "rate"),
    [
        ("food", date(2026, 3, 31), 0.12),
        ("food", date(2026, 4, 1), 0.06),
        ("food", date(2027, 12, 31), 0.06),
        ("alcohol", date(2026, 3, 31), 0.25),
        ("alcohol", date(2026, 6, 1), 0.25),
        ("non_food", date(2026, 6, 1), 0.25),
    ],
)
def test_vat_rate_depends_on_category_and_delivery_date(category, day, rate):
    assert vat_rate_for(category, day) == rate


def test_vat_lookup_refuses_dates_the_spec_does_not_cover():
    with pytest.raises(LookupError):
        vat_rule_for("food", date(2028, 1, 1))


# ---------------------------------------------------------------- PDFs


def test_readable_pdfs_contain_their_invoice_number(written, invoices):
    for inv in invoices:
        if not inv.readable:
            continue
        with pdfplumber.open(written / inv.file) as pdf:
            text = pdf.pages[0].extract_text()
        assert inv.number in text, inv.invoice_id


def test_unreadable_pdfs_have_no_usable_text(written, invoices):
    unreadable = [inv for inv in invoices if not inv.readable]
    assert {inv.unreadable_reason for inv in unreadable} == {"blurred_image_only", "truncated_file"}
    for inv in unreadable:
        if inv.unreadable_reason == "truncated_file":
            with pytest.raises(PdfminerException), pdfplumber.open(written / inv.file) as pdf:
                pdf.pages[0].extract_text()
        else:  # blurred image: the file opens but has no text layer
            with pdfplumber.open(written / inv.file) as pdf:
                assert (pdf.pages[0].extract_text() or "").strip() == ""


# ---------------------------------------------------------------- helpers


def _food_ids() -> set[str]:
    return {p.product_id for p in catalog.PRODUCTS if p.vat_category == "food"}


def _anomalies(invoices, anomaly_type):
    return [(inv, a) for inv in invoices for a in inv.anomalies if a.type == anomaly_type]


def _price_deviation(dataset, inv, line) -> float:
    agreed = find_price(dataset.price_list, line.product_id, inv.delivery_date).unit_price
    return round(line.unit_price / agreed - 1, 6)
