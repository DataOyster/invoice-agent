"""The demo database is rebuilt offline from the committed cache of the live runs."""

from __future__ import annotations

from pathlib import Path

import pytest

from invoice_agent.demo_db import build_demo_db
from invoice_agent.evals.reference import load_reference

CACHE = Path("data/llm_cache")


def _cached_model() -> str | None:
    """The model whose extraction cache is committed (no model name is hard-coded)."""
    folders = sorted(
        p.name for p in (CACHE / "seed42").glob("*") if not p.name.startswith("agent__")
    )
    return folders[0] if folders else None


@pytest.mark.skipif(_cached_model() is None, reason="no committed live-run cache")
def test_demo_database_holds_exactly_the_injected_anomalies_as_pending_flags(
    generated_dir, tmp_path
):
    repo = build_demo_db(generated_dir, tmp_path / "demo.db", CACHE, _cached_model())
    flags = repo.flags()
    truth = {
        (g.invoice_id, a.type)
        for g in load_reference(generated_dir).ground_truth
        if g.split == "hard"
        for a in g.anomalies
    }
    assert {(f.flag.invoice_id, f.flag.type) for f in flags} == truth
    assert all(f.status == "pending_review" and f.reviewer is None for f in flags)
    assert all(f.proposed_by == "pipeline" for f in flags)
