"""How the checks ask the contract index a question.

Cross-language rule (ROADMAP task 6): contracts are English only, invoices are sv/en/it, so a
query is always built from the anomaly type and the product's canonical English catalog name,
never from the invoice line text ("Laxfilé" would match nothing in an English contract).

The contracts deliberately avoid invoice words ("remuneration" instead of "price", "consignment"
instead of "delivery", "bill" instead of "invoice"). `expand` adds such synonyms to a query.
Caveat to state in the report: the synonym list was written by looking at these contracts, so
it is tuned to this dataset; on real contracts it would need to be grown from real wording.
"""

from __future__ import annotations

# Plain invoice-side wording of each problem, the way a bookkeeper would describe it.
ANOMALY_QUERIES = {
    "price_above_agreement": "unit price on the invoice is higher than the agreed price for {product}",
    "unit_confusion": "{product} price per kg charged as price per case unit",
    "quantity_mismatch": "invoiced quantity of {product} differs from the delivered quantity",
    "duplicate": "the same invoice was sent twice, duplicate invoice number",
}

# One plain question per contract topic, for reviewers asking about a contract (e.g. via MCP).
TOPIC_QUESTIONS = {
    "scope": "what does the agreement cover and for which year",
    "price_lock": "can the supplier raise the price during the year",
    "delivery": "on which days are goods delivered and which quantities can be invoiced",
    "units": "is meat or fish invoiced per kg or per case",
    "invoicing": "what happens if we receive the same invoice twice",
    "credit": "how do we get money back for damaged or missing goods",
    "minimum_order": "what is the smallest order we can place",
    "payment": "how many days do we have to pay an invoice",
}

SYNONYMS = {
    "price": ["remuneration", "annex"],
    "invoice": ["bill", "billing", "billed"],
    "invoiced": ["billed", "charged"],
    "delivered": ["consignment", "receipt", "receipted", "handed"],
    "goods": ["consignment"],
    "kg": ["kilogram", "net", "weight"],
    "case": ["packaging", "cases", "boxes"],
    "twice": ["repeated"],
    "raise": ["increase", "change"],
    "money": ["credit", "compensated"],
    "pay": ["payment", "due"],
    "agreed": ["fixed", "annex"],
}


def query_for_anomaly(anomaly_type: str, product_name: str | None) -> str:
    return ANOMALY_QUERIES[anomaly_type].format(product=product_name or "the item")


def expand(query: str) -> str:
    """Append contract-side synonyms for the words in the query."""
    words = query.lower().split()
    extra = [syn for word in words for syn in SYNONYMS.get(word.strip(",.?"), [])]
    return " ".join([query, *extra])
