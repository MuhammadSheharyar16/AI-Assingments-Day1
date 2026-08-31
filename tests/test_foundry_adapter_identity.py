"""
Task 2 — identity-based authentication.

- FoundryAdapter authenticates with a bearer token obtained from an
  injected TokenCredential (the same shape azure.identity.
  DefaultAzureCredential satisfies) - never an API key, never a literal
  secret anywhere in this file or in config/model-routing.yaml.
- The token is cached and only re-requested once it is close to expiry.
- A credential that fails to authenticate normalizes to
  GatewayAuthenticationError - never a raw azure-identity exception.
- None of this requires real cloud access or a real identity: every test
  here injects a fake credential and monkeypatches the one network call
  the adapter makes, so a reviewer with no cloud access can still run it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from azure.core.credentials import AccessToken
from azure.core.exceptions import ClientAuthenticationError

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
from aico.platform.errors import GatewayAuthenticationError
from aico.platform.foundry_adapter import FoundryAdapter

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _fake_endpoint(monkeypatch):
    # GatewayConfig.endpoint is read lazily from the environment variable
    # config names (see aico.platform.config) - never hardcoded, including
    # here: this is a fake test value, not a real Foundry endpoint.
    monkeypatch.setenv("AICO_TEST_FOUNDRY_ENDPOINT", "https://fake-foundry.example.test")


def _make_config() -> GatewayConfig:
    return GatewayConfig(
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


class FakeCredential:
    """Stands in for azure.identity.DefaultAzureCredential - same
    `get_token(scope) -> AccessToken` shape (a TokenCredential), no network."""

    def __init__(self, tokens: list[AccessToken] | None = None, raises: Exception | None = None):
        self._tokens = list(tokens or [AccessToken("fake-token-1", 9_999_999_999)])
        self._raises = raises
        self.calls: list[str] = []

    def get_token(self, *scopes: str, **kwargs) -> AccessToken:
        self.calls.append(scopes[0])
        if self._raises is not None:
            raise self._raises
        if len(self._tokens) > 1:
            return self._tokens.pop(0)
        return self._tokens[0]


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict):
        self.status_code = status_code
        self._json_body = json_body

    def json(self):
        return self._json_body

    def raise_for_status(self):
        pass


def test_embed_request_uses_bearer_token_from_injected_credential(monkeypatch):
    credential = FakeCredential(tokens=[AccessToken("secret-token-value", 9_999_999_999)])
    adapter = FoundryAdapter(_make_config(), credential=credential)

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["headers"] = headers
        return _FakeResponse(200, {"data": [{"index": 0, "embedding": [0.1, 0.2]}]})

    monkeypatch.setattr("aico.platform.foundry_adapter.requests.post", fake_post)

    adapter.embed(model_alias="test-embed-alias", texts=["hello"], timeout_seconds=5)

    assert captured["headers"]["Authorization"] == "Bearer secret-token-value"
    assert "api-key" not in captured["headers"]
    assert credential.calls == ["https://cognitiveservices.azure.com/.default"]


def test_token_is_cached_across_calls_within_its_lifetime(monkeypatch):
    credential = FakeCredential(tokens=[AccessToken("tok", 9_999_999_999)])
    adapter = FoundryAdapter(_make_config(), credential=credential)
    monkeypatch.setattr(
        "aico.platform.foundry_adapter.requests.post",
        lambda url, **kw: _FakeResponse(200, {"data": [{"index": 0, "embedding": [0.1]}]}),
    )

    adapter.embed(model_alias="a", texts=["x"], timeout_seconds=5)
    adapter.embed(model_alias="a", texts=["y"], timeout_seconds=5)

    assert len(credential.calls) == 1  # second call served from the cached token


def test_token_is_refreshed_once_it_is_close_to_expiry(monkeypatch):
    import time

    now = time.time()
    credential = FakeCredential(
        tokens=[AccessToken("tok-1", int(now) + 1), AccessToken("tok-2", int(now) + 9_999)]
    )
    adapter = FoundryAdapter(_make_config(), credential=credential)
    monkeypatch.setattr(
        "aico.platform.foundry_adapter.requests.post",
        lambda url, **kw: _FakeResponse(200, {"data": [{"index": 0, "embedding": [0.1]}]}),
    )

    adapter.embed(model_alias="a", texts=["x"], timeout_seconds=5)
    adapter.embed(model_alias="a", texts=["y"], timeout_seconds=5)  # first token already inside the refresh margin

    assert len(credential.calls) == 2


def test_authentication_failure_normalizes_to_gateway_authentication_error(monkeypatch):
    credential = FakeCredential(raises=ClientAuthenticationError("no credential in the chain worked"))
    adapter = FoundryAdapter(_make_config(), credential=credential)

    with pytest.raises(GatewayAuthenticationError):
        adapter.embed(model_alias="a", texts=["x"], timeout_seconds=5)


def test_provider_401_normalizes_to_gateway_authentication_error(monkeypatch):
    credential = FakeCredential()
    adapter = FoundryAdapter(_make_config(), credential=credential)
    monkeypatch.setattr(
        "aico.platform.foundry_adapter.requests.post",
        lambda url, **kw: _FakeResponse(401, {}),
    )

    with pytest.raises(GatewayAuthenticationError):
        adapter.embed(model_alias="a", texts=["x"], timeout_seconds=5)


# ── No credential/secret is ever a literal in source or committed config ──

SUSPICIOUS_KEY_PATTERN = re.compile(r"\b(api[_-]?key|apikey|client[_-]?secret|password|bearer\s+[A-Za-z0-9])", re.I)


def test_foundry_adapter_source_has_no_api_key_handling():
    text = (REPO_ROOT / "src" / "aico" / "platform" / "foundry_adapter.py").read_text(encoding="utf-8")
    assert "api-key" not in text.lower()
    assert "api_key" not in text.lower()


def test_committed_routing_config_has_no_secret_shaped_values():
    config_path = REPO_ROOT / "config" / "model-routing.yaml"
    if not config_path.exists():
        pytest.skip("config/model-routing.yaml not present in this checkout")
    text = config_path.read_text(encoding="utf-8")
    match = SUSPICIOUS_KEY_PATTERN.search(text)
    assert match is None, f"config/model-routing.yaml looks like it contains a credential: {match.group(0)!r}"
