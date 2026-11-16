# Roadmap (12 tasks + 1 optional), 16-19 November 2026

Interview with Mpya Digital: **Friday 20 November 2026**. Work days: Mon 16, Tue 17, Wed 18, Thu 19 (Thursday = docs, pitch rehearsal and safety margin).

Eval and guardrails come first, not last. Mark a task done only when its tests pass.
Cut order if time runs out: task 11, then task 13. **Never cut tasks 8 and 10.**

## Monday 16 Nov: foundations and first parts (tasks 2-5)
- [x] **1. Spec and risks.** `docs/SPEC.md`. Metrics and error costs fixed before code. (Done 12 Nov.)
- [x] **2. Synthetic dataset.** Suppliers, price lists, contracts, delivery notes, ~100 invoice PDFs in 3 languages, injected anomalies, ground truth. Plan in `docs/DATASET_PLAN.md`. Done when: generator is deterministic (seeded), ground truth validates against a schema, tests cover every anomaly type and the VAT trap case. **Time box:** if it runs past 3 hours, cut scan noise and the extra layouts first (one clean layout is enough); keep everything else.
- [x] **3. Evaluation skeleton.** Metrics (extraction accuracy, per-type precision/recall, retrieval hit@3, guardrail pass rate), rule-based baseline with no AI, report generator. Done when: baseline numbers print reproducibly before the agent exists.
- [x] **4. Data layer.** SQLite schema and functions: invoices, lines, price lists, contracts, delivery notes, flags. Done when: unit tests pass and functions are reusable by agent and MCP.
- [ ] **5. Extraction.** Invoice PDF to validated JSON (Pydantic) with confidence. Done when: field accuracy is measured with the eval harness; low confidence routes to manual review. **Time box and fallback below.** *Status 16 Nov:* the rule extractor (the fallback) was built in task 3 and scores 100% on the standard split; the Claude extractor, its cache and offline tests are built; the hard split was added to measure them. Open: one live run on the hard split (`python -m invoice_agent.evals.llm_runs --split hard`), then tick.

### Task 5 time box (the riskiest task)
PDF extraction is often slower than expected. Rule: **if extraction is not working well after 2 hours, stop perfecting it.** Switch to the simple version: read PDF text with `pdfplumber` and extract the key fields with fixed rules, no AI. All later tasks stay unblocked because they work on structured data (the ground truth can stand in during development). The better Claude-based extraction is done afterwards only if time remains, and the limitation is stated plainly in the eval report.

## Tuesday 17 Nov: the core (tasks 6-8)
- [ ] **6. RAG on contracts.** Chunking, BM25 retrieval, citation of the clause. Optional later: vectors and hybrid ranking, only if hit@3 can be compared with numbers. Done when: hit@3 is measured. **Cross-language rule:** contracts are English only while invoices are sv/en/it, so retrieval must query with the canonical product name or product ID from the catalog, never with the invoice-language line description (BM25 has no cross-language matching).
- [ ] **7. Reconciliation agent.** Tool-use loop applying the five checks, producing flags with evidence. Done when: precision/recall per type are measured and compared with the baseline.
- [ ] **8. Guardrails.** Prompt-injection defence, confidence thresholds, human approval gate, step/cost/time limits, audit log, log masking, no payment or edit tools. Done when: every rule in SPEC section 5 has a passing test.

## Wednesday 18 Nov: MCP, adversarial tests, UI (tasks 9-11)
- [ ] **9. MCP server.** Read-only tools over the shared data layer; flag creation behind approval. Done when: Claude Desktop can answer "which suppliers raised prices this month?".
- [ ] **10. Adversarial tests and CI.** Hidden instructions, corrupted PDFs, conflicting clauses, VAT date trap; GitHub Actions running the offline suite. Done when: suite is green and failure cases are documented.
- [ ] **11. Review UI (Streamlit).** Queue where a human approves or rejects flags with the evidence next to them. First thing to cut.

## Thursday 19 Nov: docs, pitch, margin (task 12)
- [ ] **12. Docs and pitch.** README, architecture diagram, eval report with honest limits (must include: at most one injected anomaly per invoice, while real invoices can carry two or more at once; contracts in English only), 5-minute pitch and likely questions (English and Swedish). Rehearse out loud. Keep time free for whatever slipped.

## Optional
- [ ] **13. Supplier comparison.** Take the usual weekly basket, simulate moving purchases to another supplier using prices already in the data, report net annual saving per product. Same unit and quality only; mention delivery, minimum order and reliability. Only if everything above is done. No external price platform (prices are confidential and scraping may breach terms).

## Live API check
Run at least one real-API check **today** (`pytest -m live`, needs `ANTHROPIC_API_KEY` in `.env`) so problems with the key, model name or limits show up on Monday, not on Thursday.
