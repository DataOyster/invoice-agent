"""Review one invoice end to end: route, run the agent if needed, assemble verified flags.

Who decides what (the hybrid design):
- the deterministic checks decide WHICH flags exist; the agent cannot add or remove one;
- the agent contributes what rules cannot do: matching oddly printed product names to the
  catalog, a plain explanation, and the supporting clause;
- every agent contribution is validated: mappings against the catalog and units, quotes
  against the stored clause text. What fails validation is dropped, never trusted.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from invoice_agent.agent.loop import AgentRun
from invoice_agent.agent.tools import ToolBox
from invoice_agent.data_gen import catalog
from invoice_agent.db.repository import Repository
from invoice_agent.evals.baseline import RuleBaseline
from invoice_agent.rag.citations import Citation, cite, verify_citation
from invoice_agent.rag.index import ContractIndex
from invoice_agent.rag.queries import expand, query_for_anomaly
from invoice_agent.schemas import ExtractedInvoice, Flag

MAX_EXPLANATION_CHARS = 400


@dataclass
class InvoiceReview:
    invoice_id: str
    status: str  # manual_review | clean | flags_proposed
    flags: list[Flag] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)  # why a human must look, besides flags
    run: AgentRun | None = None
    agent_mapping: dict[int, str] = field(default_factory=dict)
    citations_verified: int = 0  # agent quotes that passed the check
    citations_rejected: int = 0  # agent quotes that failed it (replaced by retrieval)
    findings_ignored: int = 0  # agent findings that the checks did not produce


AgentRunner = Callable[[ToolBox], AgentRun]


def unchecked_reasons(inv: ExtractedInvoice, repo: Repository) -> list[str]:
    """Checks that could not run because the reference data has no answer for that date.

    A missing VAT rule or agreed price must not pass silently as "no anomaly": the invoice goes
    to a person, who can see that the table needs updating (found by the adversarial tests).
    """
    reasons = []
    for i, line in enumerate(inv.lines):
        product = repo.product(line.product_id)
        if product is None or inv.delivery_date is None:
            continue
        if repo.vat_rule(product.vat_category, inv.delivery_date) is None:
            reasons.append(
                f"line {i}: no VAT rule for {product.vat_category} on {inv.delivery_date}: "
                "VAT not checked"
            )
        if repo.agreed_price(product.product_id, inv.delivery_date) is None:
            reasons.append(
                f"line {i}: no agreed price for {product.product_id} on {inv.delivery_date}: "
                "price not checked"
            )
    return reasons


def review_invoice(
    inv: ExtractedInvoice,
    repo: Repository,
    index: ContractIndex,
    run_agent: AgentRunner,
    review_threshold: float,
) -> InvoiceReview:
    review = InvoiceReview(invoice_id=inv.invoice_id, status="clean")
    supplier = repo.supplier_by_org_number(inv.supplier_org_number)
    if inv.confidence < review_threshold:
        review.status = "manual_review"
        review.reasons.append(f"extraction confidence {inv.confidence:.2f}: {inv.problems[:3]}")
        return review
    if supplier is None or inv.delivery_date is None:
        review.status = "manual_review"
        review.reasons.append("unknown supplier or missing delivery date")
        return review

    toolbox = ToolBox(repo, index, inv, supplier)
    unmapped = [i for i, x in enumerate(inv.lines) if x.product_id is None]
    first_pass = RuleBaseline(repo).check(inv)
    if not unmapped and not first_pass:
        review.reasons += unchecked_reasons(inv, repo)
        if review.reasons:
            review.status = "manual_review"
        return review  # nothing to explain or match: no model call, no cost

    run = run_agent(toolbox)
    review.run = run
    submitted = run.submitted or {}
    mapping_items = submitted.get("product_mapping")
    if mapping_items is None:  # no submission: keep what run_checks last accepted
        accepted = dict(toolbox.accepted_mapping)
    else:
        accepted, _ = toolbox.validate_mapping(mapping_items)
    review.agent_mapping = accepted
    mapped = toolbox.mapped_invoice(accepted)

    flags = RuleBaseline(repo).check(mapped)  # the checks decide which flags exist
    notes = {(f.get("type"), f.get("line_index")): f for f in submitted.get("findings", [])}
    produced = {(f.type, f.line_index) for f in flags}
    review.findings_ignored = sum(1 for key in notes if key not in produced)
    review.flags = [
        _annotate(
            f, notes.get((f.type, f.line_index)), mapped, supplier.supplier_id, repo, index, review
        )
        for f in flags
    ]

    still_unmapped = [i for i, x in enumerate(mapped.lines) if x.product_id is None]
    if still_unmapped:
        review.reasons.append(f"lines {still_unmapped} not matched to the catalog: not checked")
    review.reasons += unchecked_reasons(mapped, repo)
    if run.stopped_reason != "submitted":
        review.reasons.append(f"agent stopped: {run.stopped_reason}")
    if review.reasons:
        review.status = "manual_review"
    elif review.flags:
        review.status = "flags_proposed"
    return review


def _annotate(
    flag: Flag,
    note: dict | None,
    inv: ExtractedInvoice,
    supplier_id: str,
    repo: Repository,
    index: ContractIndex,
    review: InvoiceReview,
) -> Flag:
    explanation = None
    citation = None
    topic = catalog.CLAUSE_FOR_ANOMALY.get(flag.type)  # None for wrong_vat: the VAT table rules
    if note:
        explanation = (note.get("explanation") or "")[:MAX_EXPLANATION_CHARS] or None
        if note.get("clause_id") and note.get("quote"):
            candidate = Citation(note["clause_id"], "", note["quote"])
            clause = repo.clause(candidate.clause_id)
            if (
                clause
                and clause.supplier_id == supplier_id
                and clause.topic == topic  # verbatim is not enough: it must be on this topic
                and verify_citation(candidate, repo)
            ):
                citation = candidate
                review.citations_verified += 1
            else:
                review.citations_rejected += 1
    if citation is None and topic is not None:  # fall back to retrieval, quoted verbatim
        product = (
            repo.product(inv.lines[flag.line_index].product_id)
            if (flag.line_index is not None)
            else None
        )
        query = expand(query_for_anomaly(flag.type, product.name if product else None))
        hits = index.search(query, supplier_id=supplier_id, k=1)
        if hits:
            citation = cite(hits[0])
    return flag.model_copy(
        update={
            "clause_ref": citation.clause_id if citation else None,
            "quote": citation.quote if citation else None,
            "explanation": explanation,
        }
    )


def review_in_order(
    reads: list[ExtractedInvoice],
    repo: Repository,
    index: ContractIndex,
    runner_for: Callable[[str], AgentRunner],
    review_threshold: float,
) -> list[InvoiceReview]:
    """Review invoices in arrival order; each reviewed invoice joins the archive afterwards, so
    later invoices are compared with it (duplicate check). Reads sent to manual review are not
    archived, as in the baseline."""
    reviews = []
    for inv in sorted(reads, key=lambda x: x.invoice_id):
        review = review_invoice(inv, repo, index, runner_for(inv.invoice_id), review_threshold)
        reviews.append(review)
        if inv.confidence >= review_threshold:
            supplier = repo.supplier_by_org_number(inv.supplier_org_number)
            repo.archive_invoice(inv, supplier.supplier_id if supplier else None)
    return reviews
