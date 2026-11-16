# Dataset plan (task 2)

Everything is invented. No real company, person, IBAN or organisation number. Generation is deterministic: one `--seed`, same output every run.

## Contents
- **6 suppliers**, one per category: fish, produce, dairy, dry goods, meat, drinks. Invented names (check none matches a real company) and obviously fake organisation numbers.
- **Price list per supplier**: ~15 products each, with product, unit, weight per pack where relevant, agreed price (SEK), valid-from and valid-to dates. Include one second price list from a competitor supplier for a few shared products (feeds optional task 13).
- **Contract per supplier** as text: clauses on price lock, delivery days, credit notes, minimum order. This is the RAG material. Write some clauses with different wording from the invoice vocabulary, so retrieval is not trivial.
- **Delivery notes**: what actually arrived, per delivery date.
- **~100 invoice PDFs** in Swedish, English and Italian, 3 layouts, some with scan-like noise (rotation, blur). Use `reportlab`.
- **Ground truth** for every invoice (JSON): extracted fields as they should be read, and the list of injected anomalies.

## Injected anomalies (~25% of invoices, default until confirmed)
1. Price above agreement (sometimes slightly above, sometimes clearly).
2. Duplicate invoice (same number resent, and same date + total with a new number).
3. Wrong VAT (food at the wrong rate for the delivery date; alcohol at 6%).
4. Quantity mismatch against the delivery note.
5. Confused unit (price per kg charged as price per case, or reverse).

**Trap cases that must NOT be flagged:**
- Delivered before 1 April 2026, invoiced after it, charged at 12% (correct).
- A price within tolerance (rounding).
- A legitimate credit note.

## VAT table (dated, delivery date decides)
| Category | Valid for deliveries | Rate |
|---|---|---|
| Food | up to 2026-03-31 | 12% |
| Food | 2026-04-01 to 2027-12-31 | 6% |
| Spirits, wine, strong beer | any | 25% |
| Non-food supplies | any | 25% |

Store this as data with validity dates. Re-check with Skatteverket before freezing.

## Ground-truth schema (example)
```json
{
  "invoice_id": "INV-0042",
  "file": "invoices/INV-0042.pdf",
  "language": "sv",
  "supplier_id": "S03",
  "number": "2026-1187",
  "invoice_date": "2026-04-09",
  "delivery_date": "2026-03-30",
  "lines": [
    {"product_id": "P031", "description": "Branzino 1-2 kg", "qty": 60.0, "unit": "kg", "unit_price": 140.00, "vat_rate": 0.12}
  ],
  "total_excl_vat": 8400.00,
  "total_incl_vat": 9408.00,
  "anomalies": [],
  "is_trap": true,
  "trap_reason": "delivered before 2026-04-01, invoiced after; 12% is correct"
}
```
An anomaly entry has: `type` (one of `price_above_agreement`, `duplicate`, `wrong_vat`, `quantity_mismatch`, `unit_confusion`), `line_index` (or null), `evidence` (price-list line id or clause id) and `expected` / `found` values.

## Tests for this task
- Same seed gives byte-identical ground truth.
- Ground truth validates against its Pydantic schema.
- Every anomaly type appears; the VAT trap case appears; error share is within the configured range.
- No generated name matches a denylist of real company names kept in the test file (a short list is enough).
