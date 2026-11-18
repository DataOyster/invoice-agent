"""Build the demo database used by the MCP server and the review UI, offline.

Reference data (suppliers, prices, VAT, contracts, delivery notes) plus the hard-split invoices
run through the real pipeline (tripwire, checks, agent, flag validation, audit log). The
extraction and agent answers are replayed from the committed cache of the live runs, so this
needs no API key and gives the same database every time. Flags end up pending_review: a person
decides them in the UI.

    python -m invoice_agent.demo_db            # -> data/invoice_agent.db
"""

from __future__ import annotations

import argparse
import os
from collections import Counter
from pathlib import Path

from invoice_agent.db.repository import Repository
from invoice_agent.db.seed import repository_from_reference
from invoice_agent.evals.agent_runs import reads_for, replay_runner, run_dir
from invoice_agent.evals.reference import load_reference
from invoice_agent.pipeline import process_invoice
from invoice_agent.rag.index import ContractIndex

SPLIT = "hard"


def build_demo_db(data_dir: Path, db_path: str | Path, cache_root: Path, model: str) -> Repository:
    ref = load_reference(data_dir)
    reads = reads_for(ref, data_dir, SPLIT, "claude", cache_root, model)
    if reads is None:
        raise SystemExit(f"no complete extraction cache for {model} on the {SPLIT} split")
    repo = repository_from_reference(ref, db_path)
    index = ContractIndex(ref.clauses)
    runner_for = replay_runner(run_dir(cache_root, model, "claude"))
    by_id = {inv.invoice_id: inv for inv in reads}
    for g in (g for g in ref.ground_truth if g.split == SPLIT):  # arrival order matters
        inv = by_id[g.invoice_id]
        process_invoice(
            data_dir / g.file,
            g.invoice_id,
            repo,
            index,
            extract=lambda _path, _id, inv=inv: inv,  # the cached Claude read
            run_agent=runner_for(inv),  # the cached agent run
        )
    return repo


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description="Build the demo database offline from caches.")
    parser.add_argument("--data", type=Path, default=Path("data/generated"))
    parser.add_argument("--db", type=Path, default=Path("data/invoice_agent.db"))
    parser.add_argument("--cache", type=Path, default=Path("data/llm_cache"))
    parser.add_argument("--model", default=os.environ.get("ANTHROPIC_MODEL"))
    args = parser.parse_args()
    if not args.model:
        raise SystemExit("set ANTHROPIC_MODEL or pass --model (the model whose cache to replay)")
    if args.db.exists():
        raise SystemExit(f"{args.db} exists; delete it first to rebuild from scratch")
    repo = build_demo_db(args.data, args.db, args.cache, args.model)
    statuses = Counter(f.status for f in repo.flags())
    archived = repo.conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    print(f"created {args.db}: {archived} invoices archived, flags {dict(statuses)}")


if __name__ == "__main__":
    main()
