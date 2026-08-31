"""
Task 6 — Day 2 regression.

"Existing Day 2 evaluation metrics remain unchanged" is proven two ways in
this repository:

1. Every existing Day 1/Day 2 deterministic test still passes unchanged -
   test_chunker.py, test_bm25.py, test_ingest.py, test_day01_eval.py,
   test_embedding_provider.py, test_vector_index.py, test_embed.py,
   test_hybrid.py and test_search.py were not touched by the Day 3
   migration, and all of them use FakeEmbeddingProvider directly - none of
   the chunking, BM25, vector-cache or fusion logic changed at all.

2. This file proves the migration itself is behavior-preserving: routing
   the *same* embedding vectors through AzureEmbeddingProvider ->
   ModelGateway -> a fake transport that returns exactly what
   FakeEmbeddingProvider would (deterministic, content-hash-derived)
   produces bit-identical vectors, and bit-identical vector/hybrid search
   rankings, to calling FakeEmbeddingProvider directly, unwrapped. A real
   Foundry response, however it arrives, would flow through the gateway
   exactly as unchanged - nothing in the gateway/adapter path reorders,
   truncates, or renormalizes what a provider returns.

Reproducing Day 2's actual live Hit@1/Hit@5/MRR numbers against the real
Foundry endpoint needs real cloud credentials and is out of scope for a
deterministic unit test - that evidence belongs in
artifacts/day03/gateway_demo.md (Task 7), run once against the real
endpoint and captured there.
"""
from __future__ import annotations

import hashlib

from aico.platform.config import (
    BudgetsConfig,
    ChatBudget,
    EmbeddingBudget,
    FallbackPolicy,
    GatewayConfig,
    ModelAliases,
    ResilienceConfig,
    RetryConfig,
    RouteEndpoint,
    RoutingPolicy,
)
from aico.platform.model_gateway import ModelGateway, TransportResult
from aico.retrieval.embed import embed_chunks
from aico.retrieval.embedding_provider import AzureEmbeddingProvider, FakeEmbeddingProvider
from aico.retrieval.hybrid import reciprocal_rank_fusion
from aico.retrieval.search import bm25_search, hybrid_search, vector_search
from aico.retrieval.vector_index import VectorCache

TEST_MODEL_ALIAS = "test-embed-alias"


def _chunk(chunk_id: str, source_file: str, text: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "source_file": source_file,
        "char_start": 0,
        "char_end": len(text),
        "text": text,
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def _chunks() -> list[dict]:
    return [
        _chunk("c1", "doc-a.md", "public liability insurance cover requirement"),
        _chunk("c2", "doc-b.md", "payment terms net thirty days invoice"),
        _chunk("c3", "doc-c.md", "supplier evaluation price weighting criteria"),
        _chunk("c4", "doc-d.md", "unrelated chunk about warehouse logistics"),
    ]


def _make_config() -> GatewayConfig:
    return GatewayConfig(
        version="1.0",
        endpoint_env="AICO_TEST_FOUNDRY_ENDPOINT",
        models=ModelAliases(chat="test-chat-alias", embedding=TEST_MODEL_ALIAS),
        resilience=ResilienceConfig(
            timeout_seconds=5,
            retry=RetryConfig(max_attempts=1, base_delay_ms=10, max_delay_ms=100, jitter=False),
        ),
        budgets=BudgetsConfig(
            chat=ChatBudget(max_input_tokens=100, max_output_tokens=50),
            embedding=EmbeddingBudget(max_items_per_call=100),
        ),
        routing=RoutingPolicy(
            primary=RouteEndpoint(
                provider="microsoft-foundry", region="uk-south", data_boundary="uk", risk_class="standard"
            ),
            fallback=FallbackPolicy(
                enabled=False, route=None,
                require_compatibility={
                    "provider": True, "region": True, "data_boundary": True, "risk": True, "budget": True,
                },
            ),
        ),
    )


class _FakeProviderBackedTransport:
    """A Transport whose embed() delegates straight to a FakeEmbeddingProvider
    - so routing through ModelGateway produces exactly the vectors that
    provider would, proving the gateway is a transparent pass-through for
    whatever a real provider's response decodes to."""

    def __init__(self, fake_provider: FakeEmbeddingProvider):
        self._fake_provider = fake_provider

    def embed(self, *, model_alias, texts, timeout_seconds):
        vectors = self._fake_provider.embed(texts)
        return TransportResult(content=vectors, dimensions=self._fake_provider.dimensions, token_usage=None)

    def chat(self, *, model_alias, messages, max_output_tokens, timeout_seconds):
        raise NotImplementedError("not exercised by this regression test")


def _direct_provider(dimensions: int = 32) -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider(dimensions=dimensions, model_alias=TEST_MODEL_ALIAS)


def _gateway_backed_provider(dimensions: int = 32) -> AzureEmbeddingProvider:
    fake = FakeEmbeddingProvider(dimensions=dimensions, model_alias=TEST_MODEL_ALIAS)
    gateway = ModelGateway(_make_config(), _FakeProviderBackedTransport(fake))
    return AzureEmbeddingProvider(gateway=gateway, dimensions=dimensions)


# ── The gateway is a transparent pass-through for vectors ─────────────────

def test_gateway_backed_provider_produces_bit_identical_vectors_to_the_fake_provider_directly():
    texts = [c["text"] for c in _chunks()]
    direct = _direct_provider()
    via_gateway = _gateway_backed_provider()

    assert via_gateway.embed(texts) == direct.embed(texts)
    assert via_gateway.model_alias == direct.model_alias == TEST_MODEL_ALIAS
    assert via_gateway.dimensions == direct.dimensions


def test_gateway_backed_provider_preserves_batch_order():
    texts = [c["text"] for c in _chunks()]
    direct = _direct_provider()
    via_gateway = _gateway_backed_provider()

    for text, vector in zip(texts, via_gateway.embed(texts)):
        assert direct.embed([text])[0] == vector


# ── Retrieval rankings are unchanged by the migration ──────────────────────

def test_vector_search_rankings_identical_through_the_gateway_and_directly(tmp_path):
    chunks = _chunks()
    direct = _direct_provider()
    via_gateway = _gateway_backed_provider()

    direct_dir, gateway_dir = tmp_path / "direct", tmp_path / "gateway"
    embed_chunks(chunks, direct, (direct_cache := VectorCache()))
    direct_cache.save(direct_dir)
    embed_chunks(chunks, via_gateway, (gateway_cache := VectorCache()))
    gateway_cache.save(gateway_dir)

    for query in ("public liability insurance", "payment terms", "warehouse logistics"):
        direct_results = vector_search(chunks, query, top_k=4, vectors_dir=direct_dir, provider=direct)
        gateway_results = vector_search(chunks, query, top_k=4, vectors_dir=gateway_dir, provider=via_gateway)

        assert [(r.chunk["chunk_id"], r.score) for r in direct_results] == \
               [(r.chunk["chunk_id"], r.score) for r in gateway_results]


def test_hybrid_search_rankings_identical_through_the_gateway_and_directly(tmp_path):
    chunks = _chunks()
    direct = _direct_provider()
    via_gateway = _gateway_backed_provider()

    direct_dir, gateway_dir = tmp_path / "direct", tmp_path / "gateway"
    embed_chunks(chunks, direct, (direct_cache := VectorCache()))
    direct_cache.save(direct_dir)
    embed_chunks(chunks, via_gateway, (gateway_cache := VectorCache()))
    gateway_cache.save(gateway_dir)

    query = "supplier evaluation price weighting"
    direct_results = hybrid_search(chunks, query, top_k=4, vectors_dir=direct_dir, provider=direct)
    gateway_results = hybrid_search(chunks, query, top_k=4, vectors_dir=gateway_dir, provider=via_gateway)

    assert [(r.chunk["chunk_id"], r.score) for r in direct_results] == \
           [(r.chunk["chunk_id"], r.score) for r in gateway_results]


def test_bm25_mode_is_untouched_by_the_gateway_migration_entirely():
    # bm25 mode never touches an embedding provider at all - included here
    # as a reminder that the migration's blast radius is the embedding
    # path only, never lexical retrieval.
    chunks = _chunks()
    results = bm25_search(chunks, "payment terms", top_k=4)
    assert results and results[0].chunk["chunk_id"] == "c2"


def test_reciprocal_rank_fusion_logic_is_untouched_by_the_gateway_migration():
    # hybrid.py has no dependency on the embedding provider or the
    # gateway at all - a hand-worked fusion example, unrelated to Day 3.
    ranked_lists = {
        "bm25": [{"chunk_id": "a"}, {"chunk_id": "b"}],
        "vector": [{"chunk_id": "b"}, {"chunk_id": "a"}],
    }
    fused = reciprocal_rank_fusion(ranked_lists, k=60)
    assert [r.chunk["chunk_id"] for r in fused] == ["a", "b"]  # tie broken deterministically
