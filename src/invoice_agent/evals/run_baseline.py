"""Run the rule baseline end to end and write the report.

    python -m invoice_agent.evals.run_baseline --data data/generated --out docs/eval/baseline_report.md

Two runs of the same checks:
- "oracle input": the ground truth as if extraction were perfect (upper bound of the check logic);
- "PDF input": what the rule extractor (pdfplumber + regex) really read from the PDFs.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from invoice_agent.evals.baseline import ground_truth_as_extracted, run_baseline
from invoice_agent.evals.metrics import detection_metrics, extraction_accuracy
from invoice_agent.evals.reference import load_reference
from invoice_agent.evals.report import detection_section, extraction_section, render_report
from invoice_agent.extraction.rules import extract_pdf

REVIEW_THRESHOLD = 0.9  # below this confidence an invoice goes to manual review

NOTES = [
    (
        "Matching rule: a flag counts as correct when the same invoice has an injected anomaly of the "
        "same type; several flags of one type on one invoice count once."
    ),
    (
        "The baseline is near-perfect by construction: anomalies are injected by rules, and the "
        "PDFs are clean, machine-generated text. A perfect baseline score therefore says the "
        "dataset and checks are consistent, not that the problem is solved."
    ),
    (
        "Consequence for task 7: the agent cannot beat this baseline on recall on this dataset. "
        "Its value has to be shown on what fixed rules cannot do (cited explanations, clause "
        "retrieval, unreadable or unfamiliar documents, prompt-injection resistance), and on harder "
        "variants of the data."
    ),
    "At most one injected anomaly per invoice; real invoices can carry several at once.",
    "Contracts are in English only; invoices are in Swedish, English and Italian.",
]


def build_report(data_dir: Path) -> str:
    ref = load_reference(data_dir)
    truth = ref.ground_truth
    oracle = {g.invoice_id: ground_truth_as_extracted(g, ref) for g in truth if g.readable}
    from_pdf = {g.invoice_id: extract_pdf(data_dir / g.file, g.invoice_id) for g in truth}

    summary = {
        "documents": len(truth),
        "with injected anomaly": sum(1 for g in truth if g.anomalies),
        "trap cases (must not be flagged)": sum(1 for g in truth if g.is_trap),
        "unreadable PDFs": sum(1 for g in truth if not g.readable),
        "PDFs with scan noise": sum(1 for g in truth if g.scan_noise),
    }
    sections = [
        extraction_section(
            "rule extractor (pdfplumber + regex)",
            extraction_accuracy(from_pdf, truth, REVIEW_THRESHOLD),
        ),
        detection_section(
            "rule baseline, oracle input",
            detection_metrics(run_baseline(ref, oracle.values(), REVIEW_THRESHOLD), truth),
        ),
        detection_section(
            "rule baseline, PDF input",
            detection_metrics(run_baseline(ref, from_pdf.values(), REVIEW_THRESHOLD), truth),
        ),
    ]
    return render_report("Baseline evaluation (rules, no AI)", summary, sections, NOTES)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the rule baseline.")
    parser.add_argument("--data", type=Path, default=Path("data/generated"))
    parser.add_argument("--out", type=Path, default=Path("docs/eval/baseline_report.md"))
    args = parser.parse_args()
    report = build_report(args.data)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as f:
        f.write(report)
    print(report)


if __name__ == "__main__":
    main()
