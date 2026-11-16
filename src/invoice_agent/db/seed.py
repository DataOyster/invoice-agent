"""Load the reference data (suppliers, products, prices, VAT, contracts, delivery notes) into the
database. An administrative step run once per dataset; not available to the agent or MCP."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from invoice_agent.db.repository import Repository
from invoice_agent.db.schema import connect
from invoice_agent.evals.reference import ReferenceData, load_reference


def seed_reference(conn: sqlite3.Connection, ref: ReferenceData) -> None:
    with conn:
        conn.executemany(
            "INSERT INTO suppliers VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    s.supplier_id,
                    s.name,
                    s.category,
                    s.role,
                    s.language,
                    s.layout,
                    s.org_number,
                    s.iban,
                    s.address,
                    s.invoice_prefix,
                )
                for s in ref.suppliers
            ],
        )
        conn.executemany(
            "INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    p.product_id,
                    p.supplier_id,
                    p.name,
                    p.names["sv"],
                    p.names["en"],
                    p.names["it"],
                    p.unit,
                    p.pack_weight_kg,
                    p.vat_category,
                )
                for p in ref.products
            ],
        )
        conn.executemany(
            "INSERT INTO price_list VALUES (?,?,?,?,?,?,?)",
            [
                (
                    e.entry_id,
                    e.supplier_id,
                    e.product_id,
                    e.unit,
                    e.unit_price,
                    e.valid_from.isoformat(),
                    e.valid_to.isoformat(),
                )
                for e in ref.price_list
            ],
        )
        conn.executemany(
            "INSERT INTO vat_rules VALUES (?,?,?,?,?,?)",
            [
                (
                    r.rule_id,
                    r.category,
                    r.valid_from and r.valid_from.isoformat(),
                    r.valid_to and r.valid_to.isoformat(),
                    r.rate,
                    r.source,
                )
                for r in ref.vat_table
            ],
        )
        conn.executemany(
            "INSERT INTO contract_clauses VALUES (?,?,?,?,?)",
            [(c.clause_id, c.supplier_id, c.topic, c.title, c.text) for c in ref.clauses],
        )
        conn.executemany(
            "INSERT INTO delivery_notes VALUES (?,?,?)",
            [(n.note_id, n.supplier_id, n.delivery_date.isoformat()) for n in ref.delivery_notes],
        )
        conn.executemany(
            "INSERT INTO delivery_note_lines VALUES (?,?,?,?)",
            [(n.note_id, x.product_id, x.qty, x.unit) for n in ref.delivery_notes for x in n.lines],
        )


def repository_from_reference(ref: ReferenceData, path: str | Path = ":memory:") -> Repository:
    conn = connect(path)
    seed_reference(conn, ref)
    return Repository(conn)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Create the SQLite database from a dataset.")
    parser.add_argument("--data", type=Path, default=Path("data/generated"))
    parser.add_argument("--db", type=Path, default=Path("data/invoice_agent.db"))
    args = parser.parse_args()
    if args.db.exists():
        raise SystemExit(f"{args.db} exists; delete it first to rebuild from scratch")
    repo = repository_from_reference(load_reference(args.data), args.db)
    print(f"created {args.db} with {len(repo.products())} products")


if __name__ == "__main__":
    main()
