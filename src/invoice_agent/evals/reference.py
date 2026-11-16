"""Load the generated dataset from disk into memory.

Temporary: from task 4 the same reference data comes from the SQLite data layer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from invoice_agent.data_gen.models import (
    Clause,
    DeliveryNote,
    InvoiceGroundTruth,
    PriceListEntry,
    Product,
    Supplier,
    VatRule,
)


@dataclass
class ReferenceData:
    suppliers: list[Supplier]
    products: list[Product]
    price_list: list[PriceListEntry]
    vat_table: list[VatRule]
    delivery_notes: list[DeliveryNote]
    clauses: list[Clause]
    ground_truth: list[InvoiceGroundTruth]


def _load(path: Path, model):
    return [model.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]


def load_reference(data_dir: Path) -> ReferenceData:
    data_dir = Path(data_dir)
    gt_lines = (data_dir / "ground_truth.jsonl").read_text(encoding="utf-8").splitlines()
    return ReferenceData(
        suppliers=_load(data_dir / "suppliers.json", Supplier),
        products=_load(data_dir / "products.json", Product),
        price_list=_load(data_dir / "price_lists.json", PriceListEntry),
        vat_table=_load(data_dir / "vat_table.json", VatRule),
        delivery_notes=_load(data_dir / "delivery_notes.json", DeliveryNote),
        clauses=_load(data_dir / "contract_clauses.json", Clause),
        ground_truth=[InvoiceGroundTruth.model_validate_json(x) for x in gt_lines if x],
    )
