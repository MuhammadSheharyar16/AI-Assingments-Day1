"""
Task 1 — typed Model Gateway boundary.

- SDK isolation: no file outside src/aico/platform imports `requests` (the
  HTTP client used to reach the provider) - the required repository check.
- The gateway's typed chat/embed contract works end to end against a fake
  transport (no network call, ever, in this file).
- Successful calls return sanitized metadata (model_alias, latency,
  retry_count, budget_status) and never put prompt/completion text inside it.
- Day 2 embedding traffic goes through the gateway: AzureEmbeddingProvider
  implements the same EmbeddingProvider interface embed.py/search.py/
  vector_index.py already depend on.
- A transport failure comes back as a typed ModelGatewayError, never the
  raw underlying exception.
- Missing/invalid required routing configuration fails with
  GatewayConfigurationError, not a silent default.

Bounded retry (ceiling/backoff/jitter), full timeout/cancellation-mid-call
behavior, and routing/fallback policy are Task 3 and Task 4 - covered in
test_model_gateway_retry.py / test_model_gateway_routing.py, not here.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

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
from aico.platform.errors import GatewayConfigurationError, GatewayServerError, ModelGatewayError
from aico.platform.model_gateway import (
    CancellationToken,
    ChatMessage,
    ChatRequest,
    EmbedRequest,
    ModelGateway,
    TransportResult,
)
from aico.retrieval.embedding_provider import EmbeddingProvider, AzureEmbeddingProvider

REPO_ROOT = Path(__file__).resolve().parent.parent
SDK_IMPORT_PATTERN = re.compile(r"^\s*(import requests\b|from requests\b)", re.MULTILINE)


def _make_config(**overrides) -> GatewayConfig:
    defaults = dict(
        version="1.0",
        endpoint_env="AICO_TEST_FOUNDRY_ENDPOINT",
        models=ModelAliases(chat="test-chat-alias", embedding="test-embed-alias"),
        resilience=ResilienceConfig(
            timeout_seconds=5,
            retry=RetryConfig(max_attempts=3, base_delay_ms=100, max_delay_ms=1000, jitter=True),
        ),
        budgets=BudgetsConfig(
            chat=ChatBudget(max_input_tokens=100, max_output_tokens=50),
            embedding=EmbeddingBudget(max_items_per_call=32),
        ),
        routing=RoutingPolicy(
            primary=RouteEndpoint(
                provider="microsoft-foundry", region="uk-south", data_boundary="uk", risk_class="standard"
            ),
            fallback=FallbackPolicy(
                enabled=False,
                route=None,
                require_compatibility={
                    "provider": True, "region": True, "data_boundary": True, "risk": True, "budget": True,
                },
            ),
        ),
    )
    defaults.update(overrides)
    return GatewayConfig(**defaults)


class FakeTransport:
    """Deterministic, in-memory Transport double - satisfies
    model_gateway.Transport without touching the network."""

    def __init__(self, *, embed_result=None, chat_result=None, raises: Exception | None = None):
        self._embed_result = embed_result
        self._chat_result = chat_result
        self._raises = raises
        self.embed_calls: list[dict] = []
        self.chat_calls: list[dict] = []

    def embed(self, *, model_alias, texts, timeout_seconds):
        self.embed_calls.append({"model_alias": model_alias, "texts": texts, "timeout_seconds": timeout_seconds})
        if self._raises is not None:
            raise self._raises
        vectors = self._embed_result or [[0.1, 0.2, 0.3] for _ in texts]
        return TransportResult(content=vectors, dimensions=len(vectors[0]) if vectors else 0, token_usage=None)

    def chat(self, *, model_alias, messages, max_output_tokens, timeout_seconds):
        self.chat_calls.append(
            {"model_alias": model_alias, "messages": messages,
             "max_output_tokens": max_output_tokens, "timeout_seconds": timeout_seconds}
        )
        if self._raises is not None:
            raise self._raises
        content = self._chat_result or "fake completion"
        return TransportResult(content=content, dimensions=None, token_usage={"prompt_tokens": 10, "completion_tokens": 5})


# ── SDK isolation (required repository check) ───────────────────────────

def test_no_model_sdk_import_outside_platform_package():
    src_root = REPO_ROOT / "src"
    platform_dir = src_root / "aico" / "platform"
    offenders = []
    for path in src_root.rglob("*.py"):
        if platform_dir in path.parents:
            continue
        text = path.read_text(encoding="utf-8")
        if SDK_IMPORT_PATTERN.search(text):
            offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert offenders == [], f"HTTP client imported outside the platform package: {offenders}"


def test_requests_import_confined_to_foundry_adapter():
    platform_dir = REPO_ROOT / "src" / "aico" / "platform"
    files_importing_requests = [
        p.relative_to(REPO_ROOT).as_posix()
        for p in platform_dir.glob("*.py")
        if SDK_IMPORT_PATTERN.search(p.read_text(encoding="utf-8"))
    ]
    assert files_importing_requests == ["src/aico/platform/foundry_adapter.py"]


# ── Typed chat/embed contract ────────────────────────────────────────────

def test_embed_returns_typed_result_with_sanitized_metadata():
    gateway = ModelGateway(_make_config(), FakeTransport())
    result = gateway.embed(EmbedRequest(texts=["alpha", "beta"]))

    assert result.vectors == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert result.metadata.operation == "embed"
    assert result.metadata.model_alias == "test-embed-alias"
    assert result.metadata.retry_count == 0
    assert result.metadata.latency_ms >= 0
    assert result.metadata.budget_status == "within_budget"


def test_chat_returns_typed_result_with_sanitized_metadata():
    gateway = ModelGateway(_make_config(), FakeTransport(chat_result="hello there"))
    result = gateway.chat(ChatRequest(messages=[ChatMessage(role="user", content="hi")]))

    assert result.content == "hello there"
    assert result.metadata.operation == "chat"
    assert result.metadata.model_alias == "test-chat-alias"
    assert result.metadata.token_usage == {"prompt_tokens": 10, "completion_tokens": 5}
    assert result.metadata.budget_status == "within_budget"


def test_metadata_never_contains_prompt_or_completion_text():
    transport = FakeTransport(chat_result="the actual completion text")
    gateway = ModelGateway(_make_config(), transport)
    result = gateway.chat(ChatRequest(messages=[ChatMessage(role="user", content="a secret prompt")]))

    metadata_repr = repr(result.metadata)
    assert "the actual completion text" not in metadata_repr
    assert "a secret prompt" not in metadata_repr


def test_embed_budget_status_exceeded_when_over_configured_item_limit():
    config = _make_config(budgets=BudgetsConfig(
        chat=ChatBudget(max_input_tokens=100, max_output_tokens=50),
        embedding=EmbeddingBudget(max_items_per_call=1),
    ))
    gateway = ModelGateway(config, FakeTransport())
    result = gateway.embed(EmbedRequest(texts=["one", "two"]))
    assert result.metadata.budget_status == "exceeded"


def test_embed_uses_configured_alias_and_timeout_by_default():
    transport = FakeTransport()
    gateway = ModelGateway(_make_config(), transport)
    gateway.embed(EmbedRequest(texts=["x"]))
    assert transport.embed_calls[0]["model_alias"] == "test-embed-alias"
    assert transport.embed_calls[0]["timeout_seconds"] == 5


def test_cancellation_blocks_the_call_before_it_starts():
    from aico.platform.errors import GatewayCancelledError

    token = CancellationToken()
    token.cancel()
    transport = FakeTransport()
    gateway = ModelGateway(_make_config(), transport)

    with pytest.raises(GatewayCancelledError):
        gateway.embed(EmbedRequest(texts=["x"], cancellation=token))
    assert transport.embed_calls == []  # never reached the transport


def test_transport_failure_is_normalized_not_raw():
    transport = FakeTransport(raises=GatewayServerError("boom"))
    gateway = ModelGateway(_make_config(), transport)
    with pytest.raises(GatewayServerError):
        gateway.embed(EmbedRequest(texts=["x"]))


def test_unexpected_transport_exception_is_wrapped_not_leaked():
    transport = FakeTransport(raises=ValueError("some raw provider-shaped surprise"))
    gateway = ModelGateway(_make_config(), transport)
    with pytest.raises(ModelGatewayError):
        gateway.embed(EmbedRequest(texts=["x"]))


# ── Day 2 migration: embedding traffic behind the gateway ───────────────

def test_gateway_embedding_provider_satisfies_embedding_provider_interface():
    transport = FakeTransport(embed_result=[[1.0, 0.0], [0.0, 1.0]])
    gateway = ModelGateway(_make_config(), transport)
    provider: EmbeddingProvider = AzureEmbeddingProvider(gateway=gateway, dimensions=2)

    assert provider.model_alias == "test-embed-alias"
    assert provider.dimensions == 2
    assert provider.embed(["chunk one", "chunk two"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert transport.embed_calls[0]["texts"] == ["chunk one", "chunk two"]


def test_gateway_embedding_provider_empty_input_makes_no_transport_call():
    transport = FakeTransport()
    gateway = ModelGateway(_make_config(), transport)
    provider = AzureEmbeddingProvider(gateway=gateway)

    assert provider.embed([]) == []
    assert transport.embed_calls == []


# ── Config validation ────────────────────────────────────────────────────

def test_load_gateway_config_missing_file_raises_configuration_error(tmp_path):
    from aico.platform.config import load_gateway_config

    with pytest.raises(GatewayConfigurationError):
        load_gateway_config(tmp_path / "does-not-exist.yaml")


def test_load_gateway_config_rejects_placeholder_alias(tmp_path):
    from aico.platform.config import load_gateway_config

    config_path = tmp_path / "model-routing.yaml"
    config_path.write_text(
        """
version: "1.0"
foundry:
  endpoint_env: "AICO_TEST_ENDPOINT"
models:
  chat:
    alias: "__LEAD_PROVIDED_CHAT_ALIAS__"
  embedding:
    alias: "real-embed-alias"
resilience:
  timeout_seconds: 20
  retry:
    max_attempts: 3
    base_delay_ms: 250
    max_delay_ms: 2000
    jitter: true
budgets:
  chat:
    max_input_tokens: 8000
    max_output_tokens: 1000
  embedding:
    max_items_per_call: 32
routing:
  primary:
    provider: "microsoft-foundry"
    region: "uk-south"
    data_boundary: "uk"
    risk_class: "standard"
  fallback:
    enabled: false
    provider: "n/a"
    region: "n/a"
    data_boundary: "n/a"
    risk_class: "standard"
    require_compatibility:
      provider: true
      region: true
      data_boundary: true
      risk: true
      budget: true
""",
        encoding="utf-8",
    )
    with pytest.raises(GatewayConfigurationError, match="placeholder"):
        load_gateway_config(config_path)


def test_load_gateway_config_accepts_a_fully_filled_in_file(tmp_path):
    from aico.platform.config import load_gateway_config

    config_path = tmp_path / "model-routing.yaml"
    config_path.write_text(
        """
version: "1.0"
foundry:
  endpoint_env: "AICO_TEST_ENDPOINT"
models:
  chat:
    alias: "chat-alias"
  embedding:
    alias: "embed-alias"
resilience:
  timeout_seconds: 20
  retry:
    max_attempts: 3
    base_delay_ms: 250
    max_delay_ms: 2000
    jitter: true
budgets:
  chat:
    max_input_tokens: 8000
    max_output_tokens: 1000
  embedding:
    max_items_per_call: 32
routing:
  primary:
    provider: "microsoft-foundry"
    region: "uk-south"
    data_boundary: "uk"
    risk_class: "standard"
  fallback:
    enabled: false
    provider: "n/a"
    region: "n/a"
    data_boundary: "n/a"
    risk_class: "standard"
    require_compatibility:
      provider: true
      region: true
      data_boundary: true
      risk: true
      budget: true
""",
        encoding="utf-8",
    )
    config = load_gateway_config(config_path)
    assert config.models.chat == "chat-alias"
    assert config.models.embedding == "embed-alias"
    assert config.resilience.retry.max_attempts == 3
    assert config.budgets.embedding.max_items_per_call == 32
    assert config.routing.fallback.enabled is False
