"""The rule baseline and the rule extractor on the generated dataset."""

from __future__ import annotations

import pytest

from invoice_agent.evals.baseline import RuleBaseline, ground_truth_as_extracted, run_baseline
from invoice_agent.evals.metrics import detection_metrics, extraction_accuracy
from invoice_agent.evals.reference import load_reference
from invoice_agent.evals.run_eval import REVIEW_THRESHOLD, build_report
from invoice_agent.extraction.rules import extract_pdf


@pytest.fixture(scope="module")
def ref(generated_dir):
    return load_reference(generated_dir)


@pytest.fixture(scope="module")
def pdf_reads(ref, generated_dir):
    return {
        g.invoice_id: extract_pdf(generated_dir / g.file, g.invoice_id) for g in ref.ground_truth
    }


def _oracle(ref):
    return [ground_truth_as_extracted(g, ref) for g in ref.ground_truth if g.readable]


def test_baseline_on_oracle_input_finds_every_anomaly_and_no_trap(ref):
    report = detection_metrics(run_baseline(ref, _oracle(ref), REVIEW_THRESHOLD), ref.ground_truth)
    assert report.overall.recall == 1.0
    assert report.overall.precision == 1.0
    assert report.trap_false_alarms == []


def _standard(ref):
    return [g for g in ref.ground_truth if g.split == "standard"]


def test_rule_extractor_meets_the_key_field_threshold_on_the_standard_split(ref, pdf_reads):
    report = extraction_accuracy(pdf_reads, _standard(ref), REVIEW_THRESHOLD)
    assert report.key_field_accuracy >= 0.95


def test_every_unreadable_pdf_goes_to_manual_review(ref, pdf_reads):
    report = extraction_accuracy(pdf_reads, ref.ground_truth, REVIEW_THRESHOLD)
    assert report.unreadable_caught == report.unreadable_total == 4


def test_low_confidence_reads_are_not_checked(ref):
    invoice = _oracle(ref)[0].model_copy(update={"confidence": 0.5})
    assert run_baseline(ref, [invoice], REVIEW_THRESHOLD) == []


def test_price_tolerance_boundary(ref):
    """0.5% above the agreed price is tolerated, 2% above is flagged."""
    clean = next(
        g
        for g in ref.ground_truth
        if g.readable and not g.anomalies and not g.is_trap and g.document_type == "invoice"
    )
    base = ground_truth_as_extracted(clean, ref)
    for factor, expect_flag in ((1.005, False), (1.02, True)):
        line = base.lines[0].model_copy(
            update={"unit_price": round(base.lines[0].unit_price * factor, 2)}
        )
        invoice = base.model_copy(update={"lines": [line, *base.lines[1:]]})
        flags = RuleBaseline(ref).check(invoice)
        assert any(f.type == "price_above_agreement" for f in flags) == expect_flag, factor


def test_vat_follows_delivery_date_not_invoice_date(ref):
    trap = next(g for g in ref.ground_truth if g.is_trap and "2026-04-01" in (g.trap_reason or ""))
    flags = RuleBaseline(ref).check(ground_truth_as_extracted(trap, ref))
    assert not any(f.type == "wrong_vat" for f in flags)


def test_every_flag_cites_existing_evidence(ref):
    ids = {e.entry_id for e in ref.price_list} | {n.note_id for n in ref.delivery_notes}
    ids |= {r.rule_id for r in ref.vat_table} | {g.invoice_id for g in ref.ground_truth}
    clause_ids = {c.clause_id for c in ref.clauses}
    for flag in run_baseline(ref, _oracle(ref), REVIEW_THRESHOLD):
        assert flag.evidence.ref in ids
        assert flag.clause_ref is None or flag.clause_ref in clause_ids


def test_report_is_reproducible(generated_dir):
    first = build_report(generated_dir)
    assert first == build_report(generated_dir)
    assert "synthetic" in first and "## Hard split" in first
