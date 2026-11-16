"""Citations: a flag may quote a contract only with text that really is in that clause.

This is the guardrail against invented clauses (SPEC section 5): the quote is checked against the
stored clause text, not against what the model says the clause contains.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from invoice_agent.db.repository import Repository
from invoice_agent.rag.index import Hit


@dataclass(frozen=True)
class Citation:
    clause_id: str
    title: str
    quote: str


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def cite(hit: Hit, max_chars: int = 300) -> Citation:
    """A citation that quotes the clause verbatim (first sentence(s), up to max_chars)."""
    text = hit.clause.text
    if len(text) > max_chars:
        cut = text.rfind(". ", 0, max_chars)
        text = text[: cut + 1] if cut > 0 else text[:max_chars]
    return Citation(clause_id=hit.clause.clause_id, title=hit.clause.title, quote=text)


def verify_citation(citation: Citation, repo: Repository, min_chars: int = 20) -> bool:
    """True only if the clause exists and the quote appears in it word for word
    (whitespace and case ignored). Too-short quotes prove nothing and are rejected."""
    clause = repo.clause(citation.clause_id)
    quote = _normalise(citation.quote)
    return clause is not None and len(quote) >= min_chars and quote in _normalise(clause.text)
