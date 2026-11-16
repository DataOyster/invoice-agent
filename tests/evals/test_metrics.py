"""Metric arithmetic on small hand-made examples, so every number in the report can be trusted."""

from __future__ import annotations

from datetime import date

import pytest

from invoice_agent.data_gen.models import Anomaly, Evidence, InvoiceGroundTruth, InvoiceLine
from invoice_agent.evals.metrics import (
    GuardrailResult,
    detection_metrics,
    extraction_accuracy,
    guardrail_pass_rate,
    hit_at_k,
)
from invoice_agent.schemas import ExtractedInvoice, ExtractedLine, Flag

EV = Evidence(kind="price_list", ref="PL-P101-1")


def _gt(invoice_id, anomaly_type=None, trap=False, readable=True, price=10.0):
    line = InvoiceLine(
        product_id="P101",
        description="x",
        qty=2,
        unit="kg",
        unit_price=price,
        vat_rate=0.06,
        line_total=round(2 * price, 2),
    )
    anomalies = []
    if anomaly_type:
        anomalies = [
            Anomaly(
                type=anomaly_type,
                line_index=0,
                evidence=EV,
                clause_ref=None,
                expected=1,
                found=2,
                detail="",
            )
        ]
    total = line.line_total
    vat = round(total * 0.06, 2)
    return InvoiceGroundTruth(
        invoice_id=invoice_id,
        file=f"invoices/{invoice_id}.pdf",
        language="en",
        layout="A",
        document_type="invoice",
        supplier_id="S01",
        number=f"N-{invoice_id}",
        invoice_date=date(2026, 5, 2),
        delivery_date=date(2026, 5, 1),
        delivery_note_id="DN-1",
        lines=[line],
        total_excl_vat=total,
        vat_total=vat,
        total_incl_vat=round(total + vat, 2),
        anomalies=anomalies,
        is_trap=trap,
        trap_reason="trap" if trap else None,
        readable=readable,
        unreadable_reason=None if readable else "truncated_file",
    )


def _flag(invoice_id, type_, line_index=0):
    return Flag(invoice_id=invoice_id, type=type_, line_index=line_index, evidence=EV, message="")


def test_precision_and_recall_per_type():
    truth = [_gt("I1", "price_above_agreement"), _gt("I2", "price_above_agreement"), _gt("I3")]
    flags = [_flag("I1", "price_above_agreement"), _flag("I3", "price_above_agreement")]
    score = detection_metrics(flags, truth).per_type["price_above_agreement"]
    assert (score.true_positives, score.false_positives, score.false_negatives) == (1, 1, 1)
    assert score.precision == 0.5
    assert score.recall == 0.5


def test_repeated_flags_of_one_type_on_one_invoice_count_once():
    truth = [_gt("I1", "wrong_vat")]
    flags = [_flag("I1", "wrong_vat", 0), _flag("I1", "wrong_vat", 1)]
    score = detection_metrics(flags, truth).per_type["wrong_vat"]
    assert (score.true_positives, score.false_positives) == (1, 0)


def test_flag_of_the_wrong_type_is_both_a_false_positive_and_a_miss():
    report = detection_metrics(
        [_flag("I1", "price_above_agreement")], [_gt("I1", "unit_confusion")]
    )
    assert report.per_type["price_above_agreement"].false_positives == 1
    assert report.per_type["unit_confusion"].false_negatives == 1


def test_flagged_trap_is_reported():
    report = detection_metrics([_flag("T1", "wrong_vat")], [_gt("T1", trap=True)])
    assert report.trap_false_alarms == ["T1"]
    assert report.overall.precision == 0.0


def test_types_with_no_data_report_none_not_zero():
    score = detection_metrics([], [_gt("I1")]).per_type["duplicate"]
    assert score.precision is None and score.recall is None


def _pred(gt, **changes):
    values = {
        "invoice_id": gt.invoice_id,
        "number": gt.number,
        "invoice_date": gt.invoice_date,
        "total_incl_vat": gt.total_incl_vat,
        "confidence": 1.0,
        "lines": [
            ExtractedLine(
                description="x",
                qty=x.qty,
                unit=x.unit,
                unit_price=x.unit_price,
                vat_rate=x.vat_rate,
                line_total=x.line_total,
            )
            for x in gt.lines
        ],
    }
    values.update(changes)
    return ExtractedInvoice(**values)


def test_extraction_accuracy_counts_key_fields():
    good, bad = _gt("I1"), _gt("I2")
    predictions = {"I1": _pred(good), "I2": _pred(bad, number="WRONG", total_incl_vat=1.0)}
    report = extraction_accuracy(predictions, [good, bad], review_threshold=0.9)
    assert report.fields["number"].accuracy == 0.5
    assert report.fields["invoice_date"].accuracy == 1.0
    assert report.fields["total_incl_vat"].accuracy == 0.5
    assert report.key_field_accuracy == pytest.approx(6 / 8)


def test_missing_prediction_is_wrong_and_goes_to_review():
    report = extraction_accuracy({}, [_gt("I1")], review_threshold=0.9)
    assert report.key_field_accuracy == 0.0
    assert report.manual_review == 1


def test_unreadable_documents_are_scored_on_review_routing_only():
    unreadable = _gt("U1", readable=False)
    low = ExtractedInvoice(invoice_id="U1", confidence=0.0)
    report = extraction_accuracy({"U1": low}, [unreadable], review_threshold=0.9)
    assert report.unreadable_caught == 1 and report.unreadable_total == 1
    assert report.key_field_accuracy is None  # no readable document to score


def test_hit_at_3():
    results = {"q1": ["c9", "c1", "c2"], "q2": ["c1", "c2", "c3", "c4"]}
    expected = {"q1": "c1", "q2": "c4"}
    assert hit_at_k(results, expected, k=3) == 0.5


def test_guardrail_pass_rate_by_severity():
    results = [
        GuardrailResult("a", "never", True),
        GuardrailResult("b", "never", False),
        GuardrailResult("c", "other", True),
    ]
    assert guardrail_pass_rate(results) == {"never": 0.5, "other": 1.0}
