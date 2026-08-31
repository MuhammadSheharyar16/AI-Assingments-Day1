"""
Task 6 — required error-normalization coverage at the HTTP layer.

test_model_gateway_retry.py already proves the gateway's retry/ceiling
behavior for each normalized category via aico.platform.errors.
error_for_category (a direct, provider-agnostic mapping). This file proves
the other half: that aico.platform.foundry_adapter.FoundryAdapter itself -
the one file that actually calls `requests.post` - turns a real transport
exception or a real HTTP status code into the *same* typed errors, with no
real network call (requests.post is monkeypatched throughout).

Required categories: timeout, rate limit, authentication, bad request,
server error - plus a non-network exception (a connection failure) and an
unexpected status code that isn't one of the specifically-handled ones.
"""
from __future__ import annotations

import requests

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
from aico.platform.errors import (
    GatewayAuthenticationError,
    GatewayBadRequestError,
    GatewayRateLimitError,
    GatewayServerError,
    GatewayTimeoutError,
    ModelGatewayError,
)
from aico.platform.foundry_adapter import FoundryAdapter

import pytest
from azure.core.credentials import AccessToken


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


class _FakeCredential:
    def get_token(self, *scopes, **kwargs):
        return AccessToken("fake-token", 9_999_999_999)


class _FakeResponse:
    """A faithful-enough stand-in for requests.Response: json() returns the
    scripted body, raise_for_status() actually raises requests.HTTPError
    for any 4xx/5xx status - matching the real library's behavior, unlike
    a response double that always no-ops."""

    def __init__(self, status_code: int, json_body: dict | None = None):
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else {}

    def json(self):
        return self._json_body

    def raise_for_status(self):
        if 400 <= self.status_code < 600:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


@pytest.fixture(autouse=True)
def _fake_endpoint(monkeypatch):
    monkeypatch.setenv("AICO_TEST_FOUNDRY_ENDPOINT", "https://fake-foundry.example.test")


def _adapter() -> FoundryAdapter:
    return FoundryAdapter(_make_config(), credential=_FakeCredential())


# ── Network-level exceptions ────────────────────────────────────────────

def test_requests_timeout_normalizes_to_gateway_timeout_error(monkeypatch):
    def fake_post(url, **kwargs):
        raise requests.Timeout("connect timed out")

    monkeypatch.setattr("aico.platform.foundry_adapter.requests.post", fake_post)
    adapter = _adapter()

    with pytest.raises(GatewayTimeoutError):
        adapter.embed(model_alias="a", texts=["x"], timeout_seconds=5)


def test_connection_error_normalizes_to_gateway_server_error(monkeypatch):
    def fake_post(url, **kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr("aico.platform.foundry_adapter.requests.post", fake_post)
    adapter = _adapter()

    with pytest.raises(GatewayServerError):
        adapter.chat(model_alias="a", messages=[{"role": "user", "content": "hi"}],
                     max_output_tokens=None, timeout_seconds=5)


# ── HTTP status codes ────────────────────────────────────────────────────

def test_429_normalizes_to_rate_limit_error(monkeypatch):
    monkeypatch.setattr("aico.platform.foundry_adapter.requests.post", lambda url, **kw: _FakeResponse(429))
    adapter = _adapter()

    with pytest.raises(GatewayRateLimitError):
        adapter.embed(model_alias="a", texts=["x"], timeout_seconds=5)


def test_401_normalizes_to_authentication_error(monkeypatch):
    monkeypatch.setattr("aico.platform.foundry_adapter.requests.post", lambda url, **kw: _FakeResponse(401))
    adapter = _adapter()

    with pytest.raises(GatewayAuthenticationError):
        adapter.embed(model_alias="a", texts=["x"], timeout_seconds=5)


def test_403_normalizes_to_authentication_error(monkeypatch):
    monkeypatch.setattr("aico.platform.foundry_adapter.requests.post", lambda url, **kw: _FakeResponse(403))
    adapter = _adapter()

    with pytest.raises(GatewayAuthenticationError):
        adapter.chat(model_alias="a", messages=[{"role": "user", "content": "hi"}],
                     max_output_tokens=None, timeout_seconds=5)


def test_400_normalizes_to_bad_request_error(monkeypatch):
    monkeypatch.setattr("aico.platform.foundry_adapter.requests.post", lambda url, **kw: _FakeResponse(400))
    adapter = _adapter()

    with pytest.raises(GatewayBadRequestError):
        adapter.embed(model_alias="a", texts=["x"], timeout_seconds=5)


@pytest.mark.parametrize("status_code", [500, 502, 503])
def test_5xx_normalizes_to_server_error(monkeypatch, status_code):
    monkeypatch.setattr("aico.platform.foundry_adapter.requests.post", lambda url, **kw: _FakeResponse(status_code))
    adapter = _adapter()

    with pytest.raises(GatewayServerError):
        adapter.chat(model_alias="a", messages=[{"role": "user", "content": "hi"}],
                     max_output_tokens=None, timeout_seconds=5)


def test_unhandled_error_status_still_normalizes_to_a_typed_gateway_error(monkeypatch):
    # 402 isn't one of the specifically-handled codes - it must still come
    # back as a ModelGatewayError (via raise_for_status -> HTTPError),
    # never a raw requests exception.
    monkeypatch.setattr("aico.platform.foundry_adapter.requests.post", lambda url, **kw: _FakeResponse(402))
    adapter = _adapter()

    with pytest.raises(ModelGatewayError):
        adapter.embed(model_alias="a", texts=["x"], timeout_seconds=5)


# ── Success path sanity (not a failure case, but proves the fakes are wired right) ─

def test_2xx_response_returns_the_parsed_payload(monkeypatch):
    monkeypatch.setattr(
        "aico.platform.foundry_adapter.requests.post",
        lambda url, **kw: _FakeResponse(200, {"data": [{"index": 0, "embedding": [0.1, 0.2]}]}),
    )
    adapter = _adapter()

    result = adapter.embed(model_alias="a", texts=["x"], timeout_seconds=5)
    assert result.content == [[0.1, 0.2]]
