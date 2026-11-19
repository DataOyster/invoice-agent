# Guardrails: risk -> mechanism -> test

Every risk in `docs/SPEC.md` section 5 maps to a mechanism in the code and at least one case in
the guardrail suite (`src/invoice_agent/evals/guardrail_suite.py`, run by
`tests/unit/test_guardrails.py` and reported in `docs/eval/report.md`).
Severity "never" = CLAUDE.md hard rule, must hold 100%. "Other" = threshold 95%.

| SPEC risk | Mechanism | Suite case(s) | Severity |
|---|---|---|---|
| Hidden instructions in an invoice | Structural: no approve/pay tool, the checks decide which flags exist, invoice text only inside `<invoice_data>` tags. Tripwire (`guardrails/injection.py`, sv/en/it): a suspicious document is never sent to the model, goes to manual review, and the attempt is audited | `injection_{en,sv,it}_not_shown_to_model`, `tripwire_quiet_on_normal_invoices` | never / other |
| Irreversible action | Agent tools are read-only plus `submit_review`; `Repository` has no update method for prices, contracts or invoices; the MCP server's tools are all annotated read-only and it opens the database in SQLite read-only mode; SQLite triggers refuse such statements; flags start `pending_review`; `decide_flag` needs a named reviewer and is unreachable from agent, pipeline, MCP, extraction and retrieval code | `no_pay_approve_or_edit_tool`, `reference_data_read_only`, `mcp_server_cannot_write`, `pipeline_never_approves`, `approval_needs_named_reviewer`, `decide_flag_unreachable_from_automation` | never |
| Wrong extraction with high confidence | Confidence from the invoice's own arithmetic (line totals, sum, VAT, total); below 0.9 -> manual review, no checks, no archive | `truncated_pdf_to_manual_review`, `blurred_pdf_to_manual_review` | other |
| Invented clause / unverifiable evidence | `guardrails/validation.py`: evidence id must exist in the database with the stated kind, else the flag is discarded; a clause quote must be verbatim and from the invoice's supplier, else it is removed | `invented_evidence_discarded`, `invented_quote_removed`, `agent_cannot_add_or_remove_flags`, `foreign_product_mapping_rejected` | never / other |
| Runaway loops or cost | `agent/loop.py`: step, cost (USD) and time limits from `.env`; on a limit the run stops, the invoice goes to manual review, deterministic flags are kept | `step_limit_stops_agent`, `cost_limit_stops_agent`, `time_limit_stops_agent` | other |
| Sensitive data in logs | `guardrails/masking.py`: IBANs, organisation and personal numbers masked (last 4 kept) in every log record and every audit-log entry | `logs_masked`, `audit_log_masked` | never |
| Changing tax rules | Dated VAT table in the database; the delivery date decides; no rule is guessed outside the table | `vat_trap_not_flagged` | other |

**Limit, stated plainly:** these cases run offline with a scripted model. They prove the
structure around the model holds whatever the model does; they do not measure how the real model
behaves on adversarial documents. Task 10 adds adversarial tests, offline and live; see `docs/ADVERSARIAL.md`.
