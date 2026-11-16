"""Deterministic generator for the synthetic invoice dataset.

How determinism is guaranteed:
- one `random.Random(seed)` object is created in `generate()` and passed explicitly; nothing uses
  the global `random` module, the clock or the file system order;
- anomalies are assigned by exact quotas (e.g. 5 per type), not by probability, so every type
  appears for any seed;
- money is computed with `Decimal` and rounded to öre before it becomes a float.

The generator first builds mutable "drafts", then numbers and sorts them, and only at the end
turns them into validated Pydantic records.
"""

from __future__ import annotations

import random
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from invoice_agent.data_gen import catalog
from invoice_agent.data_gen.models import (
    Anomaly,
    Clause,
    DeliveryNote,
    DeliveryNoteLine,
    Evidence,
    InvoiceGroundTruth,
    InvoiceLine,
    PriceListEntry,
    Product,
    Supplier,
    VatRule,
)
from invoice_agent.data_gen.vat import FOOD_RATE_CHANGE, VAT_TABLE, vat_rule_for

PRICE_CHANGE_DATE = date(2026, 7, 1)
YEAR_START = date(2026, 1, 1)
YEAR_END = date(2026, 12, 31)
TOLERANCE = 0.01  # 1% on price and quantity (SPEC section 6)


@dataclass(frozen=True)
class GeneratorConfig:
    seed: int = 42
    n_documents: int = 100
    per_anomaly_type: int = 5
    n_vat_traps: int = 4
    n_price_traps: int = 4
    n_credit_notes: int = 2
    n_unreadable: int = 4
    noise_share: float = 0.10
    first_delivery: date = date(2026, 1, 7)
    last_delivery: date = date(2026, 11, 6)
    # Hard split: documents the rules were not written for (see DATASET_PLAN, "Hard split").
    n_hard_documents: int = 30
    hard_per_anomaly_type: int = 2

    @property
    def standard_quota(self) -> SplitQuota:
        return SplitQuota(
            self.n_documents,
            self.per_anomaly_type,
            self.n_vat_traps,
            self.n_price_traps,
            self.n_credit_notes,
            self.n_unreadable,
            self.noise_share,
        )

    @property
    def hard_quota(self) -> SplitQuota:
        return SplitQuota(
            self.n_hard_documents,
            self.hard_per_anomaly_type,
            n_vat_traps=2,
            n_price_traps=1,
            n_credit_notes=1,
            n_unreadable=0,
            noise_share=0.0,
        )


@dataclass(frozen=True)
class SplitQuota:
    n_documents: int
    per_anomaly_type: int
    n_vat_traps: int
    n_price_traps: int
    n_credit_notes: int
    n_unreadable: int
    noise_share: float

    @property
    def n_regular(self) -> int:
        """Invoices with their own delivery (not duplicates, credit notes or unreadable PDFs)."""
        return self.n_documents - self.per_anomaly_type - self.n_credit_notes - self.n_unreadable


@dataclass
class Dataset:
    config: GeneratorConfig
    suppliers: list[Supplier]
    products: list[Product]
    price_list: list[PriceListEntry]
    competitor_prices: list[PriceListEntry]
    vat_table: list[VatRule]
    contracts: dict[str, list[Clause]]
    delivery_notes: list[DeliveryNote]
    invoices: list[InvoiceGroundTruth]


# ---------------------------------------------------------------- money and quantities


def money(value: float | Decimal) -> float:
    """Round to öre with commercial rounding (half up), return a float for JSON."""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def qty_step(unit: str) -> float:
    return 0.5 if unit in ("kg", "l") else 1.0


def round_qty(value: float, unit: str) -> float:
    step = qty_step(unit)
    return max(step, round(value / step) * step)


# ---------------------------------------------------------------- price lists


def build_price_list(rng: random.Random) -> list[PriceListEntry]:
    entries = []
    for product in catalog.PRODUCTS:
        base = catalog.BASE_PRICES[product.product_id]
        if product.supplier_id in catalog.PRICE_CHANGE_SUPPLIERS:
            raised = money(base * rng.uniform(1.03, 1.08))
            periods = [
                (1, base, YEAR_START, PRICE_CHANGE_DATE - timedelta(days=1)),
                (2, raised, PRICE_CHANGE_DATE, YEAR_END),
            ]
        else:
            periods = [(1, base, YEAR_START, YEAR_END)]
        for version, price, start, end in periods:
            entries.append(
                PriceListEntry(
                    entry_id=f"PL-{product.product_id}-{version}",
                    supplier_id=product.supplier_id,
                    product_id=product.product_id,
                    unit=product.unit,
                    unit_price=money(price),
                    valid_from=start,
                    valid_to=end,
                )
            )
    return entries


def build_competitor_prices(rng: random.Random) -> list[PriceListEntry]:
    by_id = {p.product_id: p for p in catalog.PRODUCTS}
    return [
        PriceListEntry(
            entry_id=f"PL-S07-{pid}-1",
            supplier_id="S07",
            product_id=pid,
            unit=by_id[pid].unit,
            unit_price=money(catalog.BASE_PRICES[pid] * rng.uniform(0.88, 1.05)),
            valid_from=YEAR_START,
            valid_to=YEAR_END,
        )
        for pid in catalog.COMPETITOR_PRODUCTS
    ]


def find_price(price_list: list[PriceListEntry], product_id: str, day: date) -> PriceListEntry:
    for entry in price_list:
        if entry.product_id == product_id and entry.valid_from <= day <= entry.valid_to:
            return entry
    raise LookupError(f"no price for {product_id} on {day}")


# ---------------------------------------------------------------- contracts


def build_contracts(rng: random.Random) -> dict[str, list[Clause]]:
    contracts: dict[str, list[Clause]] = {}
    for supplier in catalog.SUPPLIERS:
        if supplier.role != "supplier":
            continue
        values = {
            "supplier": supplier.name,
            "buyer": catalog.BUYER["name"],
            "category": supplier.category,
            "delivery_days": catalog.DELIVERY_DAYS[supplier.supplier_id],
            "minimum_order": catalog.MINIMUM_ORDER[supplier.supplier_id],
        }
        clauses = []
        for number, (topic, title, wordings) in enumerate(catalog.CLAUSE_TOPICS, start=1):
            clauses.append(
                Clause(
                    clause_id=f"{supplier.supplier_id}-C{number}",
                    supplier_id=supplier.supplier_id,
                    topic=topic,
                    title=title,
                    text=rng.choice(wordings).format(**values),
                )
            )
        contracts[supplier.supplier_id] = clauses
    return contracts


def clause_id_for(
    contracts: dict[str, list[Clause]], supplier_id: str, topic: str | None
) -> str | None:
    if topic is None:
        return None
    return next(c.clause_id for c in contracts[supplier_id] if c.topic == topic)


# ---------------------------------------------------------------- drafts


@dataclass
class _Line:
    product: Product
    qty: float
    unit_price: float
    vat_rate: float
    description: str | None = None  # printed text if it differs from the catalog name


@dataclass
class _Draft:
    supplier: Supplier
    delivery_date: date
    invoice_date: date
    lines: list[_Line]
    delivered: dict[str, float]  # product_id -> quantity on the delivery note
    order: int  # creation order, used only as a stable tie-breaker
    document_type: str = "invoice"
    number: str = ""
    anomaly: dict | None = None
    trap_reason: str | None = None
    duplicate_of: _Draft | None = None
    credits: _Draft | None = None
    has_own_delivery_note: bool = True
    render_mode: str = "normal"  # normal | blurred_image_only | truncated_file
    scan_noise: bool = False
    invoice_id: str = ""
    note_id: str | None = None
    split: str = "standard"
    difficulty: list[str] = field(default_factory=list)
    layout: str | None = None  # None = the supplier's usual layout

    def total_excl_vat(self) -> float:
        return money(sum(Decimal(str(line_total(line))) for line in self.lines))


def line_total(line: _Line) -> float:
    return money(Decimal(str(line.qty)) * Decimal(str(line.unit_price)))


def has_food(draft: _Draft) -> bool:
    return any(line.product.vat_category == "food" for line in draft.lines)


class _Builder:
    """Holds the shared state (rng, price list, products) while drafts are created."""

    def __init__(
        self, rng: random.Random, price_list: list[PriceListEntry], first: date, last: date
    ):
        self.rng = rng
        self.price_list = price_list
        self.first, self.last = first, last  # default delivery window
        self.products_by_supplier: dict[str, list[Product]] = {}
        for product in catalog.PRODUCTS:
            self.products_by_supplier.setdefault(product.supplier_id, []).append(product)
        self.suppliers = [s for s in catalog.SUPPLIERS if s.role == "supplier"]
        self.counter = 0

    # --- dates

    def delivery_date(self, supplier: Supplier, first: date, last: date) -> date:
        weekdays = catalog.DELIVERY_WEEKDAYS[supplier.supplier_id]
        days = [
            first + timedelta(days=i)
            for i in range((last - first).days + 1)
            if (first + timedelta(days=i)).weekday() in weekdays
        ]
        return self.rng.choice(days)

    # --- drafts

    def draft(
        self, supplier: Supplier, delivery: date, invoice: date | None = None, require=None
    ) -> _Draft:
        """A clean invoice: agreed prices, correct VAT, quantities equal to the delivery note."""
        candidates = self.products_by_supplier[supplier.supplier_id]
        chosen = self.rng.sample(candidates, self.rng.randint(3, 6))
        if require is not None and not any(require(p) for p in chosen):
            eligible = [p for p in candidates if require(p) and p not in chosen]
            chosen[0] = self.rng.choice(eligible)
        lines = []
        for product in chosen:
            if product.unit in ("kg", "l"):
                qty = self.rng.randint(4, 60) / 2
            else:
                qty = float(self.rng.randint(2, 24))
            price = find_price(self.price_list, product.product_id, delivery).unit_price
            rate = vat_rule_for(product.vat_category, delivery).rate
            lines.append(_Line(product, qty, price, rate))
        if invoice is None:
            invoice = delivery + timedelta(days=self.rng.randint(0, 10))
        self.counter += 1
        return _Draft(
            supplier=supplier,
            delivery_date=delivery,
            invoice_date=invoice,
            lines=lines,
            delivered={line.product.product_id: line.qty for line in lines},
            order=self.counter,
        )

    def any_supplier_draft(
        self,
        require_supplier=None,
        require_product=None,
        first: date | None = None,
        last: date | None = None,
        invoice_window: tuple[date, date] | None = None,
    ) -> _Draft:
        suppliers = [s for s in self.suppliers if require_supplier is None or require_supplier(s)]
        supplier = self.rng.choice(suppliers)
        delivery = self.delivery_date(supplier, first or self.first, last or self.last)
        invoice = None
        if invoice_window is not None:
            lo, hi = invoice_window
            invoice = lo + timedelta(days=self.rng.randint(0, (hi - lo).days))
        return self.draft(supplier, delivery, invoice, require_product)


LATE_MARCH = (date(2026, 3, 16), date(2026, 3, 31))
EARLY_APRIL = (date(2026, 4, 1), date(2026, 4, 10))


def _is_food_supplier(supplier: Supplier) -> bool:
    return supplier.supplier_id != "S06"


def _is_food(product: Product) -> bool:
    return product.vat_category == "food"


def _unit_confusable(product: Product) -> bool:
    return product.unit == "kg" and (product.pack_weight_kg or 0) > 1


# ---------------------------------------------------------------- anomaly injection


def inject_price(b: _Builder, d: _Draft, k: int) -> None:
    idx = b.rng.randrange(len(d.lines))
    line = d.lines[idx]
    entry = find_price(b.price_list, line.product.product_id, d.delivery_date)
    slight = k % 2 == 0
    factor = b.rng.uniform(1.03, 1.06) if slight else b.rng.uniform(1.15, 1.40)
    line.unit_price = money(entry.unit_price * factor)
    d.anomaly = {
        "type": "price_above_agreement",
        "line_index": idx,
        "evidence": Evidence(kind="price_list", ref=entry.entry_id),
        "expected": entry.unit_price,
        "found": line.unit_price,
        "detail": f"{'slightly' if slight else 'clearly'} above the agreed price "
        f"(+{(factor - 1) * 100:.1f}%)",
    }


def inject_unit(b: _Builder, d: _Draft) -> None:
    idx = next(i for i, line in enumerate(d.lines) if _unit_confusable(line.product))
    line = d.lines[idx]
    entry = find_price(b.price_list, line.product.product_id, d.delivery_date)
    pack = line.product.pack_weight_kg
    line.unit_price = money(entry.unit_price * pack)
    d.anomaly = {
        "type": "unit_confusion",
        "line_index": idx,
        "evidence": Evidence(kind="price_list", ref=entry.entry_id),
        "expected": entry.unit_price,
        "found": line.unit_price,
        "detail": f"case price ({pack:g} kg per case) charged as price per kg",
    }


def inject_quantity(b: _Builder, d: _Draft, k: int) -> None:
    idx = b.rng.randrange(len(d.lines))
    line = d.lines[idx]
    delivered = d.delivered[line.product.product_id]
    step = qty_step(line.product.unit)
    if k == 4 and delivered > 2 * step:  # one case with fewer units billed than delivered
        billed = round_qty(delivered * b.rng.uniform(0.6, 0.85), line.product.unit)
    else:
        billed = round_qty(delivered * b.rng.uniform(1.08, 1.30), line.product.unit)
    if abs(billed - delivered) <= delivered * TOLERANCE:
        billed = delivered + 2 * step
    line.qty = billed
    d.anomaly = {
        "type": "quantity_mismatch",
        "line_index": idx,
        "evidence": Evidence(kind="delivery_note", ref="<filled later>"),
        "expected": delivered,
        "found": billed,
        "detail": "billed quantity differs from the delivery note",
    }


def inject_vat(b: _Builder, k: int) -> _Draft:
    """Three realistic VAT mistakes, cycled so each appears."""
    variant = ("invoice_date_rule", "old_food_rate", "alcohol_reduced")[k % 3]
    if variant == "invoice_date_rule":
        d = b.any_supplier_draft(
            _is_food_supplier, _is_food, *LATE_MARCH, invoice_window=EARLY_APRIL
        )
        wrong, rule = 0.06, vat_rule_for("food", d.delivery_date)
        detail = "6% applied because invoiced after 2026-04-01, but delivered before: 12% is due"
    elif variant == "old_food_rate":
        d = b.any_supplier_draft(_is_food_supplier, _is_food, first=date(2026, 4, 13))
        wrong, rule = 0.12, vat_rule_for("food", d.delivery_date)
        detail = "old 12% food rate charged on a delivery after 2026-04-01: 6% is due"
    else:
        d = b.any_supplier_draft(lambda s: s.supplier_id == "S06")
        idx = b.rng.randrange(len(d.lines))
        d.lines[idx].vat_rate = 0.06
        rule = vat_rule_for("alcohol", d.delivery_date)
        d.anomaly = {
            "type": "wrong_vat",
            "line_index": idx,
            "evidence": Evidence(kind="vat_rule", ref=rule.rule_id),
            "expected": rule.rate,
            "found": 0.06,
            "detail": "alcohol charged at the reduced food rate: 25% is due",
        }
        return d
    food_lines = [i for i, line in enumerate(d.lines) if _is_food(line.product)]
    for i in food_lines:
        d.lines[i].vat_rate = wrong
    d.anomaly = {
        "type": "wrong_vat",
        "line_index": food_lines[0] if len(food_lines) == 1 else None,
        "evidence": Evidence(kind="vat_rule", ref=rule.rule_id),
        "expected": rule.rate,
        "found": wrong,
        "detail": detail,
    }
    return d


def make_price_trap(b: _Builder, d: _Draft) -> None:
    idx = max(range(len(d.lines)), key=lambda i: d.lines[i].unit_price)
    line = d.lines[idx]
    agreed = line.unit_price
    sign = b.rng.choice((-1, 1))
    new = money(agreed * (1 + sign * b.rng.uniform(0.002, 0.005)))
    if new == agreed:
        new = money(agreed + sign * 0.01)
    line.unit_price = new
    d.trap_reason = (
        f"line {idx}: unit price {new:.2f} vs agreed {agreed:.2f} is within the 1% tolerance"
    )


# ---------------------------------------------------------------- the pipeline


def generate(config: GeneratorConfig | None = None) -> Dataset:
    config = config or GeneratorConfig()
    rng = random.Random(config.seed)
    price_list = build_price_list(rng)
    competitor_prices = build_competitor_prices(rng)
    contracts = build_contracts(rng)
    b = _Builder(rng, price_list, config.first_delivery, config.last_delivery)
    standard = _build_split(b, config.standard_quota, counters={}, credit_offset=0)

    # The hard split has its own random stream, so adding it never changes the standard split.
    hard_rng = random.Random(f"hard-split-{config.seed}")
    b_hard = _Builder(hard_rng, price_list, config.first_delivery, config.last_delivery)
    b_hard.counter = b.counter
    counters = _next_sequence(standard)  # hard invoices continue each supplier's numbering
    hard = _build_split(b_hard, config.hard_quota, counters, credit_offset=config.n_credit_notes)
    for d in hard:
        d.split = "hard"

    delivery_notes: list[DeliveryNote] = []
    _assign_ids(standard, lambda i: f"INV-{i:04d}", delivery_notes)
    _assign_ids(hard, lambda i: f"INV-H{i:03d}", delivery_notes)
    _assign_difficulties(hard_rng, hard)

    invoices = [_to_ground_truth(d, contracts) for d in standard + hard]
    return Dataset(
        config=config,
        suppliers=list(catalog.SUPPLIERS),
        products=list(catalog.PRODUCTS),
        price_list=price_list,
        competitor_prices=competitor_prices,
        vat_table=list(VAT_TABLE),
        contracts=contracts,
        delivery_notes=delivery_notes,
        invoices=invoices,
    )


def _build_split(
    b: _Builder, quota: SplitQuota, counters: dict[str, int], credit_offset: int
) -> list[_Draft]:
    """All documents of one split: anomalies, traps, clean invoices, duplicates, credit notes."""
    n = quota.per_anomaly_type
    n_clean = quota.n_regular - 4 * n - quota.n_vat_traps - quota.n_price_traps
    needed_originals = n + quota.n_credit_notes
    if n_clean < needed_originals:
        raise ValueError("config leaves too few clean invoices for duplicates and credit notes")

    drafts: list[_Draft] = []
    # 1. Anomalies that live inside one invoice.
    for k in range(n):
        d = b.any_supplier_draft()
        inject_price(b, d, k)
        drafts.append(d)
    for _ in range(n):
        d = b.any_supplier_draft(
            require_product=_unit_confusable, require_supplier=lambda s: s.supplier_id != "S06"
        )
        inject_unit(b, d)
        drafts.append(d)
    for k in range(n):
        d = b.any_supplier_draft()
        inject_quantity(b, d, k)
        drafts.append(d)
    for k in range(n):
        drafts.append(inject_vat(b, k))
    # 2. Trap cases that must NOT be flagged.
    for _ in range(quota.n_vat_traps):
        d = b.any_supplier_draft(
            _is_food_supplier, _is_food, *LATE_MARCH, invoice_window=EARLY_APRIL
        )
        d.trap_reason = "delivered before 2026-04-01 and invoiced after it; 12% on food is correct"
        drafts.append(d)
    for _ in range(quota.n_price_traps):
        d = b.any_supplier_draft()
        make_price_trap(b, d)
        drafts.append(d)
    # 3. Clean invoices, some of them later used as originals for duplicates / credit notes.
    clean = [b.any_supplier_draft() for _ in range(n_clean)]
    drafts.extend(clean)
    # 4. Unreadable PDFs (clean content, but image-only or corrupted).
    for k in range(quota.n_unreadable):
        d = b.any_supplier_draft()
        d.render_mode = "blurred_image_only" if k % 2 == 0 else "truncated_file"
        drafts.append(d)

    _avoid_accidental_duplicates(drafts)
    _mark_natural_vat_traps(clean)
    _assign_numbers(drafts, counters)

    originals = b.rng.sample([d for d in clean if d.trap_reason is None], needed_originals)
    credit_originals = originals[: quota.n_credit_notes]
    duplicate_originals = originals[quota.n_credit_notes :]
    for k, original in enumerate(credit_originals):
        drafts.append(_credit_note(b, original, k + credit_offset))
    next_seq = _next_sequence(drafts)
    for k, original in enumerate(duplicate_originals):
        drafts.append(_duplicate(b, original, k, next_seq))

    readable = [d for d in drafts if d.render_mode == "normal" and d.duplicate_of is None]
    for d in b.rng.sample(readable, round(quota.noise_share * len(readable))):
        d.scan_noise = True

    return drafts


def _assign_ids(drafts: list[_Draft], make_id, delivery_notes: list[DeliveryNote]) -> None:
    """Sort into archive order, give invoice ids, create the delivery notes."""
    drafts.sort(
        key=lambda d: (
            d.invoice_date,
            d.duplicate_of is not None,
            d.supplier.supplier_id,
            d.number,
            d.order,
        )
    )
    for i, d in enumerate(drafts, start=1):
        d.invoice_id = make_id(i)
        if d.has_own_delivery_note:
            d.note_id = f"DN-{len(delivery_notes) + 1:04d}"
            delivery_notes.append(_delivery_note(d))
    for d in drafts:
        if d.duplicate_of is not None:
            d.note_id = d.duplicate_of.note_id


# One entry per hard document (cycled if the split is larger). Two documents combine two
# difficulties; the others have one each, so the effect of each difficulty can be read apart.
HARD_DIFFICULTY_PLAN: tuple[tuple[str, ...], ...] = (
    *[("unseen_layout",)] * 9,
    *[("description_variants",)] * 9,
    *[("heavy_noise",)] * 8,
    *[("image_only_readable",)] * 2,
    *[("unseen_layout", "description_variants")] * 2,
)

PACK_WORD = {"sv": "kartong", "en": "case", "it": "cartone"}
EXACT_NAMES = {name.casefold() for p in catalog.PRODUCTS for name in p.names.values()}


def _assign_difficulties(rng: random.Random, drafts: list[_Draft]) -> None:
    plan = [HARD_DIFFICULTY_PLAN[i % len(HARD_DIFFICULTY_PLAN)] for i in range(len(drafts))]
    rng.shuffle(plan)
    for d, difficulty in zip(drafts, plan, strict=True):
        d.difficulty = list(difficulty)
        if "unseen_layout" in difficulty:
            d.layout = "D"
        if "description_variants" in difficulty:
            for line in d.lines:
                line.description = description_variant(rng, line.product, d.supplier.language)


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def description_variant(rng: random.Random, product: Product, language: str) -> str:
    """How a supplier's own system might print the product: never the exact catalog name."""
    name = product.names[language]
    for _ in range(10):
        kind = rng.choice(("pack", "abbreviation", "typo"))
        if kind == "pack":
            pack = PACK_WORD[language]
            text = (
                f"{name} {product.pack_weight_kg:g}kg-{pack}"
                if product.pack_weight_kg
                else f"{name} ({pack})"
            )
        elif kind == "abbreviation":
            text = " ".join(w[:4] + "." if len(w) > 5 else w for w in name.split())
        else:
            longest = max(name.split(), key=len)
            i = len(longest) // 2
            swapped = longest[: i - 1] + longest[i] + longest[i - 1] + longest[i + 1 :]
            text = name.replace(longest, swapped, 1)
        if rng.random() < 0.3:
            text = _strip_accents(text).upper()
        if text.casefold() not in EXACT_NAMES:
            return text
    return f"{name} [{product.product_id[-2:]}]"


def _mark_natural_vat_traps(clean: list[_Draft]) -> None:
    """A clean invoice that happens to cross the VAT change is a trap case too; label it."""
    for d in clean:
        if d.delivery_date < FOOD_RATE_CHANGE <= d.invoice_date and has_food(d):
            d.trap_reason = (
                "delivered before 2026-04-01 and invoiced after it; 12% on food is correct"
            )


def _avoid_accidental_duplicates(drafts: list[_Draft]) -> None:
    """Two different invoices from one supplier on the same day: move the later one a day on.

    Otherwise the 'same supplier, same date, same total' rule could match by chance and the
    ground truth would be wrong. Moving the invoice date never breaks the VAT logic, which
    depends on the delivery date only.
    """
    seen: set[tuple[str, date]] = set()
    for d in sorted(drafts, key=lambda x: x.order):
        while (d.supplier.supplier_id, d.invoice_date) in seen:
            d.invoice_date += timedelta(days=1)
        seen.add((d.supplier.supplier_id, d.invoice_date))
        if d.trap_reason and "2026-04-01" in d.trap_reason:
            assert d.delivery_date < FOOD_RATE_CHANGE <= d.invoice_date


def _sequence_start(supplier: Supplier) -> int:
    return 100 + 37 * int(supplier.supplier_id[1:])


def _assign_numbers(drafts: list[_Draft], counters: dict[str, int]) -> None:
    for d in sorted(drafts, key=lambda x: (x.invoice_date, x.order)):
        sid = d.supplier.supplier_id
        counters[sid] = counters.get(sid, _sequence_start(d.supplier)) + 1
        d.number = f"{d.supplier.invoice_prefix}-2026-{counters[sid]:04d}"


def _next_sequence(drafts: list[_Draft]) -> dict[str, int]:
    highest: dict[str, int] = {}
    for d in drafts:
        if d.document_type == "invoice":
            seq = int(d.number.rsplit("-", 1)[1])
            highest[d.supplier.supplier_id] = max(highest.get(d.supplier.supplier_id, 0), seq)
    return highest


def _credit_note(b: _Builder, original: _Draft, k: int) -> _Draft:
    source = b.rng.choice(original.lines)
    credited = round_qty(source.qty * b.rng.uniform(0.2, 0.5), source.product.unit)
    b.counter += 1
    return _Draft(
        supplier=original.supplier,
        delivery_date=original.delivery_date,
        invoice_date=original.invoice_date + timedelta(days=b.rng.randint(3, 10)),
        lines=[_Line(source.product, -credited, source.unit_price, source.vat_rate)],
        delivered={},
        order=b.counter,
        document_type="credit_note",
        number=f"{original.supplier.invoice_prefix}-CN-2026-{k + 1:03d}",
        trap_reason=f"legitimate credit note for {original.number} (goods returned)",
        credits=original,
        has_own_delivery_note=False,
    )


def _duplicate(b: _Builder, original: _Draft, k: int, next_seq: dict[str, int]) -> _Draft:
    same_number = k % 2 == 0
    sid = original.supplier.supplier_id
    if same_number:
        number = original.number
        invoice_date = original.invoice_date + timedelta(days=b.rng.randint(7, 20))
        detail = "same supplier and same number as an earlier invoice (resent)"
    else:
        next_seq[sid] += 1
        number = f"{original.supplier.invoice_prefix}-2026-{next_seq[sid]:04d}"
        invoice_date = original.invoice_date
        detail = "same supplier, invoice date and total as an earlier invoice, new number"
    b.counter += 1
    return _Draft(
        supplier=original.supplier,
        delivery_date=original.delivery_date,
        invoice_date=invoice_date,
        lines=[
            _Line(x.product, x.qty, x.unit_price, x.vat_rate, x.description) for x in original.lines
        ],
        delivered=dict(original.delivered),
        order=b.counter,
        number=number,
        anomaly={
            "type": "duplicate",
            "line_index": None,
            "evidence": None,
            "expected": original.number,
            "found": number,
            "detail": detail,
        },
        duplicate_of=original,
        has_own_delivery_note=False,
    )


def _delivery_note(d: _Draft) -> DeliveryNote:
    return DeliveryNote(
        note_id=d.note_id,
        supplier_id=d.supplier.supplier_id,
        delivery_date=d.delivery_date,
        lines=[
            DeliveryNoteLine(
                product_id=line.product.product_id,
                qty=d.delivered[line.product.product_id],
                unit=line.product.unit,
            )
            for line in d.lines
        ],
    )


def _to_ground_truth(d: _Draft, contracts: dict[str, list[Clause]]) -> InvoiceGroundTruth:
    lines = [
        InvoiceLine(
            product_id=line.product.product_id,
            description=line.description or line.product.names[d.supplier.language],
            qty=line.qty,
            unit=line.product.unit,
            unit_price=line.unit_price,
            vat_rate=line.vat_rate,
            line_total=line_total(line),
        )
        for line in d.lines
    ]
    excl = d.total_excl_vat()
    vat = money(sum(Decimal(str(x.line_total)) * Decimal(str(x.vat_rate)) for x in lines))
    anomalies = []
    if d.anomaly is not None:
        fields = dict(d.anomaly)
        if fields["type"] == "duplicate":
            fields["evidence"] = Evidence(kind="invoice", ref=d.duplicate_of.invoice_id)
        elif fields["type"] == "quantity_mismatch":
            fields["evidence"] = Evidence(kind="delivery_note", ref=d.note_id)
        topic = catalog.CLAUSE_FOR_ANOMALY[fields["type"]]
        fields["clause_ref"] = clause_id_for(contracts, d.supplier.supplier_id, topic)
        anomalies.append(Anomaly(**fields))
    return InvoiceGroundTruth(
        invoice_id=d.invoice_id,
        file=f"invoices/{d.invoice_id}.pdf",
        split=d.split,
        difficulty=d.difficulty,
        language=d.supplier.language,
        layout=d.layout or d.supplier.layout,
        document_type=d.document_type,
        supplier_id=d.supplier.supplier_id,
        number=d.number,
        invoice_date=d.invoice_date,
        delivery_date=d.delivery_date,
        delivery_note_id=d.note_id,
        credits_invoice_number=d.credits.number if d.credits else None,
        lines=lines,
        total_excl_vat=excl,
        vat_total=vat,
        total_incl_vat=money(Decimal(str(excl)) + Decimal(str(vat))),
        anomalies=anomalies,
        is_trap=d.trap_reason is not None,
        trap_reason=d.trap_reason,
        scan_noise=d.scan_noise,
        readable=d.render_mode == "normal",
        unreadable_reason=None if d.render_mode == "normal" else d.render_mode,
    )
