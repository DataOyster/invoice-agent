"""The shared data layer. The agent (task 7) and the MCP server (task 9) call these same
functions; there is no second copy of any query.

Three kinds of function, kept apart on purpose:
- reads: anything may call them;
- `archive_invoice` and `propose_flag`: inserts only, used by the pipeline/agent;
- `decide_flag`: the human decision. Never exposed as an agent or MCP tool (CLAUDE.md hard rules).
There is no function that updates prices, contracts, VAT rules or archived invoices, and the
schema's triggers refuse such statements even if someone writes the SQL by hand.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime

from invoice_agent.data_gen.models import (
    Clause,
    DeliveryNote,
    DeliveryNoteLine,
    PriceListEntry,
    Product,
    Supplier,
    VatCategory,
    VatRule,
)
from invoice_agent.schemas import ExtractedInvoice, ExtractedLine, Flag


@dataclass(frozen=True)
class PriceIncrease:
    supplier_id: str
    supplier_name: str
    product_id: str
    product_name: str
    old_price: float
    new_price: float
    change_pct: float
    valid_from: date
    old_entry_id: str
    new_entry_id: str


@dataclass(frozen=True)
class StoredFlag:
    flag_id: int
    flag: Flag
    proposed_by: str
    status: str
    reviewer: str | None
    decided_at: str | None


def _day(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


class Repository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ------------------------------------------------------------ suppliers and products

    def suppliers(self) -> list[Supplier]:
        rows = self.conn.execute("SELECT * FROM suppliers ORDER BY supplier_id").fetchall()
        return [self._supplier(r) for r in rows]

    def supplier_by_org_number(self, org_number: str | None) -> Supplier | None:
        row = self.conn.execute(
            "SELECT * FROM suppliers WHERE org_number = ?", (org_number,)
        ).fetchone()
        return self._supplier(row) if row else None

    def product(self, product_id: str | None) -> Product | None:
        row = self.conn.execute(
            "SELECT * FROM products WHERE product_id = ?", (product_id,)
        ).fetchone()
        return self._product(row) if row else None

    def products(self, supplier_id: str | None = None) -> list[Product]:
        if supplier_id is None:
            rows = self.conn.execute("SELECT * FROM products ORDER BY product_id").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM products WHERE supplier_id = ? ORDER BY product_id", (supplier_id,)
            ).fetchall()
        return [self._product(r) for r in rows]

    # ------------------------------------------------------------ prices, VAT, contracts

    def agreed_price(self, product_id: str, day: date) -> PriceListEntry | None:
        """The price-list entry valid on `day` (the delivery date decides, SPEC section 2)."""
        row = self.conn.execute(
            "SELECT * FROM price_list WHERE product_id = ? AND valid_from <= ? AND ? <= valid_to",
            (product_id, day.isoformat(), day.isoformat()),
        ).fetchone()
        return self._price(row) if row else None

    def price_history(self, product_id: str) -> list[PriceListEntry]:
        rows = self.conn.execute(
            "SELECT * FROM price_list WHERE product_id = ? ORDER BY valid_from", (product_id,)
        ).fetchall()
        return [self._price(r) for r in rows]

    def price_increases(self, start: date, end: date) -> list[PriceIncrease]:
        """Price-list entries that start in [start, end] and are higher than the entry before."""
        rows = self.conn.execute(
            """
            SELECT s.supplier_id, s.name AS supplier_name, p.product_id, p.name AS product_name,
                   old.unit_price AS old_price, new.unit_price AS new_price, new.valid_from,
                   old.entry_id AS old_entry_id, new.entry_id AS new_entry_id
            FROM price_list AS new
            JOIN price_list AS old
              ON old.product_id = new.product_id AND old.supplier_id = new.supplier_id
             AND old.valid_to < new.valid_from
             AND old.valid_to = (SELECT MAX(valid_to) FROM price_list
                                 WHERE product_id = new.product_id
                                   AND supplier_id = new.supplier_id
                                   AND valid_to < new.valid_from)
            JOIN products AS p ON p.product_id = new.product_id
            JOIN suppliers AS s ON s.supplier_id = new.supplier_id
            WHERE new.valid_from BETWEEN ? AND ? AND new.unit_price > old.unit_price
            ORDER BY s.supplier_id, p.product_id
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        return [
            PriceIncrease(
                supplier_id=r["supplier_id"],
                supplier_name=r["supplier_name"],
                product_id=r["product_id"],
                product_name=r["product_name"],
                old_price=r["old_price"],
                new_price=r["new_price"],
                change_pct=round((r["new_price"] / r["old_price"] - 1) * 100, 1),
                valid_from=date.fromisoformat(r["valid_from"]),
                old_entry_id=r["old_entry_id"],
                new_entry_id=r["new_entry_id"],
            )
            for r in rows
        ]

    def vat_rule(self, category: VatCategory, day: date) -> VatRule | None:
        """The VAT rule for a category on a delivery date; None if the table has no rule."""
        rows = self.conn.execute(
            """SELECT * FROM vat_rules WHERE category = ?
               AND (valid_from IS NULL OR valid_from <= ?) AND (valid_to IS NULL OR ? <= valid_to)""",
            (category, day.isoformat(), day.isoformat()),
        ).fetchall()
        return self._vat(rows[0]) if len(rows) == 1 else None

    def clauses(self, supplier_id: str) -> list[Clause]:
        rows = self.conn.execute(
            "SELECT * FROM contract_clauses WHERE supplier_id = ? ORDER BY clause_id",
            (supplier_id,),
        ).fetchall()
        return [Clause(**dict(r)) for r in rows]

    def all_clauses(self) -> list[Clause]:
        rows = self.conn.execute("SELECT * FROM contract_clauses ORDER BY clause_id").fetchall()
        return [Clause(**dict(r)) for r in rows]

    def clause(self, clause_id: str) -> Clause | None:
        row = self.conn.execute(
            "SELECT * FROM contract_clauses WHERE clause_id = ?", (clause_id,)
        ).fetchone()
        return Clause(**dict(row)) if row else None

    def clause_by_topic(self, supplier_id: str, topic: str) -> Clause | None:
        row = self.conn.execute(
            "SELECT * FROM contract_clauses WHERE supplier_id = ? AND topic = ?",
            (supplier_id, topic),
        ).fetchone()
        return Clause(**dict(row)) if row else None

    # ------------------------------------------------------------ deliveries

    def delivery_note(self, note_id: str | None) -> DeliveryNote | None:
        row = self.conn.execute(
            "SELECT * FROM delivery_notes WHERE note_id = ?", (note_id,)
        ).fetchone()
        if row is None:
            return None
        lines = self.conn.execute(
            "SELECT product_id, qty, unit FROM delivery_note_lines WHERE note_id = ? "
            "ORDER BY rowid",
            (note_id,),
        ).fetchall()
        return DeliveryNote(
            note_id=row["note_id"],
            supplier_id=row["supplier_id"],
            delivery_date=date.fromisoformat(row["delivery_date"]),
            lines=[DeliveryNoteLine(**dict(x)) for x in lines],
        )

    # ------------------------------------------------------------ invoice archive

    def archive_invoice(self, inv: ExtractedInvoice, supplier_id: str | None) -> None:
        """Store an invoice as read. Insert-only: an id can be archived once."""
        seq = self.conn.execute(
            "SELECT COALESCE(MAX(arrival_seq), 0) + 1 FROM invoices"
        ).fetchone()[0]
        with self.conn:
            self.conn.execute(
                """INSERT INTO invoices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    inv.invoice_id,
                    seq,
                    supplier_id,
                    inv.document_type,
                    inv.number,
                    _iso(inv.invoice_date),
                    _iso(inv.delivery_date),
                    inv.delivery_note_id,
                    inv.credits_invoice_number,
                    inv.total_excl_vat,
                    inv.vat_total,
                    inv.total_incl_vat,
                    inv.confidence,
                ),
            )
            self.conn.executemany(
                "INSERT INTO invoice_lines VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (
                        inv.invoice_id,
                        i,
                        x.description,
                        x.product_id,
                        x.qty,
                        x.unit,
                        x.unit_price,
                        x.vat_rate,
                        x.line_total,
                    )
                    for i, x in enumerate(inv.lines)
                ],
            )

    def invoice(self, invoice_id: str) -> ExtractedInvoice | None:
        row = self.conn.execute(
            "SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)
        ).fetchone()
        if row is None:
            return None
        lines = self.conn.execute(
            "SELECT * FROM invoice_lines WHERE invoice_id = ? ORDER BY line_index", (invoice_id,)
        ).fetchall()
        supplier = self.conn.execute(
            "SELECT org_number FROM suppliers WHERE supplier_id = ?", (row["supplier_id"],)
        ).fetchone()
        return ExtractedInvoice(
            invoice_id=row["invoice_id"],
            document_type=row["document_type"],
            supplier_org_number=supplier["org_number"] if supplier else None,
            number=row["number"],
            invoice_date=_day(row["invoice_date"]),
            delivery_date=_day(row["delivery_date"]),
            delivery_note_id=row["delivery_note_id"],
            credits_invoice_number=row["credits_invoice_number"],
            lines=[
                ExtractedLine(
                    description=x["description"],
                    product_id=x["product_id"],
                    qty=x["qty"],
                    unit=x["unit"],
                    unit_price=x["unit_price"],
                    vat_rate=x["vat_rate"],
                    line_total=x["line_total"],
                )
                for x in lines
            ],
            total_excl_vat=row["total_excl_vat"],
            vat_total=row["vat_total"],
            total_incl_vat=row["total_incl_vat"],
            confidence=row["confidence"],
        )

    def find_earlier_invoice(
        self,
        supplier_id: str,
        number: str | None,
        invoice_date: date | None,
        total_incl_vat: float | None,
        tolerance: float = 0.011,
    ) -> tuple[str, str] | None:
        """Duplicate lookup in the archive: (invoice_id, reason) of the first earlier invoice
        with the same supplier and number, or the same supplier, date and total."""
        if number is not None:
            row = self.conn.execute(
                """SELECT invoice_id FROM invoices WHERE supplier_id = ? AND number = ?
                   AND document_type = 'invoice' ORDER BY arrival_seq LIMIT 1""",
                (supplier_id, number),
            ).fetchone()
            if row:
                return row["invoice_id"], "same_number"
        if invoice_date is not None and total_incl_vat is not None:
            row = self.conn.execute(
                """SELECT invoice_id FROM invoices WHERE supplier_id = ? AND invoice_date = ?
                   AND ABS(total_incl_vat - ?) <= ? AND document_type = 'invoice'
                   ORDER BY arrival_seq LIMIT 1""",
                (supplier_id, invoice_date.isoformat(), total_incl_vat, tolerance),
            ).fetchone()
            if row:
                return row["invoice_id"], "same_date_and_total"
        return None

    # ------------------------------------------------------------ flags and audit

    def propose_flag(self, flag: Flag, proposed_by: str) -> int:
        """Add a flag to the review queue. It is only a proposal until a human decides."""
        with self.conn:
            cursor = self.conn.execute(
                """INSERT INTO flags (invoice_id, type, line_index, evidence_kind, evidence_ref,
                   clause_ref, message, proposed_by) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    flag.invoice_id,
                    flag.type,
                    flag.line_index,
                    flag.evidence.kind,
                    flag.evidence.ref,
                    flag.clause_ref,
                    flag.message,
                    proposed_by,
                ),
            )
            self._audit(
                proposed_by,
                "propose_flag",
                {"flag_id": cursor.lastrowid, "invoice_id": flag.invoice_id, "type": flag.type},
            )
        return cursor.lastrowid

    def decide_flag(self, flag_id: int, approve: bool, reviewer: str) -> None:
        """The human decision. Not an agent tool and not an MCP tool."""
        if not reviewer.strip():
            raise ValueError("a decision needs a named reviewer")
        status = "approved" if approve else "rejected"
        with self.conn:
            changed = self.conn.execute(
                """UPDATE flags SET status = ?, reviewer = ?, decided_at = ?
                   WHERE flag_id = ? AND status = 'pending_review'""",
                (status, reviewer, datetime.now(UTC).isoformat(timespec="seconds"), flag_id),
            ).rowcount
            if changed != 1:
                raise ValueError(f"flag {flag_id} does not exist or was already decided")
            self._audit(reviewer, f"{status}_flag", {"flag_id": flag_id})

    def flags(self, status: str | None = None, invoice_id: str | None = None) -> list[StoredFlag]:
        query, params = "SELECT * FROM flags WHERE 1=1", []
        if status is not None:
            query, params = query + " AND status = ?", [*params, status]
        if invoice_id is not None:
            query, params = query + " AND invoice_id = ?", [*params, invoice_id]
        rows = self.conn.execute(query + " ORDER BY flag_id", params).fetchall()
        return [
            StoredFlag(
                flag_id=r["flag_id"],
                flag=Flag(
                    invoice_id=r["invoice_id"],
                    type=r["type"],
                    line_index=r["line_index"],
                    evidence={"kind": r["evidence_kind"], "ref": r["evidence_ref"]},
                    clause_ref=r["clause_ref"],
                    message=r["message"],
                ),
                proposed_by=r["proposed_by"],
                status=r["status"],
                reviewer=r["reviewer"],
                decided_at=r["decided_at"],
            )
            for r in rows
        ]

    def audit_log(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM audit_log ORDER BY seq").fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]

    def _audit(self, actor: str, action: str, detail: dict) -> None:
        self.conn.execute(
            "INSERT INTO audit_log (at, actor, action, detail) VALUES (?,?,?,?)",
            (
                datetime.now(UTC).isoformat(timespec="seconds"),
                actor,
                action,
                json.dumps(detail, sort_keys=True),
            ),
        )

    # ------------------------------------------------------------ row -> model

    @staticmethod
    def _supplier(r: sqlite3.Row) -> Supplier:
        return Supplier(**dict(r))

    @staticmethod
    def _product(r: sqlite3.Row) -> Product:
        return Product(
            product_id=r["product_id"],
            supplier_id=r["supplier_id"],
            name=r["name"],
            names={"sv": r["name_sv"], "en": r["name_en"], "it": r["name_it"]},
            unit=r["unit"],
            pack_weight_kg=r["pack_weight_kg"],
            vat_category=r["vat_category"],
        )

    @staticmethod
    def _price(r: sqlite3.Row) -> PriceListEntry:
        return PriceListEntry(
            **{**dict(r), "valid_from": r["valid_from"], "valid_to": r["valid_to"]}
        )

    @staticmethod
    def _vat(r: sqlite3.Row) -> VatRule:
        return VatRule(**dict(r))
