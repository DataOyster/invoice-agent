"""Evaluation metrics. Pure functions: predictions + ground truth in, numbers out.

Matching rule for anomaly detection: a flag is correct when the ground truth of the same invoice
contains an anomaly of the same type. Several flags of one type on one invoice count once
(the reviewer sees one problem). The line index is reported but not required to match, because
some anomalies (e.g. a wrong VAT rate on every food line) concern the whole invoice.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from invoice_agent.data_gen.models import ANOMALY_TYPES, InvoiceGroundTruth
from invoice_agent.schemas import ExtractedInvoice, Flag

# Starting thresholds from docs/SPEC.md section 4 (confirmed defaults: 95 / 90 / 85).
KEY_FIELDS = ("number", "invoice_date", "total_incl_vat", "unit_price")
EXTRACTION_THRESHOLD = 0.95
RECALL_THRESHOLDS = {
    "price_above_agreement": 0.90,
    "duplicate": 0.90,
    "wrong_vat": 0.90,
    "quantity_mismatch": 0.80,
    "unit_confusion": 0.80,
}
PRECISION_THRESHOLD = 0.85
RETRIEVAL_THRESHOLD = 0.90
GUARDRAIL_THRESHOLDS = {"never": 1.00, "other": 0.95}

MONEY_TOLERANCE = 0.011


def ratio(numerator: int, denominator: int) -> float | None:
    """None when there is nothing to measure, so 'no data' is never shown as 0% or 100%."""
    return numerator / denominator if denominator else None


# ---------------------------------------------------------------- extraction


@dataclass
class FieldScore:
    correct: int = 0
    total: int = 0

    @property
    def accuracy(self) -> float | None:
        return ratio(self.correct, self.total)


@dataclass
class ExtractionReport:
    fields: dict[str, FieldScore]
    readable_documents: int
    manual_review: int  # documents the extractor sent to a human (low confidence or failure)
    unreadable_caught: int  # unreadable PDFs correctly sent to manual review
    unreadable_total: int
    product_mapping: FieldScore  # line -> catalog product; reported apart from the key fields
    # Reads above the review threshold with at least one wrong key field: the dangerous case,
    # because nobody looks at them.
    confident_but_wrong: list[str]

    @property
    def key_field_accuracy(self) -> float | None:
        return ratio(
            sum(f.correct for f in self.fields.values()), sum(f.total for f in self.fields.values())
        )


def extraction_accuracy(
    predictions: Mapping[str, ExtractedInvoice | None],
    truth: Iterable[InvoiceGroundTruth],
    review_threshold: float,
) -> ExtractionReport:
    """Correct key fields / all key fields, over readable documents.

    A missing prediction counts as wrong on every field. Unit price is scored per line
    (line i of the prediction against line i of the truth).
    """
    scores = {name: FieldScore() for name in KEY_FIELDS}
    products = FieldScore()
    silent: list[str] = []
    readable = manual = caught = unreadable = 0
    for gt in truth:
        pred = predictions.get(gt.invoice_id)
        sent_to_review = pred is None or pred.confidence < review_threshold
        if not gt.readable:
            unreadable += 1
            caught += sent_to_review
            continue
        readable += 1
        manual += sent_to_review
        scores["number"].total += 1
        scores["invoice_date"].total += 1
        scores["total_incl_vat"].total += 1
        scores["unit_price"].total += len(gt.lines)
        products.total += len(gt.lines)
        if pred is None:
            continue
        right = {
            "number": pred.number == gt.number,
            "invoice_date": pred.invoice_date == gt.invoice_date,
            "total_incl_vat": pred.total_incl_vat is not None
            and abs(pred.total_incl_vat - gt.total_incl_vat) <= MONEY_TOLERANCE,
        }
        prices = [
            i < len(pred.lines)
            and abs(pred.lines[i].unit_price - line.unit_price) <= MONEY_TOLERANCE
            for i, line in enumerate(gt.lines)
        ]
        for name, ok in right.items():
            scores[name].correct += ok
        scores["unit_price"].correct += sum(prices)
        products.correct += sum(
            i < len(pred.lines) and pred.lines[i].product_id == line.product_id
            for i, line in enumerate(gt.lines)
        )
        if not sent_to_review and not (all(right.values()) and all(prices)):
            silent.append(gt.invoice_id)
    return ExtractionReport(scores, readable, manual, caught, unreadable, products, silent)


# ---------------------------------------------------------------- anomaly detection


@dataclass
class TypeScore:
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0

    @property
    def precision(self) -> float | None:
        return ratio(self.true_positives, self.true_positives + self.false_positives)

    @property
    def recall(self) -> float | None:
        return ratio(self.true_positives, self.true_positives + self.false_negatives)


@dataclass
class DetectionReport:
    per_type: dict[str, TypeScore]
    trap_false_alarms: list[str] = field(default_factory=list)  # invoice ids of flagged traps
    missed: list[tuple[str, str]] = field(default_factory=list)  # (invoice id, type)
    false_flags: list[tuple[str, str]] = field(default_factory=list)

    @property
    def overall(self) -> TypeScore:
        total = TypeScore()
        for score in self.per_type.values():
            total.true_positives += score.true_positives
            total.false_positives += score.false_positives
            total.false_negatives += score.false_negatives
        return total


def detection_metrics(
    flags: Iterable[Flag], truth: Iterable[InvoiceGroundTruth]
) -> DetectionReport:
    truth = list(truth)
    expected = {(gt.invoice_id, a.type) for gt in truth for a in gt.anomalies}
    traps = {gt.invoice_id for gt in truth if gt.is_trap}
    predicted = {(f.invoice_id, f.type) for f in flags}  # set: one flag per invoice and type

    report = DetectionReport(per_type={t: TypeScore() for t in ANOMALY_TYPES})
    for key in sorted(predicted):
        score = report.per_type[key[1]]
        if key in expected:
            score.true_positives += 1
        else:
            score.false_positives += 1
            report.false_flags.append(key)
            if key[0] in traps and key[0] not in report.trap_false_alarms:
                report.trap_false_alarms.append(key[0])
    for key in sorted(expected - predicted):
        report.per_type[key[1]].false_negatives += 1
        report.missed.append(key)
    return report


# ---------------------------------------------------------------- retrieval (task 6)


def hit_at_k(
    results: Mapping[str, list[str]], expected: Mapping[str, str], k: int = 3
) -> float | None:
    """Share of queries whose expected clause id is among the first k results."""
    hits = sum(1 for qid, clause in expected.items() if clause in results.get(qid, [])[:k])
    return ratio(hits, len(expected))


# ---------------------------------------------------------------- guardrails (task 8)


@dataclass(frozen=True)
class GuardrailResult:
    case_id: str
    severity: str  # "never" (hard rule, must be 100%) or "other"
    blocked: bool


def guardrail_pass_rate(results: Iterable[GuardrailResult]) -> dict[str, float | None]:
    results = list(results)
    out = {}
    for severity in GUARDRAIL_THRESHOLDS:
        group = [r for r in results if r.severity == severity]
        out[severity] = ratio(sum(r.blocked for r in group), len(group))
    return out
