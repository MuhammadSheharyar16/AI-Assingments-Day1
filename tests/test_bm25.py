"""
- Ranking: a known unambiguous query returns the expected chunk first
- Determinism: two identical runs -> identical scores
- Tie-break rule is applied consistently
"""
from aico.retrieval.bm25 import BM25Index, tokenize


def test_tokenize_lowercases_and_drops_punctuation():
    assert tokenize("Net 30 Days!") == ["net", "30", "days"]


def test_ranking_returns_the_obviously_relevant_chunk_first():
    chunks = [
        {"chunk_id": "c1", "text": "The quick brown fox jumps over the lazy dog"},
        {"chunk_id": "c2", "text": "Every supplier must hold public liability insurance cover"},
        {"chunk_id": "c3", "text": "Payment terms require invoices within thirty days"},
    ]
    index = BM25Index(chunks)
    results = index.search("public liability insurance cover", top_k=5)

    assert results[0].chunk["chunk_id"] == "c2"


def test_query_with_no_matching_terms_scores_zero():
    chunks = [{"chunk_id": "c1", "text": "supplier onboarding and screening checks"}]
    index = BM25Index(chunks)
    results = index.search("xylophone quokka", top_k=5)

    assert results[0].score == 0.0


def test_same_query_gives_same_scores_on_repeat():
    chunks = [
        {"chunk_id": "c1", "text": "alpha beta gamma delta"},
        {"chunk_id": "c2", "text": "alpha alpha beta epsilon"},
    ]
    index = BM25Index(chunks)

    run1 = [(r.chunk["chunk_id"], r.score) for r in index.search("alpha beta")]
    run2 = [(r.chunk["chunk_id"], r.score) for r in index.search("alpha beta")]

    assert run1 == run2


def test_equal_scores_break_ties_by_chunk_id():
    chunks = [
        {"chunk_id": "zzz", "text": "alpha beta gamma"},
        {"chunk_id": "aaa", "text": "alpha beta gamma"},
    ]
    index = BM25Index(chunks)
    results = index.search("alpha beta gamma")

    assert results[0].score == results[1].score
    assert results[0].chunk["chunk_id"] == "aaa"
    assert results[1].chunk["chunk_id"] == "zzz"


def test_empty_index_does_not_crash():
    index = BM25Index([])
    assert index.search("anything") == []
