"""Retrieval evaluation: is the right contract clause among the top 3 results (hit@3)?

Two query sets, both with a known correct clause:
- anomaly queries: one per injected anomaly that has a clause label in the ground truth (all
  types except wrong_vat, whose evidence is the VAT table, not the contract);
- topic questions: one plain question per supplier and contract topic (6 x 8 = 48).
Each set is measured with and without the supplier filter and the synonym expansion, so the
effect of each design choice is a number, not a claim.
"""

from __future__ import annotations

from dataclasses import dataclass

from invoice_agent.evals.metrics import hit_at_k
from invoice_agent.evals.reference import ReferenceData
from invoice_agent.rag.index import ContractIndex
from invoice_agent.rag.queries import TOPIC_QUESTIONS, expand, query_for_anomaly


@dataclass(frozen=True)
class RetrievalQuery:
    query_id: str
    text: str
    supplier_id: str
    expected_clause: str


def anomaly_queries(ref: ReferenceData) -> list[RetrievalQuery]:
    names = {p.product_id: p.name for p in ref.products}  # canonical English names
    out = []
    for gt in ref.ground_truth:
        for a in gt.anomalies:
            if a.clause_ref is None:
                continue
            product = names[gt.lines[a.line_index].product_id] if a.line_index is not None else None
            out.append(
                RetrievalQuery(
                    f"{gt.invoice_id}:{a.type}",
                    query_for_anomaly(a.type, product),
                    gt.supplier_id,
                    a.clause_ref,
                )
            )
    return out


def topic_queries(ref: ReferenceData) -> list[RetrievalQuery]:
    by_topic = {(c.supplier_id, c.topic): c.clause_id for c in ref.clauses}
    suppliers = sorted({c.supplier_id for c in ref.clauses})
    return [
        RetrievalQuery(f"{sid}:{topic}", question, sid, by_topic[(sid, topic)])
        for sid in suppliers
        for topic, question in TOPIC_QUESTIONS.items()
    ]


@dataclass(frozen=True)
class RetrievalScore:
    query_set: str
    supplier_filter: bool
    synonyms: bool
    queries: int
    hit_at_1: float | None
    hit_at_3: float | None
    right_topic_at_3: float | None  # right kind of clause, possibly another supplier's
    misses: tuple[str, ...]


def evaluate(
    index: ContractIndex,
    query_set: str,
    queries: list[RetrievalQuery],
    supplier_filter: bool,
    synonyms: bool,
) -> RetrievalScore:
    topic = {c.clause_id: c.topic for c in index.clauses}
    results = {}
    for q in queries:
        text = expand(q.text) if synonyms else q.text
        hits = index.search(text, q.supplier_id if supplier_filter else None, k=3)
        results[q.query_id] = [h.clause.clause_id for h in hits]
    expected = {q.query_id: q.expected_clause for q in queries}
    misses = tuple(qid for qid, clause in expected.items() if clause not in results[qid][:3])
    right_topic = sum(
        any(topic[c] == topic[expected[qid]] for c in found[:3]) for qid, found in results.items()
    )
    return RetrievalScore(
        query_set,
        supplier_filter,
        synonyms,
        len(queries),
        hit_at_k(results, expected, k=1),
        hit_at_k(results, expected, k=3),
        right_topic / len(queries) if queries else None,
        misses,
    )


def evaluate_all(ref: ReferenceData) -> list[RetrievalScore]:
    index = ContractIndex(ref.clauses)
    sets = {"anomaly queries": anomaly_queries(ref), "topic questions": topic_queries(ref)}
    return [
        evaluate(index, name, queries, supplier_filter, synonyms)
        for name, queries in sets.items()
        for supplier_filter in (False, True)
        for synonyms in (False, True)
    ]
