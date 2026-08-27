"""
- FakeEmbeddingProvider is deterministic: the same text always produces the
  same vector, from a fresh instance, with no shared state required
- Different text produces a different vector (not a degenerate constant)
- Vector length matches the provider's declared `dimensions`
- Batch embedding preserves input order (each vector corresponds to its
  same-index input text)
Every test in this file - and every other test file - uses
FakeEmbeddingProvider. None makes a network call.
"""
from aico.retrieval.embedding_provider import FakeEmbeddingProvider


def test_same_text_gives_the_same_vector():
    provider = FakeEmbeddingProvider()
    v1 = provider.embed(["net thirty days from invoice"])[0]
    v2 = provider.embed(["net thirty days from invoice"])[0]
    assert v1 == v2


def test_same_text_gives_the_same_vector_across_separate_instances():
    v1 = FakeEmbeddingProvider().embed(["public liability insurance"])[0]
    v2 = FakeEmbeddingProvider().embed(["public liability insurance"])[0]
    assert v1 == v2


def test_different_text_gives_different_vectors():
    v1, v2 = FakeEmbeddingProvider().embed(["alpha beta gamma", "delta epsilon zeta"])
    assert v1 != v2


def test_vector_length_matches_declared_dimensions():
    provider = FakeEmbeddingProvider(dimensions=16)
    vector = provider.embed(["some text"])[0]
    assert len(vector) == 16 == provider.dimensions


def test_embed_of_empty_list_returns_empty_list():
    assert FakeEmbeddingProvider().embed([]) == []


def test_embed_preserves_input_order():
    provider = FakeEmbeddingProvider()
    texts = ["first text", "second text", "third text"]
    vectors = provider.embed(texts)
    for text, vector in zip(texts, vectors):
        assert provider.embed([text])[0] == vector


def test_model_alias_is_stable_and_configurable():
    provider = FakeEmbeddingProvider(model_alias="fake-v2")
    assert provider.model_alias == "fake-v2"
