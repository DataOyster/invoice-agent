"""Review queue logic, kept apart from Streamlit so it can be tested and read line by line.

This is the only place in the code base where a flag is decided (Repository.decide_flag), and
only with a named reviewer. "Confirm" means the reviewer agrees the anomaly is real; "dismiss"
means it is not. Neither pays, approves or rejects an invoice: that stays outside this system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from invoice_agent.db.repository import Repository, StoredFlag
from invoice_agent.db.schema import connect

TYPE_LABELS = {
    "price_above_agreement": "Price above agreement",
    "duplicate": "Duplicate invoice",
    "wrong_vat": "Wrong VAT",
    "quantity_mismatch": "Quantity not matching the delivery note",
    "unit_confusion": "Confused unit of measure",
}


def open_queue(db_path: str | Path) -> Repository:
    """Open an existing database for reviewing (refuses to create an empty one by accident)."""
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; build it with python -m invoice_agent.demo_db")
    return Repository(connect(path))


@dataclass
class FlagView:
    """Everything a reviewer needs to decide one flag, side by side."""

    flag_id: int
    status: str
    reviewer: str | None
    invoice_id: str
    supplier: str
    type_label: str
    message: str
    explanation: str | None
    clause_ref: str | None
    quote: str | None
    line: dict | None  # the invoice line the flag is about, as printed on the invoice
    evidence_title: str
    evidence: dict = field(default_factory=dict)


def _evidence(repo: Repository, kind: str, ref: str) -> tuple[str, dict]:
    if kind == "price_list":
        row = repo.conn.execute("SELECT * FROM price_list WHERE entry_id = ?", (ref,)).fetchone()
        if row is None:
            return f"Price list {ref} (not found)", {}
        product = repo.product(row["product_id"])
        return f"Agreed price ({ref})", {
            "product": product.name if product else row["product_id"],
            "agreed unit price": row["unit_price"],
            "unit": row["unit"],
            "valid": f"{row['valid_from']} to {row['valid_to']}",
        }
    if kind == "delivery_note":
        note = repo.delivery_note(ref)
        if note is None:
            return f"Delivery note {ref} (not found)", {}
        delivered = {
            (repo.product(x.product_id).name if repo.product(x.product_id) else x.product_id): (
                f"{x.qty:g} {x.unit}"
            )
            for x in note.lines
        }
        return f"Delivery note {ref}, delivered {note.delivery_date}", delivered
    if kind == "vat_rule":
        row = repo.conn.execute("SELECT * FROM vat_rules WHERE rule_id = ?", (ref,)).fetchone()
        if row is None:
            return f"VAT rule {ref} (not found)", {}
        return f"VAT rule {ref}", {
            "category": row["category"],
            "rate due": f"{row['rate']:.0%}",
            "valid": f"{row['valid_from'] or '...'} to {row['valid_to'] or '...'}",
            "source": row["source"],
        }
    if kind == "invoice":
        earlier = repo.invoice(ref)
        if earlier is None:
            return f"Earlier invoice {ref} (not found)", {}
        return f"Earlier invoice {ref}", {
            "number": earlier.number,
            "invoice date": str(earlier.invoice_date),
            "total incl. VAT": earlier.total_incl_vat,
        }
    return f"{kind} {ref}", {}


def flag_view(repo: Repository, stored: StoredFlag) -> FlagView:
    flag = stored.flag
    inv = repo.invoice(flag.invoice_id)
    supplier = repo.supplier_by_org_number(inv.supplier_org_number) if inv else None
    line = None
    if inv is not None and flag.line_index is not None and flag.line_index < len(inv.lines):
        x = inv.lines[flag.line_index]
        line = {
            "line": flag.line_index,
            "description": x.description,
            "qty": f"{x.qty:g} {x.unit}",
            "unit price": x.unit_price,
            "VAT": f"{x.vat_rate:.0%}",
            "line total": x.line_total,
        }
    title, details = _evidence(repo, flag.evidence.kind, flag.evidence.ref)
    return FlagView(
        flag_id=stored.flag_id,
        status=stored.status,
        reviewer=stored.reviewer,
        invoice_id=flag.invoice_id,
        supplier=supplier.name if supplier else "unknown supplier",
        type_label=TYPE_LABELS.get(flag.type, flag.type),
        message=flag.message,
        explanation=flag.explanation,
        clause_ref=flag.clause_ref,
        quote=flag.quote,
        line=line,
        evidence_title=title,
        evidence=details,
    )


def queue(repo: Repository, status: str | None = "pending_review") -> list[FlagView]:
    return [flag_view(repo, s) for s in repo.flags(status=status)]


def counts(repo: Repository) -> dict[str, int]:
    rows = repo.conn.execute("SELECT status, COUNT(*) AS n FROM flags GROUP BY status").fetchall()
    found = {r["status"]: r["n"] for r in rows}
    return {s: found.get(s, 0) for s in ("pending_review", "approved", "rejected")}


def decide(repo: Repository, flag_id: int, confirm: bool, reviewer: str) -> str:
    """Record one human decision. Returns a message for the reviewer; raises on misuse."""
    name = reviewer.strip()
    if not name:
        raise ValueError("Enter your name first: every decision is signed.")
    repo.decide_flag(flag_id, approve=confirm, reviewer=name)
    verb = "confirmed" if confirm else "dismissed"
    return f"Flag {flag_id} {verb} by {name}. The decision is final and in the audit log."
