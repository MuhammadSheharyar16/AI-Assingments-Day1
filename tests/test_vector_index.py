"""
- Cosine correctness: known vectors produce known similarity, including the
  identical (1.0), orthogonal (0.0) and opposite (-1.0) cases
- Dimension mismatch: mismatched vector lengths raise - directly in
  cosine_similarity and via VectorCache.search - never padded or truncated
- VectorCache round-trips through save()/load() with every required field
  (chunk_id, content_hash, model_alias, dimensions, dataset_version,
  created_at) intact
- get_valid() is a cache hit only when BOTH content_hash and model_alias
  match - anything else (missing entry, edited content, different model)
  is a miss
"""
import math

import pytest

from aico.retrieval.vector_index import VectorCache, VectorEntry, cosine_similarity


def _entry(chunk_id, vector, content_hash="h", model_alias="m", dataset_version="v1", created_at="t"):
    return VectorEntry(
        chunk_id=chunk_id, content_hash=content_hash, model_alias=model_alias,
        dimensions=len(vector), dataset_version=dataset_version, created_at=created_at, vector=vector,
    )


def test_cosine_of_identical_vectors_is_one():
    v = [1.0, 2.0, 3.0]
    assert cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_of_orthogonal_vectors_is_zero():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_of_opposite_vectors_is_minus_one():
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_of_a_hand_worked_example():
    # cos = (1*1 + 0*1) / (|[1,0]| * |[1,1]|) = 1 / (1 * sqrt(2))
    assert cosine_similarity([1.0, 0.0], [1.0, 1.0]) == pytest.approx(1 / math.sqrt(2))


def test_cosine_of_a_zero_vector_is_zero_not_a_divide_by_zero_error():
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_dimension_mismatch_raises_not_padded_or_truncated():
    with pytest.raises(ValueError):
        cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])


def test_cache_search_raises_on_dimension_mismatch_against_a_stored_vector():
    cache = VectorCache()
    cache.put(_entry("c1", [1.0, 0.0, 0.0]))
    with pytest.raises(ValueError):
        cache.search([1.0, 0.0])  # query vector is the wrong length


def test_get_valid_is_a_hit_only_when_hash_and_alias_both_match():
    cache = VectorCache()
    cache.put(_entry("c1", [1.0, 0.0], content_hash="hash-a", model_alias="model-a"))

    assert cache.get_valid("c1", "hash-a", "model-a") is not None
    assert cache.get_valid("c1", "hash-b", "model-a") is None  # content changed
    assert cache.get_valid("c1", "hash-a", "model-b") is None  # model changed
    assert cache.get_valid("missing", "hash-a", "model-a") is None  # no entry at all


def test_save_and_load_round_trips_every_required_field(tmp_path):
    cache = VectorCache()
    cache.put(_entry(
        "c1", [0.1, 0.2, 0.3],
        content_hash="hash-a", model_alias="model-a", dataset_version="v1", created_at="2026-01-01T00:00:00Z",
    ))
    cache.save(tmp_path)

    reloaded = VectorCache.load(tmp_path)
    entry = reloaded.get_valid("c1", "hash-a", "model-a")

    assert entry is not None
    assert entry.vector == [0.1, 0.2, 0.3]
    assert entry.dimensions == 3
    assert entry.dataset_version == "v1"
    assert entry.created_at == "2026-01-01T00:00:00Z"


def test_load_of_a_missing_cache_file_returns_an_empty_cache(tmp_path):
    cache = VectorCache.load(tmp_path / "does-not-exist")
    assert len(cache) == 0


def test_search_ranks_by_cosine_descending_and_breaks_ties_by_chunk_id():
    cache = VectorCache()
    cache.put(_entry("b", [1.0, 0.0]))   # cos with [1,0] = 1.0
    cache.put(_entry("a", [1.0, 0.0]))   # tied with b -> chunk_id breaks the tie
    cache.put(_entry("c", [0.0, 1.0]))   # cos with [1,0] = 0.0

    results = cache.search([1.0, 0.0], top_k=3)
    ordered_ids = [entry.chunk_id for entry, _ in results]

    assert ordered_ids == ["a", "b", "c"]
