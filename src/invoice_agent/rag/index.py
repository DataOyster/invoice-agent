"""Contract retrieval with BM25.

In one sentence: BM25 scores each clause by how many query words it contains, giving more weight
to rare words (e.g. "kilogram") than to common ones (e.g. "the"), and less weight to very long
clauses. No embeddings, no model: fast, deterministic and easy to explain.

Chunking: one clause = one chunk (title + text). Clauses are short and self-contained, so
splitting them further would only cut sentences apart. Annex A (the price table) is not indexed:
prices come from the price_list table, which is exact; retrieval is for the contract wording.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from invoice_agent.data_gen.models import Clause

STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "by",
        "each",
        "for",
        "from",
        "has",
        "in",
        "is",
        "it",
        "may",
        "no",
        "not",
        "of",
        "on",
        "or",
        "per",
        "such",
        "that",
        "the",
        "their",
        "there",
        "this",
        "to",
        "which",
        "with",
    }
)


def tokenize(text: str) -> list[str]:
    """Lower-case words without stopwords, with a light stemmer ('deliveries' -> 'deliver')."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [_stem(w) for w in words if w not in STOPWORDS]


def _stem(word: str) -> str:
    for suffix in ("ies", "ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


@dataclass(frozen=True)
class Hit:
    clause: Clause
    score: float


class ContractIndex:
    def __init__(self, clauses: list[Clause]):
        self.clauses = sorted(clauses, key=lambda c: c.clause_id)  # fixed order = reproducible
        self._bm25 = BM25Okapi([tokenize(f"{c.title} {c.text}") for c in self.clauses])

    def search(self, query: str, supplier_id: str | None = None, k: int = 3) -> list[Hit]:
        """Top-k clauses for the query. With supplier_id, only that supplier's contract counts:
        a metadata filter applied before ranking, as a real system would do."""
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(
            (
                Hit(clause, float(score))
                for clause, score in zip(self.clauses, scores, strict=True)
                if supplier_id is None or clause.supplier_id == supplier_id
            ),
            key=lambda h: (-h.score, h.clause.clause_id),  # ties broken by id, not by luck
        )
        return ranked[:k]
