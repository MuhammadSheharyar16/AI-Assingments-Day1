"""
- Mode isolation: bm25, vector and hybrid each return results independently,
  and the record shape (chunk_id, source_file, char span, score) is
  identical across all three modes
- Determinism: two identical runs of each mode - and of the full search
  pipeline end to end - produce identical rankings and identical scores
- A dimension mismatch surfaces as an error from vector/hybrid mode, not a
  silently wrong ranking
All tests inject FakeEmbeddingProvider directly into vector_search /
hybrid_search - never through the CLI, never a network call.
"""
import hashlib

import pytest

from aico.retrieval.bm25 import ChunkScore
from aico.retrieval.embed import embed_chunks
from aico.retrieval.embedding_provider import FakeEmbeddingProvider
from aico.retrieval.search import bm25_search, hybrid_search, vector_search
from aico.retrieval.vector_index import VectorCache

REQUIRED_FIELDS = {"chunk_id", "source_file", "char_start", "char_end"}


def _chunk(chunk_id, source_file, text):
    return {
        "chunk_id": chunk_id,
        "source_file": source_file,
        "char_start": 0,
        "char_end": len(text),
        "text": text,
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def _chunks():
    return [
        _chunk("c1", "doc-a.md", "public liability insurance cover requirement"),
        _chunk("c2", "doc-b.md", "payment terms net thirty days invoice"),
        _chunk("c3", "doc-c.md", "unrelated chunk about warehouse logistics"),
    ]


def _vectors_dir(tmp_path, chunks, provider):
    cache = VectorCache()
    embed_chunks(chunks, provider, cache)
    cache.save(tmp_path)
    return tmp_path


def test_all_three_modes_return_the_same_record_shape(tmp_path):
    chunks = _chunks()
    provider = FakeEmbeddingProvider()
    vectors_dir = _vectors_dir(tmp_path, chunks, provider)

    bm25_results = bm25_search(chunks, "public liability insurance", top_k=2)
    vector_results = vector_search(chunks, "public liability insurance", top_k=2, vectors_dir=vectors_dir, provider=provider)
    hybrid_results = hybrid_search(chunks, "public liability insurance", top_k=2, vectors_dir=vectors_dir, provider=provider)

    for results in (bm25_results, vector_results, hybrid_results):
        assert results
        for r in results:
            assert isinstance(r, ChunkScore)
            assert REQUIRED_FIELDS.issubset(r.chunk.keys())
            assert isinstance(r.score, float)


def test_modes_do_not_interfere_with_each_other(tmp_path):
    chunks = _chunks()
    provider = FakeEmbeddingProvider()
    vectors_dir = _vectors_dir(tmp_path, chunks, provider)

    vector_before = vector_search(chunks, "payment terms", top_k=2, vectors_dir=vectors_dir, provider=provider)
    bm25_search(chunks, "payment terms", top_k=2)
    hybrid_search(chunks, "payment terms", top_k=2, vectors_dir=vectors_dir, provider=provider)
    vector_after = vector_search(chunks, "payment terms", top_k=2, vectors_dir=vectors_dir, provider=provider)

    assert [(r.chunk["chunk_id"], r.score) for r in vector_before] == \
           [(r.chunk["chunk_id"], r.score) for r in vector_after]


@pytest.mark.parametrize("mode", ["bm25", "vector", "hybrid"])
def test_each_mode_is_deterministic_across_runs(tmp_path, mode):
    chunks = _chunks()
    provider = FakeEmbeddingProvider()
    vectors_dir = _vectors_dir(tmp_path, chunks, provider)

    def run():
        if mode == "bm25":
            results = bm25_search(chunks, "payment terms", top_k=3)
        elif mode == "vector":
            results = vector_search(chunks, "payment terms", top_k=3, vectors_dir=vectors_dir, provider=provider)
        else:
            results = hybrid_search(chunks, "payment terms", top_k=3, vectors_dir=vectors_dir, provider=provider)
        return [(r.chunk["chunk_id"], r.score) for r in results]

    assert run() == run()


def test_vector_mode_returns_the_top_cosine_match(tmp_path):
    # FakeEmbeddingProvider is content-hash-derived, not a real semantic
    # model, so this only proves the *pipeline* (embed query -> cosine
    # search -> ranked results) works end to end, not real semantic recall -
    # querying with a chunk's own exact text is the one case guaranteed to
    # cosine-match that chunk regardless of what the fake vectors look like.
    chunks = _chunks()
    provider = FakeEmbeddingProvider()
    vectors_dir = _vectors_dir(tmp_path, chunks, provider)

    results = vector_search(chunks, "public liability insurance cover requirement", top_k=1,
                             vectors_dir=vectors_dir, provider=provider)
    assert results[0].chunk["chunk_id"] == "c1"


def test_vector_mode_raises_on_query_dimension_mismatch(tmp_path):
    chunks = _chunks()
    cache_provider = FakeEmbeddingProvider(dimensions=32)
    vectors_dir = _vectors_dir(tmp_path, chunks, cache_provider)

    mismatched_provider = FakeEmbeddingProvider(dimensions=8)
    with pytest.raises(ValueError):
        vector_search(chunks, "payment terms", top_k=2, vectors_dir=vectors_dir, provider=mismatched_provider)


def test_vector_search_raises_if_no_cache_exists(tmp_path):
    chunks = _chunks()
    with pytest.raises(FileNotFoundError):
        vector_search(chunks, "payment terms", top_k=2, vectors_dir=tmp_path, provider=FakeEmbeddingProvider())
