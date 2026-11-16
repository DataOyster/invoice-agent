"""The shared data layer: lookups, archive, flag queue, and the database-level hard rules."""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from invoice_agent.data_gen.models import Evidence
from invoice_agent.db.repository import Repository
from invoice_agent.db.schema import connect
from invoice_agent.db.seed import repository_from_reference, seed_reference
from invoice_agent.evals.baseline import ground_truth_as_extracted
from invoice_agent.evals.reference import load_reference
from invoice_agent.schemas import Flag


@pytest.fixture(scope="module")
def ref(generated_dir):
    return load_reference(generated_dir)


@pytest.fixture
def repo(ref):
    return repository_from_reference(ref)


def _flag(invoice_id="INV-0001"):
    return Flag(
        invoice_id=invoice_id,
        type="price_above_agreement",
        line_index=0,
        evidence=Evidence(kind="price_list", ref="PL-P101-1"),
        message="test",
    )


# ---------------------------------------------------------------- lookups


def test_seed_loads_all_reference_data(repo, ref):
    assert len(repo.suppliers()) == len(ref.suppliers)
    assert len(repo.products()) == len(ref.products)
    assert len(repo.all_clauses()) == len(ref.clauses)
    assert repo.supplier_by_org_number("000000-0001").supplier_id == "S01"
    assert repo.supplier_by_org_number("999999-9999") is None


def test_agreed_price_follows_the_delivery_date(repo):
    assert repo.agreed_price("P101", date(2026, 6, 30)).entry_id == "PL-P101-1"
    assert repo.agreed_price("P101", date(2026, 7, 1)).entry_id == "PL-P101-2"
    assert repo.agreed_price("P101", date(2025, 12, 31)) is None
    assert [e.entry_id for e in repo.price_history("P101")] == ["PL-P101-1", "PL-P101-2"]


def test_vat_rule_boundary_and_missing_rule(repo):
    assert repo.vat_rule("food", date(2026, 3, 31)).rule_id == "VAT-FOOD-12"
    assert repo.vat_rule("food", date(2026, 4, 1)).rule_id == "VAT-FOOD-06"
    assert repo.vat_rule("food", date(2028, 1, 1)) is None  # SPEC gives no rule: no guess


def test_price_increases_answer_which_suppliers_raised_prices(repo):
    july = repo.price_increases(date(2026, 7, 1), date(2026, 7, 31))
    assert {x.supplier_id for x in july} == {"S01", "S05"}
    assert all(x.new_price > x.old_price and x.change_pct > 0 for x in july)
    assert repo.price_increases(date(2026, 8, 1), date(2026, 8, 31)) == []


def test_clauses_and_delivery_notes(repo, ref):
    assert repo.clause_by_topic("S03", "price_lock").clause_id == "S03-C2"
    assert repo.clause("S03-C2").supplier_id == "S03"
    note = ref.delivery_notes[0]
    assert repo.delivery_note(note.note_id) == note
    assert repo.delivery_note("DN-9999") is None


# ---------------------------------------------------------------- archive


def test_archived_invoice_reads_back_unchanged(repo, ref):
    gt = ref.ground_truth[0]
    inv = ground_truth_as_extracted(gt, ref)
    repo.archive_invoice(inv, gt.supplier_id)
    stored = repo.invoice(gt.invoice_id)
    assert stored.model_dump(exclude={"language"}) == inv.model_dump(exclude={"language"})


def test_an_invoice_can_be_archived_only_once(repo, ref):
    gt = ref.ground_truth[0]
    inv = ground_truth_as_extracted(gt, ref)
    repo.archive_invoice(inv, gt.supplier_id)
    with pytest.raises(sqlite3.IntegrityError):
        repo.archive_invoice(inv, gt.supplier_id)


def test_find_earlier_invoice_by_number_or_date_and_total(repo, ref):
    gt = next(g for g in ref.ground_truth if g.document_type == "invoice")
    repo.archive_invoice(ground_truth_as_extracted(gt, ref), gt.supplier_id)
    assert repo.find_earlier_invoice(gt.supplier_id, gt.number, None, None) == (
        gt.invoice_id,
        "same_number",
    )
    assert repo.find_earlier_invoice(
        gt.supplier_id, "OTHER", gt.invoice_date, gt.total_incl_vat
    ) == (gt.invoice_id, "same_date_and_total")
    assert repo.find_earlier_invoice(gt.supplier_id, "OTHER", gt.invoice_date, 1.0) is None
    assert repo.find_earlier_invoice("S99", gt.number, None, None) is None


# ---------------------------------------------------------------- hard rules in the database


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE price_list SET unit_price = 1",
        "DELETE FROM price_list",
        "UPDATE contract_clauses SET text = 'anything goes'",
        "DELETE FROM contract_clauses",
        "UPDATE vat_rules SET rate = 0",
    ],
)
def test_reference_data_cannot_be_changed_even_with_raw_sql(repo, statement):
    with pytest.raises(sqlite3.IntegrityError):
        repo.conn.execute(statement)


def test_archived_invoices_cannot_be_changed_even_with_raw_sql(repo, ref):
    gt = ref.ground_truth[0]
    repo.archive_invoice(ground_truth_as_extracted(gt, ref), gt.supplier_id)
    for statement in (
        "UPDATE invoices SET total_incl_vat = 0",
        "DELETE FROM invoice_lines",
        "UPDATE invoice_lines SET unit_price = 0",
        "DELETE FROM invoices",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            repo.conn.execute(statement)


def test_repository_has_no_method_that_writes_reference_data_or_pays():
    allowed_writes = {"archive_invoice", "propose_flag", "decide_flag"}
    public = {name for name in dir(Repository) if not name.startswith("_")}
    forbidden_words = (
        "pay",
        "approve_invoice",
        "reject_invoice",
        "update",
        "delete",
        "set_price",
        "edit",
    )
    for name in public:
        assert not any(word in name for word in forbidden_words), name
    writers = {
        n
        for n in public
        if n.startswith(("archive", "propose", "decide", "insert", "write", "save"))
    }
    assert writers == allowed_writes


# ---------------------------------------------------------------- flags and audit


def test_new_flag_waits_for_a_human(repo):
    flag_id = repo.propose_flag(_flag(), proposed_by="rule-baseline")
    [stored] = repo.flags()
    assert stored.flag_id == flag_id and stored.status == "pending_review"
    assert stored.reviewer is None and stored.flag == _flag()


def test_human_decision_is_recorded_and_final(repo):
    flag_id = repo.propose_flag(_flag(), proposed_by="agent")
    repo.decide_flag(flag_id, approve=True, reviewer="fabio")
    [stored] = repo.flags(status="approved")
    assert stored.reviewer == "fabio" and stored.decided_at
    with pytest.raises(ValueError):
        repo.decide_flag(flag_id, approve=False, reviewer="fabio")
    with pytest.raises(sqlite3.IntegrityError):
        repo.conn.execute("UPDATE flags SET status = 'rejected' WHERE flag_id = ?", (flag_id,))


def test_decision_needs_a_named_reviewer(repo):
    flag_id = repo.propose_flag(_flag(), proposed_by="agent")
    with pytest.raises(ValueError):
        repo.decide_flag(flag_id, approve=True, reviewer="  ")


def test_flag_content_cannot_be_edited_or_deleted(repo):
    flag_id = repo.propose_flag(_flag(), proposed_by="agent")
    with pytest.raises(sqlite3.IntegrityError):
        repo.conn.execute("UPDATE flags SET message = 'changed' WHERE flag_id = ?", (flag_id,))
    with pytest.raises(sqlite3.IntegrityError):
        repo.conn.execute("DELETE FROM flags")


def test_a_pending_flag_cannot_be_approved_without_a_reviewer_by_raw_sql(repo):
    flag_id = repo.propose_flag(_flag(), proposed_by="agent")
    with pytest.raises(sqlite3.IntegrityError):
        repo.conn.execute("UPDATE flags SET status = 'approved' WHERE flag_id = ?", (flag_id,))


def test_audit_log_records_proposals_and_decisions_and_is_append_only(repo):
    flag_id = repo.propose_flag(_flag(), proposed_by="agent")
    repo.decide_flag(flag_id, approve=False, reviewer="fabio")
    log = repo.audit_log()
    assert [(e["actor"], e["action"]) for e in log] == [
        ("agent", "propose_flag"),
        ("fabio", "rejected_flag"),
    ]
    with pytest.raises(sqlite3.IntegrityError):
        repo.conn.execute("DELETE FROM audit_log")


def test_database_file_persists_between_connections(tmp_path, ref):
    path = tmp_path / "test.db"
    conn = connect(path)
    seed_reference(conn, ref)
    Repository(conn).propose_flag(_flag(), proposed_by="agent")
    conn.close()
    again = Repository(connect(path))
    assert len(again.flags()) == 1 and len(again.products()) == len(ref.products)
