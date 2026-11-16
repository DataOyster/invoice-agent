"""Build the evaluation report for both splits.

    python -m invoice_agent.evals.run_eval              # writes docs/eval/report.md

For each split the same five checks run on three kinds of input:
- oracle: the ground truth as if extraction were perfect (upper bound of the check logic);
- rules: pdfplumber + regex, then products mapped by exact catalog name;
- Claude: the cached answers of the Claude extractor (see evals/llm_runs.py), when present.
No API call happens here: the report is reproducible offline.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from invoice_agent.data_gen.models import InvoiceGroundTruth
from invoice_agent.evals.baseline import (
    ground_truth_as_extracted,
    resolve_products_by_name,
    run_baseline,
)
from invoice_agent.evals.llm_runs import DEFAULT_CACHE, fresh_entries, load_cache
from invoice_agent.evals.metrics import detection_metrics, extraction_accuracy
from invoice_agent.evals.reference import ReferenceData, load_reference
from invoice_agent.evals.report import (
    detection_section,
    difficulty_section,
    extraction_section,
    render_report,
)
from invoice_agent.extraction.llm import to_extracted
from invoice_agent.extraction.rules import extract_pdf
from invoice_agent.schemas import ExtractedInvoice

REVIEW_THRESHOLD = 0.9  # below this confidence an invoice goes to manual review
SEED = 42

NOTES = [
    (
        "Matching rule: a flag counts as correct when the same invoice has an injected anomaly of "
        "the same type; several flags of one type on one invoice count once."
    ),
    (
        "Standard split: the rules are near-perfect by construction (anomalies injected by rules, "
        "clean machine-generated PDFs). It shows that dataset and checks are consistent, not that "
        "the problem is solved."
    ),
    (
        "Hard split: an unseen layout, product names printed differently from the catalog, strong "
        "rotation and scans without a text layer. The rules were not changed to fit it."
    ),
    "At most one injected anomaly per invoice; real invoices can carry several at once.",
    "Contracts are in English only; invoices are in Swedish, English and Italian.",
]


def _split(ref: ReferenceData, name: str) -> list[InvoiceGroundTruth]:
    return [g for g in ref.ground_truth if g.split == name]


def _claude_reads(
    ref: ReferenceData, data_dir: Path, split: str, cache_root: Path, model: str | None
) -> tuple[dict[str, ExtractedInvoice], dict] | None:
    """Claude reads for a split, only if every document of the split has a cached answer."""
    if model is None:
        return None
    entries = fresh_entries(load_cache(cache_root, SEED, model), ref, data_dir, split)
    if len(entries) < len(_split(ref, split)):
        return None
    known = {p.product_id for p in ref.products}
    reads = {}
    for invoice_id, entry in entries.items():
        if entry.read is None:
            reads[invoice_id] = ExtractedInvoice(
                invoice_id=invoice_id, confidence=0.0, problems=[entry.error or "call failed"]
            )
        else:
            reads[invoice_id] = to_extracted(invoice_id, entry.read, known)
    usage = {
        "model": model,
        "mean input tokens": round(sum(e.input_tokens for e in entries.values()) / len(entries)),
        "mean output tokens": round(sum(e.output_tokens for e in entries.values()) / len(entries)),
        "mean seconds": round(sum(e.seconds for e in entries.values()) / len(entries), 1),
        "failed calls": sum(1 for e in entries.values() if e.read is None),
    }
    return reads, usage


def build_report(data_dir: Path, cache_root: Path = DEFAULT_CACHE, model: str | None = None) -> str:
    ref = load_reference(data_dir)
    sections: list[list[str]] = []
    summary: dict[str, object] = {}
    for split in ("standard", "hard"):
        truth = _split(ref, split)
        summary[f"{split} split documents"] = len(truth)
        summary[f"{split} split with injected anomaly"] = sum(1 for g in truth if g.anomalies)
        summary[f"{split} split trap cases"] = sum(1 for g in truth if g.is_trap)

        oracle = [ground_truth_as_extracted(g, ref) for g in truth if g.readable]
        rules = {
            g.invoice_id: resolve_products_by_name(
                extract_pdf(data_dir / g.file, g.invoice_id), ref
            )
            for g in truth
        }
        readers: dict[str, dict[str, ExtractedInvoice]] = {"rules": rules}
        claude = _claude_reads(ref, data_dir, split, cache_root, model)
        if claude is not None:
            readers["Claude"] = claude[0]

        sections.append([f"## {split.capitalize()} split", ""])
        sections.append(
            detection_section(
                f"{split}, oracle input",
                detection_metrics(run_baseline(ref, oracle, REVIEW_THRESHOLD), truth),
            )
        )
        for name, reads in readers.items():
            sections.append(
                extraction_section(
                    f"{split}, {name}", extraction_accuracy(reads, truth, REVIEW_THRESHOLD)
                )
            )
            flags = run_baseline(ref, reads.values(), REVIEW_THRESHOLD)
            sections.append(
                detection_section(
                    f"{split}, checks on {name} reads", detection_metrics(flags, truth)
                )
            )
        if claude is None:
            sections.append(
                [
                    (
                        f"Claude extraction on the {split} split: not run yet "
                        f"(`python -m invoice_agent.evals.llm_runs --split {split}`)."
                    ),
                    "",
                ]
            )
        else:
            sections.append(
                [f"Claude usage on the {split} split:", ""]
                + [f"- {k}: {v}" for k, v in claude[1].items()]
                + [""]
            )
        if split == "hard":
            rows = []
            for difficulty in (
                "unseen_layout",
                "description_variants",
                "heavy_noise",
                "image_only_readable",
            ):
                subset = [g for g in truth if difficulty in g.difficulty]
                for name, reads in readers.items():
                    rows.append(
                        (difficulty, name, extraction_accuracy(reads, subset, REVIEW_THRESHOLD))
                    )
            sections.append(difficulty_section(rows))
    return render_report("Evaluation report", summary, sections, NOTES)


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description="Build the evaluation report.")
    parser.add_argument("--data", type=Path, default=Path("data/generated"))
    parser.add_argument("--out", type=Path, default=Path("docs/eval/report.md"))
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--model", default=os.environ.get("ANTHROPIC_MODEL"))
    args = parser.parse_args()
    report = build_report(args.data, args.cache, args.model)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as f:
        f.write(report)
    print(report)


if __name__ == "__main__":
    main()
