"""Shared output types. Every extractor and every detector (rule baseline, agent) produces these,
so the evaluation can compare them on equal terms."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from invoice_agent.data_gen.models import AnomalyType, Evidence, Language


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractedLine(_Strict):
    description: str
    qty: float
    unit: str  # normalised to kg / l / pcs / bottle
    unit_price: float
    vat_rate: float
    line_total: float


class ExtractedInvoice(_Strict):
    """What an extractor read from one PDF. Fields it could not read are None."""

    invoice_id: str  # our own id (file name), not printed on the document
    language: Language | None = None
    document_type: Literal["invoice", "credit_note"] | None = None
    supplier_org_number: str | None = None
    number: str | None = None
    invoice_date: date | None = None
    delivery_date: date | None = None
    delivery_note_id: str | None = None
    credits_invoice_number: str | None = None
    lines: list[ExtractedLine] = Field(default_factory=list)
    total_excl_vat: float | None = None
    vat_total: float | None = None
    total_incl_vat: float | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    problems: list[str] = Field(default_factory=list)  # why confidence is low, for the reviewer


class Flag(_Strict):
    """A proposed anomaly. Only a proposal: a human decides (see CLAUDE.md hard rules)."""

    invoice_id: str
    type: AnomalyType
    line_index: int | None
    evidence: Evidence
    clause_ref: str | None = None
    message: str
