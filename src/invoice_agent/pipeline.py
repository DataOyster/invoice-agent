"""One invoice, end to end, with every guardrail in place.

    PDF -> extraction (+ confidence) -> injection tripwire -> review (checks + agent)
        -> flag validation -> review queue (pending a human) -> audit log -> archive

Nothing here approves, rejects or pays. The output is a set of pending flags and, when needed,
a manual-review reason. A person decides (Repository.decide_flag, called only from the UI).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
from pdfplumber.utils.exceptions import PdfminerException

from invoice_agent.agent.loop import AgentRun
from invoice_agent.agent.review import AgentRunner, InvoiceReview, review_invoice
from invoice_agent.db.repository import Repository
from invoice_agent.extraction.llm import LLMClient, catalog_prompt, extract_with_llm
from invoice_agent.extraction.rules import extract_pdf
from invoice_agent.guardrails.injection import InjectionHit, scan_for_injection
from invoice_agent.guardrails.masking import get_logger
from invoice_agent.guardrails.validation import validate_flag
from invoice_agent.rag.index import ContractIndex
from invoice_agent.schemas import ExtractedInvoice

log = get_logger("invoice_agent.pipeline")

Extractor = Callable[[Path, str], ExtractedInvoice]


@dataclass
class PipelineResult:
    review: InvoiceReview
    flag_ids: list[int] = field(default_factory=list)  # pending flags created in the queue
    discarded: list[str] = field(default_factory=list)  # flags or citations removed, and why
    injection: list[InjectionHit] = field(default_factory=list)


def pdf_text(path: Path) -> str:
    try:
        with pdfplumber.open(path) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except PdfminerException:
        return ""


def map_products_by_name(inv: ExtractedInvoice, repo: Repository) -> ExtractedInvoice:
    """Exact-name product matching (any language, case-insensitive), as in the rule baseline."""
    supplier = repo.supplier_by_org_number(inv.supplier_org_number)
    if supplier is None:
        return inv
    by_name = {
        n.casefold(): p.product_id
        for p in repo.products(supplier.supplier_id)
        for n in p.names.values()
    }
    lines = [
        x
        if x.product_id
        else x.model_copy(update={"product_id": by_name.get(x.description.casefold())})
        for x in inv.lines
    ]
    return inv.model_copy(update={"lines": lines})


def rule_extractor(repo: Repository) -> Extractor:
    return lambda path, invoice_id: map_products_by_name(extract_pdf(path, invoice_id), repo)


def claude_extractor(client: LLMClient, repo: Repository) -> Extractor:
    catalog = catalog_prompt(repo.suppliers(), repo.products())
    known = {p.product_id for p in repo.products()}

    def extract(path: Path, invoice_id: str) -> ExtractedInvoice:
        inv, _ = extract_with_llm(path.read_bytes(), invoice_id, client, catalog, known)
        return inv

    return extract


def _skip_agent(_toolbox) -> AgentRun:
    return AgentRun(submitted=None, stopped_reason="skipped_injection")


def process_invoice(
    pdf_path: Path,
    invoice_id: str,
    repo: Repository,
    index: ContractIndex,
    extract: Extractor,
    run_agent: AgentRunner,
    review_threshold: float = 0.9,
    actor: str = "pipeline",
) -> PipelineResult:
    inv = extract(pdf_path, invoice_id)
    repo.record_event(
        actor,
        "extracted",
        {"invoice_id": invoice_id, "confidence": inv.confidence, "problems": inv.problems[:5]},
    )
    log.info(
        "extracted %s from %s, confidence %.2f", invoice_id, inv.supplier_org_number, inv.confidence
    )

    # Tripwire: the raw text of the PDF and every text field the extractor returned.
    scanned = "\n".join(
        [pdf_text(pdf_path), inv.number or "", *(line.description for line in inv.lines)]
    )
    hits = scan_for_injection(scanned)
    runner = run_agent
    if hits:
        runner = _skip_agent  # a suspicious document is never shown to the model
        repo.record_event(
            actor,
            "injection_suspected",
            {"invoice_id": invoice_id, "excerpts": [h.excerpt for h in hits[:3]]},
        )
        log.warning("possible prompt injection in %s", invoice_id)

    review = review_invoice(inv, repo, index, runner, review_threshold)
    if hits:
        review.status = "manual_review"
        review.reasons.append("possible prompt injection: text addressed to an AI was found")
    result = PipelineResult(review=review, injection=hits)

    supplier = repo.supplier_by_org_number(inv.supplier_org_number)
    for flag in review.flags:
        verdict = validate_flag(flag, supplier.supplier_id if supplier else "", repo)
        if verdict.reason:
            result.discarded.append(f"{flag.type} line {flag.line_index}: {verdict.reason}")
            repo.record_event(
                actor,
                "flag_changed_or_discarded",
                {"invoice_id": invoice_id, "type": flag.type, "reason": verdict.reason},
            )
        if verdict.flag is not None:
            result.flag_ids.append(repo.propose_flag(verdict.flag, proposed_by=actor))

    if review.run is not None:
        repo.record_event(
            actor,
            "agent_run",
            {
                "invoice_id": invoice_id,
                "stopped": review.run.stopped_reason,
                "steps": review.run.steps,
                "cost_usd": round(review.run.cost_usd, 5),
                "tools": [c["name"] for c in review.run.tool_calls],
            },
        )
    if review.status == "manual_review":
        repo.record_event(
            actor, "sent_to_manual_review", {"invoice_id": invoice_id, "reasons": review.reasons}
        )
    if inv.confidence >= review_threshold:
        repo.archive_invoice(inv, supplier.supplier_id if supplier else None)
    return result
