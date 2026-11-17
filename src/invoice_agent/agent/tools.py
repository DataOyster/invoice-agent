"""The agent's tools. All read-only except `submit_review`, which only hands in a proposal.

There is deliberately no tool to pay, approve, reject, or edit prices, contracts or invoices
(CLAUDE.md hard rules). The supplier is fixed by the code from the invoice's organisation number,
not chosen by the model, so a search can never wander into another supplier's contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from invoice_agent.data_gen.models import Supplier
from invoice_agent.db.repository import Repository
from invoice_agent.evals.baseline import RuleBaseline
from invoice_agent.rag.index import ContractIndex
from invoice_agent.schemas import ExtractedInvoice

_MAPPING = {
    "type": "array",
    "description": "Catalog product for invoice lines that have no product_id yet",
    "items": {
        "type": "object",
        "properties": {
            "line_index": {"type": "integer"},
            "product_id": {"type": "string"},
        },
        "required": ["line_index", "product_id"],
        "additionalProperties": False,
    },
}

TOOL_SPECS = [
    {
        "name": "list_supplier_products",
        "description": "The catalog of this invoice's supplier: product ids, names in "
        "English, Swedish and Italian, unit and kg per case. Use it to match invoice lines "
        "whose product_id is null.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_checks",
        "description": "Run the five deterministic checks (price, duplicate, VAT, quantity, "
        "unit) with your product mapping added. Returns the accepted mapping, rejected "
        "mappings with the reason, and the findings. Only these findings can become flags.",
        "input_schema": {
            "type": "object",
            "properties": {"product_mapping": _MAPPING},
            "required": ["product_mapping"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_contract",
        "description": "Search this supplier's contract. Returns the 3 best clauses with id, "
        "title and full text. Use plain English and the product's English catalog name.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "submit_review",
        "description": "Finish: hand in your product mapping and, for each finding from "
        "run_checks, a short explanation for the reviewer and the supporting clause with a "
        "verbatim quote. A human decides on every flag.",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_mapping": _MAPPING,
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string"},
                            "line_index": {"type": ["integer", "null"]},
                            "clause_id": {"type": ["string", "null"]},
                            "quote": {"type": ["string", "null"]},
                            "explanation": {"type": "string"},
                        },
                        "required": ["type", "line_index", "clause_id", "quote", "explanation"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["product_mapping", "findings"],
            "additionalProperties": False,
        },
    },
]
for _spec in TOOL_SPECS:
    _spec["strict"] = True  # arguments are validated against the schema by the API

TOOL_NAMES = frozenset(spec["name"] for spec in TOOL_SPECS)


@dataclass
class ToolBox:
    """Executes tool calls for one invoice. Holds no state the model could abuse."""

    repo: Repository
    index: ContractIndex
    invoice: ExtractedInvoice
    supplier: Supplier
    accepted_mapping: dict[int, str] = field(default_factory=dict)

    def execute(self, name: str, args: dict) -> str:
        if name == "list_supplier_products":
            return self._products()
        if name == "run_checks":
            return self._run_checks(args.get("product_mapping", []))
        if name == "search_contract":
            return self._search(str(args.get("query", "")))
        raise ValueError(f"unknown tool {name!r}")

    def validate_mapping(self, mapping: list[dict]) -> tuple[dict[int, str], list[str]]:
        """Accept a mapping only for lines without a product, to a product of this supplier,
        with the same unit as printed. Everything else is rejected with a reason."""
        accepted, rejected = {}, []
        for item in mapping:
            i, pid = item.get("line_index"), item.get("product_id")
            product = self.repo.product(pid)
            if not isinstance(i, int) or not 0 <= i < len(self.invoice.lines):
                rejected.append(f"line {i}: no such line")
            elif self.invoice.lines[i].product_id is not None:
                rejected.append(f"line {i}: already mapped by the extractor")
            elif product is None or product.supplier_id != self.supplier.supplier_id:
                rejected.append(f"line {i}: {pid!r} is not in this supplier's catalog")
            elif product.unit != self.invoice.lines[i].unit:
                rejected.append(f"line {i}: unit {self.invoice.lines[i].unit} != {product.unit}")
            else:
                accepted[i] = product.product_id
        return accepted, rejected

    def mapped_invoice(self, mapping: dict[int, str]) -> ExtractedInvoice:
        lines = [
            line.model_copy(update={"product_id": mapping[i]}) if i in mapping else line
            for i, line in enumerate(self.invoice.lines)
        ]
        return self.invoice.model_copy(update={"lines": lines})

    def _products(self) -> str:
        return json.dumps(
            [
                {
                    "product_id": p.product_id,
                    "names": p.names,
                    "unit": p.unit,
                    "kg_per_case": p.pack_weight_kg,
                }
                for p in self.repo.products(self.supplier.supplier_id)
            ],
            ensure_ascii=False,
        )

    def _run_checks(self, mapping: list[dict]) -> str:
        accepted, rejected = self.validate_mapping(mapping)
        self.accepted_mapping = accepted
        flags = RuleBaseline(self.repo).check(self.mapped_invoice(accepted))
        return json.dumps(
            {
                "accepted_mapping": {str(k): v for k, v in accepted.items()},
                "rejected_mapping": rejected,
                "unmapped_lines": [
                    i
                    for i, line in enumerate(self.mapped_invoice(accepted).lines)
                    if line.product_id is None
                ],
                "findings": [
                    {
                        "type": f.type,
                        "line_index": f.line_index,
                        "evidence": f.evidence.model_dump(),
                        "message": f.message,
                    }
                    for f in flags
                ],
            },
            ensure_ascii=False,
        )

    def _search(self, query: str) -> str:
        hits = self.index.search(query, supplier_id=self.supplier.supplier_id, k=3)
        return json.dumps(
            [
                {"clause_id": h.clause.clause_id, "title": h.clause.title, "text": h.clause.text}
                for h in hits
            ],
            ensure_ascii=False,
        )
