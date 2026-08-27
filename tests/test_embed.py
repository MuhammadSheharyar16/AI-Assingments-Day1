"""
- Cache hit: a second embed pass over unchanged chunks makes zero provider
  calls
- Cache invalidation: editing one chunk's text (and recomputing its
  content_hash, as ingest.py would) re-embeds that chunk, and only that
  chunk - every other cached vector is untouched
- Model alias change: a different model_alias invalidates every cache entry
  even though content_hash is unchanged - a vector from one model is never
  valid for another
- Dimension mismatch between what the provider declares and what it
  actually returns is a hard error, never silently accepted
"""
import hashlib

import pytest

from aico.retrieval.embed import embed_chunks
from aico.retrieval.embedding_provider import FakeEmbeddingProvider
from aico.retrieval.vector_index import VectorCache


def _chunk(chunk_id, text):
    return {"chunk_id": chunk_id, "text": text, "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def test_second_run_over_unchanged_chunks_makes_zero_provider_calls():
    chunks = [_chunk("c1", "alpha beta"), _chunk("c2", "gamma delta")]
    provider = FakeEmbeddingProvider()
    cache = VectorCache()

    embedded1, cached1, calls1 = embed_chunks(chunks, provider, cache)
    embedded2, cached2, calls2 = embed_chunks(chunks, provider, cache)

    assert (embedded1, cached1) == (2, 0)
    assert calls1 > 0
    assert (embedded2, cached2, calls2) == (0, 2, 0)


def test_editing_one_chunk_reembeds_only_that_chunk():
    chunks = [_chunk("c1", "alpha beta"), _chunk("c2", "gamma delta")]
    provider = FakeEmbeddingProvider()
    cache = VectorCache()
    embed_chunks(chunks, provider, cache)

    untouched_vector = cache.get_valid("c2", chunks[1]["content_hash"], provider.model_alias).vector

    edited = [_chunk("c1", "alpha beta EDITED"), chunks[1]]
    embedded, cached, calls = embed_chunks(edited, provider, cache)

    assert (embedded, cached, calls) == (1, 1, 1)
    # the untouched chunk's cached vector is bit-for-bit unchanged
    assert cache.get_valid("c2", chunks[1]["content_hash"], provider.model_alias).vector == untouched_vector
    # the edited chunk's OLD entry is a miss now (content_hash no longer matches)
    assert cache.get_valid("c1", chunks[0]["content_hash"], provider.model_alias) is None
    # its NEW entry, keyed on the new content_hash, is a hit
    assert cache.get_valid("c1", edited[0]["content_hash"], provider.model_alias) is not None


def test_editing_a_chunk_and_reverting_it_is_still_a_cache_miss_not_id_based():
    # proves invalidation is genuinely keyed on content_hash, not chunk_id:
    # even though c1's chunk_id never changes, embed_chunks must re-embed it
    # on ANY intervening content_hash change, regardless of what the text
    # ends up being afterwards.
    chunks = [_chunk("c1", "alpha beta")]
    provider = FakeEmbeddingProvider()
    cache = VectorCache()
    embed_chunks(chunks, provider, cache)

    edited = [_chunk("c1", "alpha beta EDITED")]
    embedded, cached, calls = embed_chunks(edited, provider, cache)
    assert (embedded, cached) == (1, 0)

    reverted = [_chunk("c1", "alpha beta")]  # back to the original text/hash
    embedded, cached, calls = embed_chunks(reverted, provider, cache)
    # the cache never held an entry for THIS content_hash after the edit, so
    # this is correctly a fresh miss, not a hit smuggled in via chunk_id
    assert (embedded, cached) == (1, 0)


def test_model_alias_change_invalidates_every_entry_even_with_unchanged_content_hash():
    chunks = [_chunk("c1", "alpha beta")]
    cache = VectorCache()
    embed_chunks(chunks, FakeEmbeddingProvider(model_alias="model-a"), cache)

    embedded, cached, calls = embed_chunks(chunks, FakeEmbeddingProvider(model_alias="model-b"), cache)

    assert (embedded, cached, calls) == (1, 0, 1)
    assert cache.get_valid("c1", chunks[0]["content_hash"], "model-a") is None
    assert cache.get_valid("c1", chunks[0]["content_hash"], "model-b") is not None


def test_provider_returning_the_wrong_dimensionality_is_a_hard_error():
    class _BrokenProvider(FakeEmbeddingProvider):
        def embed(self, texts):
            # declares 32 dimensions but actually returns 4 - must not be
            # silently accepted into the cache
            return [[0.0, 0.0, 0.0, 0.0] for _ in texts]

    chunks = [_chunk("c1", "alpha beta")]
    with pytest.raises(ValueError):
        embed_chunks(chunks, _BrokenProvider(), VectorCache())


def test_embedding_zero_chunks_makes_zero_calls():
    embedded, cached, calls = embed_chunks([], FakeEmbeddingProvider(), VectorCache())
    assert (embedded, cached, calls) == (0, 0, 0)
