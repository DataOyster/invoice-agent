"""Run the agent over a split (live, cached) and replay the cache for the report (offline).

    python -m invoice_agent.evals.agent_runs --split hard --input rules     # needs the API key

`--input rules` reviews what the rule extractor read; `--input claude` reviews the cached Claude
extraction (run evals/llm_runs.py first). Only invoices that need the agent (unmatched lines or
findings to explain) call the model; each run is stored, so re-running costs nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from invoice_agent.agent.llm import AnthropicChatClient, ChatClient
from invoice_agent.agent.loop import AgentRun, Limits, run_agent
from invoice_agent.agent.review import InvoiceReview, review_in_order
from invoice_agent.agent.tools import ToolBox
from invoice_agent.db.seed import repository_from_reference
from invoice_agent.evals.baseline import resolve_products_by_name
from invoice_agent.evals.llm_runs import DEFAULT_CACHE, fresh_entries, load_cache
from invoice_agent.evals.reference import ReferenceData, load_reference
from invoice_agent.extraction.llm import to_extracted
from invoice_agent.extraction.rules import extract_pdf
from invoice_agent.rag.index import ContractIndex
from invoice_agent.schemas import ExtractedInvoice

REVIEW_THRESHOLD = 0.9


class MissingRun(LookupError):
    """The cache has no run for an invoice that needs the agent."""


def reads_for(
    ref: ReferenceData,
    data_dir: Path,
    split: str,
    source: str,
    cache_root: Path,
    extraction_model: str | None,
) -> list[ExtractedInvoice] | None:
    truth = [g for g in ref.ground_truth if g.split == split]
    if source == "rules":
        return [
            resolve_products_by_name(extract_pdf(data_dir / g.file, g.invoice_id), ref)
            for g in truth
        ]
    if extraction_model is None:
        return None
    entries = fresh_entries(load_cache(cache_root, 42, extraction_model), ref, data_dir, split)
    if len(entries) < len(truth):
        return None
    known = {p.product_id for p in ref.products}
    return [
        to_extracted(g.invoice_id, entries[g.invoice_id].read, known)
        if entries[g.invoice_id].read
        else ExtractedInvoice(invoice_id=g.invoice_id, confidence=0.0, problems=["call failed"])
        for g in truth
    ]


def _input_sha(inv: ExtractedInvoice) -> str:
    return hashlib.sha256(inv.model_dump_json().encode()).hexdigest()


def run_dir(cache_root: Path, model: str, source: str) -> Path:
    return Path(cache_root) / "seed42" / f"agent__{model}__{source}"


def _load_runs(folder: Path) -> dict[str, dict]:
    return {
        p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))
    }


def _save_run(folder: Path, invoice_id: str, sha: str, run: AgentRun) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    data = {"input_sha256": sha, **run.__dict__}
    with (folder / f"{invoice_id}.json").open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def review_split(
    ref: ReferenceData,
    reads: list[ExtractedInvoice],
    runner_for: Callable[[ExtractedInvoice], Callable[[ToolBox], AgentRun]],
) -> list[InvoiceReview]:
    repo = repository_from_reference(ref)
    index = ContractIndex(ref.clauses)
    by_id = {r.invoice_id: r for r in reads}
    return review_in_order(reads, repo, index, lambda i: runner_for(by_id[i]), REVIEW_THRESHOLD)


def live_runner(client: ChatClient, limits: Limits, folder: Path):
    cached = _load_runs(folder)

    def runner_for(inv: ExtractedInvoice):
        def run(toolbox: ToolBox) -> AgentRun:
            sha = _input_sha(inv)
            hit = cached.get(inv.invoice_id)
            if hit and hit["input_sha256"] == sha:
                return AgentRun(**{k: v for k, v in hit.items() if k != "input_sha256"})
            result = run_agent(toolbox, client, limits)
            _save_run(folder, inv.invoice_id, sha, result)
            print(
                f"{inv.invoice_id}: {result.stopped_reason}, {result.steps} steps, "
                f"${result.cost_usd:.4f}"
            )
            return result

        return run

    return runner_for


def replay_runner(folder: Path):
    cached = _load_runs(folder)

    def runner_for(inv: ExtractedInvoice):
        def run(toolbox: ToolBox) -> AgentRun:
            hit = cached.get(inv.invoice_id)
            if not hit or hit["input_sha256"] != _input_sha(inv):
                raise MissingRun(inv.invoice_id)
            return AgentRun(**{k: v for k, v in hit.items() if k != "input_sha256"})

        return run

    return runner_for


def replay(
    ref: ReferenceData,
    data_dir: Path,
    split: str,
    source: str,
    cache_root: Path,
    model: str | None,
    extraction_model: str | None,
) -> list[InvoiceReview] | None:
    """Agent reviews rebuilt from the cache, or None if the cache does not cover the split."""
    if model is None:
        return None
    reads = reads_for(ref, data_dir, split, source, cache_root, extraction_model)
    if reads is None:
        return None
    try:
        return review_split(ref, reads, replay_runner(run_dir(cache_root, model, source)))
    except MissingRun:
        return None


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description="Run the agent over a split and cache it.")
    parser.add_argument("--data", type=Path, default=Path("data/generated"))
    parser.add_argument("--split", choices=("standard", "hard"), default="hard")
    parser.add_argument("--input", choices=("rules", "claude"), default="rules")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args()

    ref = load_reference(args.data)
    client = AnthropicChatClient()
    reads = reads_for(ref, args.data, args.split, args.input, args.cache, client.model)
    if reads is None:
        raise SystemExit("no complete Claude extraction cache: run evals.llm_runs first")
    folder = run_dir(args.cache, client.model, args.input)
    reviews = review_split(ref, reads, live_runner(client, Limits.from_env(), folder))
    runs = [r.run for r in reviews if r.run]
    print(
        f"{len(reviews)} invoices, agent called on {len(runs)}, total "
        f"${sum(r.cost_usd for r in runs):.4f}"
    )


if __name__ == "__main__":
    main()
