"""Contract retrieval (BM25), query building and citation checks."""

from __future__ import annotations

import pytest

from invoice_agent.db.seed import repository_from_reference
from invoice_agent.evals.reference import load_reference
from invoice_agent.evals.retrieval import anomaly_queries, evaluate_all
from invoice_agent.rag.citations import Citation, cite, verify_citation
from invoice_agent.rag.index import ContractIndex, tokenize
from invoice_agent.rag.queries import expand, query_for_anomaly


@pytest.fixture(scope="module")
def ref(generated_dir):
    return load_reference(generated_dir)


@pytest.fixture(scope="module")
def index(ref):
    return ContractIndex(ref.clauses)


@pytest.fixture(scope="module")
def repo(ref):
    return repository_from_reference(ref)


def test_tokenize_lowercases_drops_stopwords_and_stems():
    assert tokenize("The Deliveries of the Consignments") == ["deliver", "consignment"]


def test_supplier_filter_keeps_only_that_contract(index):
    hits = index.search("price", supplier_id="S03", k=8)
    assert hits and all(h.clause.supplier_id == "S03" for h in hits)


def test_search_is_deterministic(index):
    first = [h.clause.clause_id for h in index.search("bill", k=10)]
    assert first == [h.clause.clause_id for h in ContractIndex(index.clauses).search("bill", k=10)]


def test_price_question_finds_the_price_lock_clause(index):
    query = expand(query_for_anomaly("price_above_agreement", "Salmon fillet"))
    assert index.search(query, supplier_id="S01", k=1)[0].clause.topic == "price_lock"


def test_queries_use_canonical_english_names_not_invoice_text(ref):
    """Cross-language rule: an Italian or Swedish line description never reaches the index."""
    invoice_texts = {
        line.description for gt in ref.ground_truth if gt.language != "en" for line in gt.lines
    }
    english = {p.name for p in ref.products}
    for q in anomaly_queries(ref):
        assert not any(text in q.text for text in invoice_texts - english), q.text


def test_expand_adds_contract_wording():
    assert "remuneration" in expand("the price is wrong")
    assert expand("nothing to add here") == "nothing to add here"


def test_retrieval_meets_threshold_with_filter_and_needs_the_filter(ref):
    scores = {(s.query_set, s.supplier_filter, s.synonyms): s for s in evaluate_all(ref)}
    for query_set in ("anomaly queries", "topic questions"):
        assert scores[(query_set, True, True)].hit_at_3 >= 0.90
        assert (
            scores[(query_set, False, False)].hit_at_3 < scores[(query_set, True, False)].hit_at_3
        )


# ---------------------------------------------------------------- citations


def test_a_verbatim_citation_is_accepted(index, repo):
    hit = index.search("payment days", supplier_id="S02", k=1)[0]
    assert verify_citation(cite(hit), repo)


def test_an_altered_quote_is_rejected(index, repo):
    citation = cite(index.search("payment days", supplier_id="S02", k=1)[0])
    altered = Citation(citation.clause_id, citation.title, citation.quote.replace("30", "90"))
    assert not verify_citation(altered, repo)


def test_an_invented_clause_is_rejected(repo):
    invented = Citation("S02-C99", "Penalties", "The supplier pays a penalty of 10% per day.")
    assert not verify_citation(invented, repo)


def test_a_real_quote_under_the_wrong_clause_id_is_rejected(repo):
    payment = repo.clause_by_topic("S02", "payment")
    wrong_id = repo.clause_by_topic("S02", "scope").clause_id
    assert not verify_citation(Citation(wrong_id, "Scope", payment.text), repo)


def test_a_too_short_quote_proves_nothing(repo):
    clause = repo.clause_by_topic("S02", "payment")
    assert not verify_citation(Citation(clause.clause_id, clause.title, "30 days"), repo)


def test_long_clauses_are_quoted_up_to_a_sentence_boundary(index):
    hit = index.search("price", supplier_id="S01", k=1)[0]
    citation = cite(hit, max_chars=80)
    assert len(citation.quote) <= 80 and hit.clause.text.startswith(citation.quote)
