"""SQLite schema. One file, one connection helper.

Hard rules enforced by the database itself, not only by the code (defence in depth):
- price lists, contracts, VAT rules and archived invoices can be inserted once and never
  updated or deleted (triggers abort the statement);
- a flag starts as 'pending_review'; only a human decision moves it to 'approved' or 'rejected',
  and a decision is final;
- the audit log is append-only.
Dates are ISO text (YYYY-MM-DD). Money is REAL, rounded to öre by the code: fine for a prototype,
a production system would store integer öre.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS suppliers (
    supplier_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('supplier', 'competitor')),
    language TEXT NOT NULL,
    layout TEXT NOT NULL,
    org_number TEXT NOT NULL UNIQUE,
    iban TEXT NOT NULL,
    address TEXT NOT NULL,
    invoice_prefix TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    product_id TEXT PRIMARY KEY,
    supplier_id TEXT NOT NULL REFERENCES suppliers(supplier_id),
    name TEXT NOT NULL,
    name_sv TEXT NOT NULL,
    name_en TEXT NOT NULL,
    name_it TEXT NOT NULL,
    unit TEXT NOT NULL,
    pack_weight_kg REAL,
    vat_category TEXT NOT NULL CHECK (vat_category IN ('food', 'alcohol', 'non_food'))
);

CREATE TABLE IF NOT EXISTS price_list (
    entry_id TEXT PRIMARY KEY,
    supplier_id TEXT NOT NULL REFERENCES suppliers(supplier_id),
    product_id TEXT NOT NULL REFERENCES products(product_id),
    unit TEXT NOT NULL,
    unit_price REAL NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vat_rules (
    rule_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    rate REAL NOT NULL,
    source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contract_clauses (
    clause_id TEXT PRIMARY KEY,
    supplier_id TEXT NOT NULL REFERENCES suppliers(supplier_id),
    topic TEXT NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS delivery_notes (
    note_id TEXT PRIMARY KEY,
    supplier_id TEXT NOT NULL REFERENCES suppliers(supplier_id),
    delivery_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS delivery_note_lines (
    note_id TEXT NOT NULL REFERENCES delivery_notes(note_id),
    product_id TEXT NOT NULL REFERENCES products(product_id),
    qty REAL NOT NULL,
    unit TEXT NOT NULL,
    PRIMARY KEY (note_id, product_id)
);

-- Archive of invoices as they were read (extracted), in arrival order.
CREATE TABLE IF NOT EXISTS invoices (
    invoice_id TEXT PRIMARY KEY,
    arrival_seq INTEGER NOT NULL UNIQUE,
    supplier_id TEXT REFERENCES suppliers(supplier_id),
    document_type TEXT,
    number TEXT,
    invoice_date TEXT,
    delivery_date TEXT,
    delivery_note_id TEXT,
    credits_invoice_number TEXT,
    total_excl_vat REAL,
    vat_total REAL,
    total_incl_vat REAL,
    confidence REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS invoice_lines (
    invoice_id TEXT NOT NULL REFERENCES invoices(invoice_id),
    line_index INTEGER NOT NULL,
    description TEXT NOT NULL,
    product_id TEXT,
    qty REAL NOT NULL,
    unit TEXT NOT NULL,
    unit_price REAL NOT NULL,
    vat_rate REAL NOT NULL,
    line_total REAL NOT NULL,
    PRIMARY KEY (invoice_id, line_index)
);

-- Proposed anomalies. The agent can only add rows with status 'pending_review'.
CREATE TABLE IF NOT EXISTS flags (
    flag_id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id TEXT NOT NULL,
    type TEXT NOT NULL,
    line_index INTEGER,
    evidence_kind TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    clause_ref TEXT,
    message TEXT NOT NULL,
    quote TEXT,
    explanation TEXT,
    proposed_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_review'
        CHECK (status IN ('pending_review', 'approved', 'rejected')),
    reviewer TEXT,
    decided_at TEXT,
    CHECK ((status = 'pending_review') = (reviewer IS NULL))
);

CREATE TABLE IF NOT EXISTS audit_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


def _read_only_triggers() -> str:
    statements = []
    for table, message in (
        ("price_list", "price lists are read-only"),
        ("contract_clauses", "contracts are read-only"),
        ("vat_rules", "VAT rules are read-only"),
        ("invoices", "archived invoices are read-only"),
        ("invoice_lines", "archived invoices are read-only"),
        ("audit_log", "the audit log is append-only"),
    ):
        for event in ("UPDATE", "DELETE"):
            statements.append(
                f"CREATE TRIGGER IF NOT EXISTS {table}_no_{event.lower()} BEFORE {event} ON "
                f"{table} BEGIN SELECT RAISE(ABORT, '{message}'); END;"
            )
    statements += [
        # A flag can change only once: from pending_review to a human decision.
        (
            "CREATE TRIGGER IF NOT EXISTS flags_decision_is_final BEFORE UPDATE ON flags "
            "WHEN OLD.status != 'pending_review' "
            "BEGIN SELECT RAISE(ABORT, 'a flag decision is final'); END;"
        ),
        (
            "CREATE TRIGGER IF NOT EXISTS flags_only_status_changes BEFORE UPDATE ON flags "
            "WHEN NEW.invoice_id != OLD.invoice_id OR NEW.type != OLD.type "
            "OR NEW.evidence_ref != OLD.evidence_ref OR NEW.message != OLD.message "
            "BEGIN SELECT RAISE(ABORT, 'flag content cannot be edited'); END;"
        ),
        (
            "CREATE TRIGGER IF NOT EXISTS flags_no_delete BEFORE DELETE ON flags "
            "BEGIN SELECT RAISE(ABORT, 'flags cannot be deleted'); END;"
        ),
    ]
    return "\n".join(statements)


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    """Open (and create if needed) the database. ':memory:' gives a throwaway database."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.executescript(_read_only_triggers())
    return conn
