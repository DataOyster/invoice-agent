"""Rule-based baseline: the five checks as fixed rules, no AI.

The agent (task 7) must beat this on recall without losing precision (SPEC section 4).
Order matters: the unit check runs before the price check, because a case price charged per kg
also looks like a price above agreement (DATASET_PLAN decision 2).

Input is an ExtractedInvoice, so the same checks can run on perfect data (the ground truth,
converted) or on what an extractor really read from the PDF.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from invoice_agent.data_gen.models import Evidence, InvoiceGroundTruth, Product, VatCategory
from invoice_agent.evals.reference import ReferenceData
from invoice_agent.schemas import ExtractedInvoice, ExtractedLine, Flag

TOLERANCE = 0.01  # 1% on price and quantity (SPEC section 6, confirmed default)


class RuleBaseline:
    def __init__(self, ref: ReferenceData):
        self.ref = ref
        self.supplier_by_org = {s.org_number: s.supplier_id for s in ref.suppliers}
        self.products = {p.product_id: p for p in ref.products}
        self.notes = {n.note_id: n for n in ref.delivery_notes}
        self.clause_by_topic = {(c.supplier_id, c.topic): c.clause_id for c in ref.clauses}
        # Archive of invoices already processed, for the duplicate check.
        self.seen_numbers: dict[tuple[str, str], str] = {}
        self.seen_date_totals: dict[tuple[str, date, float], str] = {}

    # ------------------------------------------------------------ lookups

    def agreed_price(self, product_id: str, day: date):
        for entry in self.ref.price_list:
            if entry.product_id == product_id and entry.valid_from <= day <= entry.valid_to:
                return entry
        return None

    def vat_rule(self, category: VatCategory, day: date):
        for rule in self.ref.vat_table:
            if (
                rule.category == category
                and (rule.valid_from is None or rule.valid_from <= day)
                and (rule.valid_to is None or day <= rule.valid_to)
            ):
                return rule
        return None

    def clause(self, supplier_id: str, topic: str) -> str | None:
        return self.clause_by_topic.get((supplier_id, topic))

    # ------------------------------------------------------------ the five checks

    def check(self, inv: ExtractedInvoice) -> list[Flag]:
        """Return proposed flags for one invoice. Invoices must arrive in archive order."""
        supplier_id = self.supplier_by_org.get(inv.supplier_org_number or "")
        if supplier_id is None or inv.delivery_date is None:
            return []  # cannot check without knowing who and when; extraction sends it to review
        flags = self._check_duplicate(inv, supplier_id)
        for i, line in enumerate(inv.lines):
            product = self.products.get(line.product_id or "")
            if product is None or product.supplier_id != supplier_id:
                continue  # unknown product: no reference to compare with, nothing is flagged
            flags += self._check_line(inv, supplier_id, i, line, product)
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
        if inv.document_type != "invoice" or inv.number is None:
            return []
        clause = self.clause(supplier_id, "invoicing")
        flags = []
        by_number = self.seen_numbers.get((supplier_id, inv.number))
        by_date_total = self.seen_date_totals.get(
            (supplier_id, inv.invoice_date, inv.total_incl_vat)
        )
        if by_number:
            flags.append(
                self._flag(
                    inv,
                    "duplicate",
                    None,
                    "invoice",
                    by_number,
                    clause,
                    f"number {inv.number} already received in {by_number}",
                )
            )
        elif by_date_total:
            flags.append(
                self._flag(
                    inv,
                    "duplicate",
                    None,
                    "invoice",
                    by_date_total,
                    clause,
                    f"same date and total as {by_date_total}",
                )
            )
        self.seen_numbers.setdefault((supplier_id, inv.number), inv.invoice_id)
        self.seen_date_totals.setdefault(
            (supplier_id, inv.invoice_date, inv.total_incl_vat), inv.invoice_id
        )
        return flags

    def _check_line(
        self, inv: ExtractedInvoice, supplier_id: str, i: int, line: ExtractedLine, product: Product
    ) -> list[Flag]:
        flags = []
        entry = self.agreed_price(product.product_id, inv.delivery_date)
        if entry is not None:
            agreed = entry.unit_price
            case_price = agreed * (product.pack_weight_kg or 0)
            if (
                product.pack_weight_kg
                and product.pack_weight_kg > 1
                and abs(line.unit_price - case_price) <= case_price * TOLERANCE
            ):
                flags.append(
                    self._flag(
                        inv,
                        "unit_confusion",
                        i,
                        "price_list",
                        entry.entry_id,
                        self.clause(supplier_id, "units"),
                        f"{line.unit_price:.2f} equals the case price ({product.pack_weight_kg:g} kg "
                        f"x {agreed:.2f}/kg)",
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

        rule = self.vat_rule(product.vat_category, inv.delivery_date)
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

        note = self.notes.get(inv.delivery_note_id or "")
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
    """Run the checks over invoices in archive order. Low-confidence reads are not checked:
    they go to manual review, exactly as the agent will do."""
    baseline = RuleBaseline(ref)
    flags: list[Flag] = []
    for inv in sorted((x for x in extracted if x is not None), key=lambda x: x.invoice_id):
        if inv.confidence >= review_threshold:
            flags += baseline.check(inv)
    return flags
