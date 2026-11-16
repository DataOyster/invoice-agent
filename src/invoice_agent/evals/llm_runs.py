"""Run the Claude extractor over a split and cache every answer on disk.

    python -m invoice_agent.evals.llm_runs --split hard          # needs ANTHROPIC_API_KEY in .env

Why a cache: the evaluation report must be reproducible by anyone, offline, without a key.
Each answer is stored with the PDF's hash; if the dataset changes, stale answers are ignored.
Only documents without a cached answer are sent to the API, so re-running costs nothing.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from invoice_agent.evals.reference import ReferenceData, load_reference
from invoice_agent.extraction.llm import (
    AnthropicLLMClient,
    LLMClient,
    LLMInvoiceRead,
    LLMResult,
    catalog_prompt,
    extract_with_llm,
    pdf_sha256,
)

DEFAULT_CACHE = Path("data/llm_cache")


@dataclass
class CachedRead:
    invoice_id: str
    pdf_sha256: str
    model: str
    read: LLMInvoiceRead | None  # None = the call failed (kept, so the failure is counted)
    error: str | None
    input_tokens: int
    output_tokens: int
    seconds: float


def cache_dir(root: Path, seed: int, model: str) -> Path:
    return Path(root) / f"seed{seed}" / model


def load_cache(root: Path, seed: int, model: str) -> dict[str, CachedRead]:
    folder = cache_dir(root, seed, model)
    out = {}
    for path in sorted(folder.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        read = LLMInvoiceRead.model_validate(raw["read"]) if raw["read"] else None
        out[raw["invoice_id"]] = CachedRead(**{**raw, "read": read})
    return out


def _save(root: Path, seed: int, model: str, entry: CachedRead) -> None:
    folder = cache_dir(root, seed, model)
    folder.mkdir(parents=True, exist_ok=True)
    data = {**entry.__dict__, "read": entry.read.model_dump() if entry.read else None}
    text = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    with (folder / f"{entry.invoice_id}.json").open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def fresh_entries(
    cache: dict[str, CachedRead], ref: ReferenceData, data_dir: Path, split: str
) -> dict[str, CachedRead]:
    """Cached answers that match the current PDFs of a split."""
    out = {}
    for gt in ref.ground_truth:
        entry = cache.get(gt.invoice_id)
        if (
            gt.split == split
            and entry is not None
            and entry.pdf_sha256 == pdf_sha256((data_dir / gt.file).read_bytes())
        ):
            out[gt.invoice_id] = entry
    return out


def run_split(
    ref: ReferenceData,
    data_dir: Path,
    split: str,
    client: LLMClient,
    root: Path,
    seed: int,
    limit: int | None = None,
) -> dict[str, CachedRead]:
    catalog = catalog_prompt(ref.suppliers, ref.products)
    known = {p.product_id for p in ref.products}
    cache = fresh_entries(load_cache(root, seed, client.model), ref, data_dir, split)
    todo = [g for g in ref.ground_truth if g.split == split and g.invoice_id not in cache]
    for gt in todo[:limit]:
        pdf = (data_dir / gt.file).read_bytes()
        extracted, result = extract_with_llm(pdf, gt.invoice_id, client, catalog, known)
        entry = _entry(gt.invoice_id, pdf, client.model, result, extracted.problems)
        _save(root, seed, client.model, entry)
        cache[gt.invoice_id] = entry
        print(f"{gt.invoice_id}: confidence {extracted.confidence:.2f} {extracted.problems[:2]}")
    return cache


def _entry(
    invoice_id: str, pdf: bytes, model: str, result: LLMResult | None, problems: list[str]
) -> CachedRead:
    return CachedRead(
        invoice_id=invoice_id,
        pdf_sha256=pdf_sha256(pdf),
        model=model,
        read=result.read if result else None,
        error=None if result else "; ".join(problems),
        input_tokens=result.input_tokens if result else 0,
        output_tokens=result.output_tokens if result else 0,
        seconds=result.seconds if result else 0.0,
    )


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description="Extract a split with Claude and cache it.")
    parser.add_argument("--data", type=Path, default=Path("data/generated"))
    parser.add_argument("--split", choices=("standard", "hard"), default="hard")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--limit", type=int, default=None, help="at most N new API calls")
    args = parser.parse_args()

    ref = load_reference(args.data)
    client = AnthropicLLMClient()
    cache = run_split(ref, args.data, args.split, client, args.cache, args.seed, args.limit)
    tokens_in = sum(e.input_tokens for e in cache.values())
    tokens_out = sum(e.output_tokens for e in cache.values())
    print(
        f"{len(cache)} cached answers for model {client.model}: "
        f"{tokens_in} input and {tokens_out} output tokens in total"
    )


if __name__ == "__main__":
    main()
