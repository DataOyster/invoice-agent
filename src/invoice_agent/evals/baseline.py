"""Rule-based baseline: the five checks as fixed rules, no AI.

The agent (task 7) must beat this on recall without losing precision (SPEC section 4).
Order matters: the unit check runs before the price check, because a case price charged per kg
also looks like a price above agreement (DATASET_PLAN decision 2).

Input is an ExtractedInvoice, so the same checks can run on perfect data (the ground truth,
converted) or on what an extractor really read from the PDF.
"""

from __future__ import annotations

from collections.abc import Iterable

from invoice_agent.data_gen.models import Evidence, InvoiceGroundTruth, Product
from invoice_agent.db.repository import Repository
from invoice_agent.db.seed import repository_from_reference
from invoice_agent.evals.reference import ReferenceData
from invoice_agent.schemas import ExtractedInvoice, ExtractedLine, Flag

TOLERANCE = 0.01  # 1% on price and quantity (SPEC section 6, confirmed default)


class RuleBaseline:
    """The five checks as fixed rules. All reference data comes from the shared Repository."""

    def __init__(self, repo: Repository):
        self.repo = repo

    def clause(self, supplier_id: str, topic: str) -> str | None:
        clause = self.repo.clause_by_topic(supplier_id, topic)
        return clause.clause_id if clause else None

    # ------------------------------------------------------------ the five checks

    def check(self, inv: ExtractedInvoice) -> list[Flag]:
        """Proposed flags for one invoice, compared with the archive as it is now."""
        supplier = self.repo.supplier_by_org_number(inv.supplier_org_number)
        if supplier is None or inv.delivery_date is None:
            return []  # cannot check without knowing who and when; extraction sends it to review
        flags = self._check_duplicate(inv, supplier.supplier_id)
        for i, line in enumerate(inv.lines):
            product = self.repo.product(line.product_id)
            if product is None or product.supplier_id != supplier.supplier_id:
                continue  # unknown product: no reference to compare with, nothing is flagged
            flags += self._check_line(inv, supplier.supplier_id, i, line, product)
        return flags

    def _flag(self, inv, type_, line_index, kind, ref, clause, message) -> Flag:
        return Flag(
            invoice_id=inv.invoice_id,
            type=type_,
            line_index=line_index,
            evidence=Evidence(kind=kind, ref=ref),
            clause_ref=clause,
            message=message,
        )

    def _check_duplicate(self, inv: ExtractedInvoice, supplier_id: str) -> list[Flag]:
        if inv.document_type != "invoice":
            return []  # a credit note legitimately refers to an earlier invoice
        earlier = self.repo.find_earlier_invoice(
            supplier_id, inv.number, inv.invoice_date, inv.total_incl_vat
        )
        if earlier is None:
            return []
        earlier_id, reason = earlier
        message = (
            f"number {inv.number} already received in {earlier_id}"
            if reason == "same_number"
            else f"same supplier, date and total as {earlier_id}"
        )
        clause = self.clause(supplier_id, "invoicing")
        return [self._flag(inv, "duplicate", None, "invoice", earlier_id, clause, message)]

    def _check_line(
        self, inv: ExtractedInvoice, supplier_id: str, i: int, line: ExtractedLine, product: Product
    ) -> list[Flag]:
        flags = []
        entry = self.repo.agreed_price(product.product_id, inv.delivery_date)
        if entry is not None:
            agreed = entry.unit_price
            pack = product.pack_weight_kg or 0
            case_price = agreed * pack
            if pack > 1 and abs(line.unit_price - case_price) <= case_price * TOLERANCE:
                flags.append(
                    self._flag(
                        inv,
                        "unit_confusion",
                        i,
                        "price_list",
                        entry.entry_id,
                        self.clause(supplier_id, "units"),
                        f"{line.unit_price:.2f} equals the case price ({pack:g} kg x {agreed:.2f}/kg)",
                    )
                )
            elif line.unit_price > agreed * (1 + TOLERANCE):
                flags.append(
                    self._flag(
                        inv,
                        "price_above_agreement",
                        i,
                        "price_list",
                        entry.entry_id,
                        self.clause(supplier_id, "price_lock"),
                        f"{line.unit_price:.2f} above agreed {agreed:.2f}",
                    )
                )

        rule = self.repo.vat_rule(product.vat_category, inv.delivery_date)
        if rule is not None and abs(line.vat_rate - rule.rate) > 1e-9:
            flags.append(
                self._flag(
                    inv,
                    "wrong_vat",
                    i,
                    "vat_rule",
                    rule.rule_id,
                    None,
                    f"VAT {line.vat_rate:.0%} but {rule.rate:.0%} is due for delivery "
                    f"{inv.delivery_date}",
                )
            )

        note = self.repo.delivery_note(inv.delivery_note_id)
        if inv.document_type == "invoice" and note is not None:
            delivered = {x.product_id: x.qty for x in note.lines}.get(product.product_id, 0.0)
            if abs(line.qty - delivered) > delivered * TOLERANCE:
                flags.append(
                    self._flag(
                        inv,
                        "quantity_mismatch",
                        i,
                        "delivery_note",
                        note.note_id,
                        self.clause(supplier_id, "delivery"),
                        f"billed {line.qty:g} but delivery note says {delivered:g}",
                    )
                )
        return flags


def resolve_products_by_name(inv: ExtractedInvoice, ref: ReferenceData) -> ExtractedInvoice:
    """Rule step after the regex extractor: map each printed description to a catalog product
    by exact name (any language, case-insensitive). Variants and typos stay unmapped."""
    supplier_id = next(
        (s.supplier_id for s in ref.suppliers if s.org_number == inv.supplier_org_number), None
    )
    by_name = {
        (p.supplier_id, name.casefold()): p.product_id
        for p in ref.products
        for name in p.names.values()
    }
    lines = [
        line.model_copy(
            update={
                "product_id": line.product_id
                or by_name.get((supplier_id, line.description.casefold()))
            }
        )
        for line in inv.lines
    ]
    return inv.model_copy(update={"lines": lines})


def ground_truth_as_extracted(gt: InvoiceGroundTruth, ref: ReferenceData) -> ExtractedInvoice:
    """Perfect extraction: the ground truth in the extractor's output format."""
    org = next(s.org_number for s in ref.suppliers if s.supplier_id == gt.supplier_id)
    return ExtractedInvoice(
        invoice_id=gt.invoice_id,
        language=gt.language,
        document_type=gt.document_type,
        supplier_org_number=org,
        number=gt.number,
        invoice_date=gt.invoice_date,
        delivery_date=gt.delivery_date,
        delivery_note_id=gt.delivery_note_id,
        credits_invoice_number=gt.credits_invoice_number,
        lines=[
            ExtractedLine(
                description=x.description,
                product_id=x.product_id,
                qty=x.qty,
                unit=x.unit,
                unit_price=x.unit_price,
                vat_rate=x.vat_rate,
                line_total=x.line_total,
            )
            for x in gt.lines
        ],
        total_excl_vat=gt.total_excl_vat,
        vat_total=gt.vat_total,
        total_incl_vat=gt.total_incl_vat,
        confidence=1.0,
    )


def run_baseline(
    ref: ReferenceData, extracted: Iterable[ExtractedInvoice | None], review_threshold: float
) -> list[Flag]:
    """Run the checks over invoices in arrival order, on a fresh in-memory database.

    Low-confidence reads are not checked and not archived: they go to manual review, exactly as
    the agent will do. Each checked invoice is archived after its checks, so later invoices are
    compared with it (duplicate check).
    """
    repo = repository_from_reference(ref)
    baseline = RuleBaseline(repo)
    flags: list[Flag] = []
    for inv in sorted((x for x in extracted if x is not None), key=lambda x: x.invoice_id):
        if inv.confidence < review_threshold:
            continue
        flags += baseline.check(inv)
        supplier = repo.supplier_by_org_number(inv.supplier_org_number)
        repo.archive_invoice(inv, supplier.supplier_id if supplier else None)
    return flags
