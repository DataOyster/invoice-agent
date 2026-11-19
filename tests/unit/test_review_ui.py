"""Review UI (task 11): the only place a flag is decided, always by a named person.

The helpers are tested directly; the Streamlit page is driven offline with AppTest (no browser).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from invoice_agent.db.seed import repository_from_reference
from invoice_agent.evals.baseline import RuleBaseline, ground_truth_as_extracted
from invoice_agent.evals.reference import load_reference
from invoice_agent.ui.review_queue import counts, decide, open_queue, queue

APP = str(Path(__file__).resolve().parents[2] / "src" / "invoice_agent" / "ui" / "app.py")


@pytest.fixture
def db_path(generated_dir, tmp_path):
    """The standard split checked in arrival order: one pending flag per injected anomaly."""
    ref = load_reference(generated_dir)
    path = tmp_path / "queue.db"
    repo = repository_from_reference(ref, path)
    for gt in (g for g in ref.ground_truth if g.split == "standard" and g.readable):
        inv = ground_truth_as_extracted(gt, ref)
        for flag in RuleBaseline(repo).check(inv):
            repo.propose_flag(flag, proposed_by="pipeline")
        repo.archive_invoice(inv, gt.supplier_id)
    repo.conn.close()
    return path


def test_every_flag_shows_its_evidence_next_to_it(db_path):
    views = queue(open_queue(db_path))
    assert views and all(v.status == "pending_review" for v in views)
    kinds = {v.evidence_title.split(" ")[0] for v in views}
    assert {"Agreed", "Delivery", "VAT", "Earlier"} <= kinds  # all four evidence kinds
    assert all(v.evidence for v in views), "no evidence may be missing"
    assert all(v.line for v in views if v.type_label != "Duplicate invoice")


def test_a_decision_needs_a_name(db_path):
    repo = open_queue(db_path)
    flag_id = queue(repo)[0].flag_id
    with pytest.raises(ValueError, match="name"):
        decide(repo, flag_id, confirm=True, reviewer="   ")
    assert counts(repo)["pending_review"] == len(queue(repo))


def test_a_decision_is_signed_audited_and_final(db_path):
    repo = open_queue(db_path)
    flag_id = queue(repo)[0].flag_id
    decide(repo, flag_id, confirm=False, reviewer="Anna Reviewer")
    stored = repo.flags(status="rejected")[0]
    assert (stored.flag_id, stored.reviewer) == (flag_id, "Anna Reviewer")
    assert repo.audit_log()[-1]["action"] == "rejected_flag"
    with pytest.raises(ValueError, match="already decided"):
        decide(repo, flag_id, confirm=True, reviewer="Someone Else")


def test_a_missing_database_is_reported_not_created(tmp_path):
    with pytest.raises(FileNotFoundError):
        open_queue(tmp_path / "nope.db")
    assert not (tmp_path / "nope.db").exists()


# ---------------------------------------------------------------- the page itself


def _app(db_path, monkeypatch) -> AppTest:
    monkeypatch.setenv("INVOICE_AGENT_DB", str(db_path))
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


def test_page_lists_pending_flags_and_blocks_decisions_without_a_name(db_path, monkeypatch):
    at = _app(db_path, monkeypatch)
    pending = counts(open_queue(db_path))["pending_review"]
    confirm_buttons = [b for b in at.button if b.key.startswith("confirm-")]
    assert len(confirm_buttons) == pending
    assert all(b.disabled for b in at.button)


def test_page_records_a_named_confirmation(db_path, monkeypatch):
    at = _app(db_path, monkeypatch)
    at.sidebar.text_input(key="reviewer").input("Anna Reviewer").run()
    first = next(b for b in at.button if b.key.startswith("confirm-"))
    flag_id = int(first.key.split("-")[1])
    first.click().run()
    assert not at.exception, at.exception
    stored = next(s for s in open_queue(db_path).flags() if s.flag_id == flag_id)
    assert (stored.status, stored.reviewer) == ("approved", "Anna Reviewer")
    assert any("confirmed by Anna Reviewer" in s.value for s in at.success)
