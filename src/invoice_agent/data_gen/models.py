"""Pydantic models for the synthetic dataset.

These models are the contract between the generator and everything that reads the dataset
(evaluation, data layer, agent). If a ground-truth record does not validate here, it is a bug.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Language = Literal["sv", "en", "it"]
Unit = Literal["kg", "l", "pcs", "bottle"]
VatCategory = Literal["food", "alcohol", "non_food"]
AnomalyType = Literal[
    "price_above_agreement",
    "duplicate",
    "wrong_vat",
    "quantity_mismatch",
    "unit_confusion",
]
EvidenceKind = Literal["price_list", "clause", "delivery_note", "vat_rule", "invoice"]

ANOMALY_TYPES: tuple[AnomalyType, ...] = (
    "price_above_agreement",
    "duplicate",
    "wrong_vat",
    "quantity_mismatch",
    "unit_confusion",
)

# Money is rounded to whole öre (0.01 SEK). Totals must agree within this amount.
MONEY_TOLERANCE = 0.011


class Strict(BaseModel):
    """Base model: unknown fields are an error, so typos in the data never pass silently."""

    model_config = ConfigDict(extra="forbid")


class Supplier(Strict):
    supplier_id: str
    name: str
    category: str
    role: Literal["supplier", "competitor"] = "supplier"
    language: Language
    layout: Literal["A", "B", "C"]
    org_number: str
    iban: str
    address: str
    invoice_prefix: str


class Product(Strict):
    product_id: str
    supplier_id: str
    name: str  # canonical English name, used for retrieval queries (see ROADMAP task 6)
    names: dict[Language, str]  # how the product is written on invoices, per language
    unit: Unit  # the unit the agreed price refers to
    pack_weight_kg: float | None = None  # kg per case, for kg-priced products sold in cases
    vat_category: VatCategory


class PriceListEntry(Strict):
    entry_id: str
    supplier_id: str
    product_id: str
    unit: Unit
    unit_price: float
    valid_from: date
    valid_to: date


class VatRule(Strict):
    rule_id: str
    category: VatCategory
    valid_from: date | None  # None = no lower bound
    valid_to: date | None  # None = no upper bound
    rate: float
    source: str


class Clause(Strict):
    clause_id: str
    supplier_id: str
    topic: str
    title: str
    text: str


class DeliveryNoteLine(Strict):
    product_id: str
    qty: float
    unit: Unit


class DeliveryNote(Strict):
    note_id: str
    supplier_id: str
    delivery_date: date
    lines: list[DeliveryNoteLine]


class InvoiceLine(Strict):
    product_id: str
    description: str  # in the invoice language
    qty: float
    unit: Unit
    unit_price: float
    vat_rate: float
    line_total: float  # qty * unit_price, rounded to öre


class Evidence(Strict):
    kind: EvidenceKind
    ref: str  # id of the price-list entry, clause, delivery note, VAT rule or invoice


class Anomaly(Strict):
    type: AnomalyType
    line_index: int | None  # None = the anomaly concerns the whole invoice
    evidence: Evidence
    clause_ref: str | None  # contract clause a reviewer should read (retrieval label)
    expected: float | str | None
    found: float | str | None
    detail: str


class InvoiceGroundTruth(Strict):
    """What a perfect reader would extract from one PDF, plus the injected anomalies."""

    invoice_id: str
    file: str
    language: Language
    layout: Literal["A", "B", "C"]
    document_type: Literal["invoice", "credit_note"]
    supplier_id: str
    number: str
    invoice_date: date
    delivery_date: date
    delivery_note_id: str | None
    credits_invoice_number: str | None = None
    lines: list[InvoiceLine] = Field(min_length=1)
    total_excl_vat: float
    vat_total: float
    total_incl_vat: float
    anomalies: list[Anomaly]
    is_trap: bool
    trap_reason: str | None = None
    scan_noise: bool = False
    readable: bool = True  # False = image-only or corrupted PDF; must go to manual review
    unreadable_reason: Literal["blurred_image_only", "truncated_file"] | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> InvoiceGroundTruth:
        lines_sum = sum(line.line_total for line in self.lines)
        if abs(lines_sum - self.total_excl_vat) > MONEY_TOLERANCE:
            raise ValueError(f"lines sum {lines_sum} != total_excl_vat {self.total_excl_vat}")
        if abs(self.total_excl_vat + self.vat_total - self.total_incl_vat) > MONEY_TOLERANCE:
            raise ValueError("total_excl_vat + vat_total != total_incl_vat")
        if len(self.anomalies) > 1:
            raise ValueError("dataset rule: at most one injected anomaly per invoice")
        if self.is_trap and self.anomalies:
            raise ValueError("a trap case must not carry an anomaly")
        if self.is_trap != (self.trap_reason is not None):
            raise ValueError("trap_reason is required exactly when is_trap is true")
        if (self.document_type == "credit_note") != (self.credits_invoice_number is not None):
            raise ValueError("credit notes, and only credit notes, reference an invoice")
        if self.readable != (self.unreadable_reason is None):
            raise ValueError("unreadable_reason is required exactly when readable is false")
        if self.delivery_date > self.invoice_date:
            raise ValueError("delivery_date after invoice_date")
        return self
