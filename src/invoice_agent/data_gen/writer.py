"""Write a generated Dataset to disk. All JSON is sorted and written with "\\n" line endings,
so the same seed gives byte-identical files on every machine."""

from __future__ import annotations

import json
import random
from pathlib import Path

from pydantic import BaseModel

from invoice_agent.data_gen.generator import Dataset
from invoice_agent.data_gen.pdf_render import render_pdf


def _dump(obj) -> str:
    if isinstance(obj, BaseModel):
        obj = obj.model_dump(mode="json")
    elif isinstance(obj, list):
        obj = [x.model_dump(mode="json") if isinstance(x, BaseModel) else x for x in obj]
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def contract_markdown(ds: Dataset, supplier_id: str) -> str:
    """Contract text (the RAG material) plus Annex A with the agreed prices."""
    supplier = next(s for s in ds.suppliers if s.supplier_id == supplier_id)
    names = {p.product_id: p.name for p in ds.products}
    out = [f"# Supply agreement: {supplier.name} ({supplier_id})", ""]
    for clause in ds.contracts[supplier_id]:
        out += [f"## {clause.clause_id} {clause.title}", "", clause.text, ""]
    out += [
        "## Annex A: agreed prices (SEK excl. VAT)",
        "",
        "| Entry | Product | Unit | Price | Valid from | Valid to |",
        "|---|---|---|---|---|---|",
    ]
    for e in ds.price_list:
        if e.supplier_id == supplier_id:
            out.append(
                f"| {e.entry_id} | {names[e.product_id]} | {e.unit} | {e.unit_price:.2f} "
                f"| {e.valid_from} | {e.valid_to} |"
            )
    return "\n".join(out) + "\n"


def write_dataset(ds: Dataset, out_dir: Path) -> None:
    out_dir = Path(out_dir)
    _write_text(out_dir / "suppliers.json", _dump(ds.suppliers))
    _write_text(out_dir / "products.json", _dump(ds.products))
    _write_text(out_dir / "price_lists.json", _dump(ds.price_list))
    _write_text(out_dir / "competitor_prices.json", _dump(ds.competitor_prices))
    _write_text(out_dir / "vat_table.json", _dump(ds.vat_table))
    _write_text(out_dir / "delivery_notes.json", _dump(ds.delivery_notes))
    clauses = [c for sid in sorted(ds.contracts) for c in ds.contracts[sid]]
    _write_text(out_dir / "contract_clauses.json", _dump(clauses))
    for sid in sorted(ds.contracts):
        _write_text(out_dir / "contracts" / f"{sid}.md", contract_markdown(ds, sid))

    gt_lines = [inv.model_dump_json() for inv in ds.invoices]
    _write_text(out_dir / "ground_truth.jsonl", "\n".join(gt_lines) + "\n")

    # Separate rng for the noise angles, derived from the seed, so PDFs never shift the data.
    rng = random.Random(f"pdf-noise-{ds.config.seed}")
    suppliers = {s.supplier_id: s for s in ds.suppliers}
    pdf_dir = out_dir / "invoices"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    for inv in ds.invoices:
        angle = rng.choice((-1.2, -0.8, -0.5, 0.5, 0.8, 1.2)) if inv.scan_noise else 0.0
        mode = inv.unreadable_reason or "normal"
        (out_dir / inv.file).write_bytes(render_pdf(inv, suppliers[inv.supplier_id], angle, mode))

    summary = {
        "seed": ds.config.seed,
        "documents": len(ds.invoices),
        "with_anomaly": sum(1 for i in ds.invoices if i.anomalies),
        "traps": sum(1 for i in ds.invoices if i.is_trap),
        "unreadable": sum(1 for i in ds.invoices if not i.readable),
        "scan_noise": sum(1 for i in ds.invoices if i.scan_noise),
        "delivery_notes": len(ds.delivery_notes),
    }
    _write_text(out_dir / "manifest.json", _dump(summary))
