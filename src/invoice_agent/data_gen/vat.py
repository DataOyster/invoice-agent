"""Dated Swedish VAT table. The DELIVERY date decides the rate, not the invoice date.

Rates live here as data, never in prompts. Sources are in docs/SPEC.md (Verksamt, Riksdagen);
re-check with Skatteverket before freezing the dataset.

The SPEC does not say which food rate applies after 2027-12-31, so there is deliberately no rule
for it: a lookup for such a date raises LookupError instead of guessing.
"""

from __future__ import annotations

from datetime import date

from invoice_agent.data_gen.models import VatCategory, VatRule

_SOURCE = "docs/SPEC.md section 2 (Verksamt, Riksdagen); re-check with Skatteverket"

VAT_TABLE: tuple[VatRule, ...] = (
    VatRule(
        rule_id="VAT-FOOD-12",
        category="food",
        valid_from=None,
        valid_to=date(2026, 3, 31),
        rate=0.12,
        source=_SOURCE,
    ),
    VatRule(
        rule_id="VAT-FOOD-06",
        category="food",
        valid_from=date(2026, 4, 1),
        valid_to=date(2027, 12, 31),
        rate=0.06,
        source=_SOURCE,
    ),
    VatRule(
        rule_id="VAT-ALCOHOL-25",
        category="alcohol",
        valid_from=None,
        valid_to=None,
        rate=0.25,
        source=_SOURCE,
    ),
    VatRule(
        rule_id="VAT-NONFOOD-25",
        category="non_food",
        valid_from=None,
        valid_to=None,
        rate=0.25,
        source=_SOURCE,
    ),
)

# Date the reduced food rate starts. Used to build the VAT trap cases.
FOOD_RATE_CHANGE = date(2026, 4, 1)


def vat_rule_for(category: VatCategory, delivery_date: date) -> VatRule:
    """Return the single rule that applies to this category on this delivery date."""
    matches = [
        rule
        for rule in VAT_TABLE
        if rule.category == category
        and (rule.valid_from is None or rule.valid_from <= delivery_date)
        and (rule.valid_to is None or delivery_date <= rule.valid_to)
    ]
    if len(matches) != 1:
        raise LookupError(f"expected one VAT rule for {category} on {delivery_date}, got {matches}")
    return matches[0]


def vat_rate_for(category: VatCategory, delivery_date: date) -> float:
    return vat_rule_for(category, delivery_date).rate
