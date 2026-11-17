"""Last gate before a flag enters the review queue: its evidence must exist in the sources.

SPEC: "every flag must quote evidence that exists in the source documents; flags without
verifiable evidence are discarded". Checked here against the database, not against what any
component claims:
- the evidence id exists and is of the stated kind (price-list entry, delivery note, VAT rule,
  earlier archived invoice);
- a clause reference, if present, belongs to the invoice's supplier and its quote is verbatim.
A flag whose clause quote fails is not discarded: its price/quantity/VAT evidence still stands,
so only the unverifiable citation is removed (we prefer false alarms to missed anomalies).
"""

from __future__ import annotations

from dataclasses import dataclass

from invoice_agent.db.repository import Repository
from invoice_agent.rag.citations import Citation, verify_citation
from invoice_agent.schemas import Flag


@dataclass(frozen=True)
class Verdict:
    flag: Flag | None  # None = discarded
    reason: str | None  # why it was discarded or changed


def _evidence_exists(flag: Flag, repo: Repository) -> bool:
    kind, ref = flag.evidence.kind, flag.evidence.ref
    if kind == "price_list":
        row = repo.conn.execute("SELECT 1 FROM price_list WHERE entry_id = ?", (ref,)).fetchone()
        return row is not None
    if kind == "delivery_note":
        return repo.delivery_note(ref) is not None
    if kind == "vat_rule":
        row = repo.conn.execute("SELECT 1 FROM vat_rules WHERE rule_id = ?", (ref,)).fetchone()
        return row is not None
    if kind == "invoice":
        return repo.invoice(ref) is not None and ref != flag.invoice_id
    return False


def validate_flag(flag: Flag, supplier_id: str, repo: Repository) -> Verdict:
    if not _evidence_exists(flag, repo):
        return Verdict(None, f"evidence {flag.evidence.kind}:{flag.evidence.ref} not found")
    if flag.clause_ref is None:
        return Verdict(flag, None)
    clause = repo.clause(flag.clause_ref)
    quote_ok = (
        clause is not None
        and clause.supplier_id == supplier_id
        and flag.quote is not None
        and verify_citation(Citation(flag.clause_ref, clause.title, flag.quote), repo)
    )
    if quote_ok:
        return Verdict(flag, None)
    stripped = flag.model_copy(update={"clause_ref": None, "quote": None})
    return Verdict(stripped, f"citation {flag.clause_ref} could not be verified; removed")
