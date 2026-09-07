"""
Task 4 — routing policy and safe fallback.

Fallback is never automatic: it only happens when (a) the primary failure
is retryable and exhausted its own retry ceiling (GatewayRetryCeilingExceededError)
- never for a non-retryable authentication/bad_request failure, (b) a
fallback transport is actually configured, (c) `routing.fallback.enabled`
is true, and (d) every one of the five compatibility axes (provider/
region/data_boundary/risk/budget) actually matches between the primary and
fallback routes - unconditionally; there is no config-driven way to relax
one (see `test_require_compatibility_false_never_relaxes_a_mismatch_all_axes_are_mandatory`).
Scenarios mirror day03_pack/fixtures/gateway_cases.json's fallback cases
(G10-G14) without depending on that file's exact shape.

Required test cases, one function each:
1. allowed route proceeds (fully-compatible fallback serves a failed primary)
2. policy-disallowed fallback is blocked (routing.fallback.enabled=false)
3. region mismatch blocks fallback
4. data-boundary mismatch blocks fallback
5. risk incompatibility blocks fallback
6. budget incompatibility blocks fallback

Plus: no fallback transport configured at all -> the primary error
propagates unchanged (nothing to silently switch to); a successful
fallback is marked `used_fallback=True` in metadata so the result is
always explainable; cancellation is never treated as a fallback trigger.
"""
from __future__ import annotations

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
from aico.platform.errors import (
    GatewayAuthenticationError,
    GatewayBadRequestError,
    GatewayFallbackBlockedError,
    GatewayServerError,
)
from aico.platform.model_gateway import (
    CancellationToken,
    ChatMessage,
    ChatRequest,
    EmbedRequest,
    ModelGateway,
    TransportResult,
)

PRIMARY_ROUTE = RouteEndpoint(
    provider="microsoft-foundry", region="uk-south", data_boundary="uk", risk_class="standard"
)

FULLY_COMPATIBLE_FALLBACK_ROUTE = RouteEndpoint(
    provider="microsoft-foundry", region="uk-south", data_boundary="uk", risk_class="standard"
)

ALL_REQUIRED = {"provider": True, "region": True, "data_boundary": True, "risk": True, "budget": True}


def _make_config(*, fallback_enabled: bool, fallback_route: RouteEndpoint | None, require=None, **overrides) -> GatewayConfig:
    defaults = dict(
        version="1.0",
        endpoint_env="AICO_TEST_FOUNDRY_ENDPOINT",
        models=ModelAliases(chat="test-chat-alias", embedding="test-embed-alias"),
        resilience=ResilienceConfig(
            timeout_seconds=5,
            retry=RetryConfig(max_attempts=2, base_delay_ms=10, max_delay_ms=100, jitter=False),
        ),
        budgets=BudgetsConfig(
            chat=ChatBudget(max_input_tokens=100, max_output_tokens=50),
            embedding=EmbeddingBudget(max_items_per_call=32),
        ),
        routing=RoutingPolicy(
            primary=PRIMARY_ROUTE,
            fallback=FallbackPolicy(
                enabled=fallback_enabled,
                route=fallback_route,
                require_compatibility=require or dict(ALL_REQUIRED),
            ),
        ),
    )
    defaults.update(overrides)
    return GatewayConfig(**defaults)


class FailingTransport:
    """Primary transport that always fails with a retryable server error -
    exhausts the (short) retry ceiling every time, deterministically."""

    def __init__(self):
        self.calls = 0

    def embed(self, *, model_alias, texts, timeout_seconds):
        self.calls += 1
        raise GatewayServerError("primary embed failed")

    def chat(self, *, model_alias, messages, max_output_tokens, timeout_seconds):
        self.calls += 1
        raise GatewayServerError("primary chat failed")


class SucceedingTransport:
    """A transport that always succeeds - stands in for the fallback route."""

    def __init__(self):
        self.calls = 0

    def embed(self, *, model_alias, texts, timeout_seconds):
        self.calls += 1
        return TransportResult(content=[[0.9, 0.1]], dimensions=2, token_usage=None)

    def chat(self, *, model_alias, messages, max_output_tokens, timeout_seconds):
        self.calls += 1
        return TransportResult(content="fallback completion", dimensions=None, token_usage=None)


# ── 1. allowed route proceeds ───────────────────────────────────────────

def test_allowed_route_proceeds_through_a_fully_compatible_fallback():
    primary = FailingTransport()
    fallback = SucceedingTransport()
    config = _make_config(fallback_enabled=True, fallback_route=FULLY_COMPATIBLE_FALLBACK_ROUTE)
    gateway = ModelGateway(config, primary, fallback_transport=fallback, sleep=lambda s: None)

    result = gateway.chat(ChatRequest(messages=[ChatMessage(role="user", content="hi")]))

    assert result.content == "fallback completion"
    assert result.metadata.used_fallback is True
    assert fallback.calls == 1


# ── 2. policy-disallowed fallback is blocked ────────────────────────────

def test_policy_disallowed_fallback_is_blocked():
    primary = FailingTransport()
    fallback = SucceedingTransport()  # configured, but policy says no
    config = _make_config(fallback_enabled=False, fallback_route=FULLY_COMPATIBLE_FALLBACK_ROUTE)
    gateway = ModelGateway(config, primary, fallback_transport=fallback, sleep=lambda s: None)

    with pytest.raises(GatewayFallbackBlockedError) as excinfo:
        gateway.chat(ChatRequest(messages=[ChatMessage(role="user", content="hi")]))

    assert fallback.calls == 0  # never silently tried anyway
    assert excinfo.value.cause is not None  # the primary failure is explainable, not swallowed


# ── 3. region mismatch blocks fallback ──────────────────────────────────

def test_region_mismatch_blocks_fallback():
    primary = FailingTransport()
    fallback = SucceedingTransport()
    mismatched_route = RouteEndpoint(
        provider="microsoft-foundry", region="us-east", data_boundary="uk", risk_class="standard"
    )
    config = _make_config(fallback_enabled=True, fallback_route=mismatched_route)
    gateway = ModelGateway(config, primary, fallback_transport=fallback, sleep=lambda s: None)

    with pytest.raises(GatewayFallbackBlockedError, match="region"):
        gateway.embed(EmbedRequest(texts=["x"]))
    assert fallback.calls == 0


# ── 4. data-boundary mismatch blocks fallback ───────────────────────────

def test_data_boundary_mismatch_blocks_fallback():
    primary = FailingTransport()
    fallback = SucceedingTransport()
    mismatched_route = RouteEndpoint(
        provider="microsoft-foundry", region="uk-south", data_boundary="us", risk_class="standard"
    )
    config = _make_config(fallback_enabled=True, fallback_route=mismatched_route)
    gateway = ModelGateway(config, primary, fallback_transport=fallback, sleep=lambda s: None)

    with pytest.raises(GatewayFallbackBlockedError, match="data_boundary"):
        gateway.embed(EmbedRequest(texts=["x"]))
    assert fallback.calls == 0


# ── 5. risk incompatibility blocks fallback ─────────────────────────────

def test_risk_incompatibility_blocks_fallback():
    primary = FailingTransport()
    fallback = SucceedingTransport()
    mismatched_route = RouteEndpoint(
        provider="microsoft-foundry", region="uk-south", data_boundary="uk", risk_class="elevated"
    )
    config = _make_config(fallback_enabled=True, fallback_route=mismatched_route)
    gateway = ModelGateway(config, primary, fallback_transport=fallback, sleep=lambda s: None)

    with pytest.raises(GatewayFallbackBlockedError, match="risk"):
        gateway.embed(EmbedRequest(texts=["x"]))
    assert fallback.calls == 0


# ── 6. budget incompatibility blocks fallback ───────────────────────────

def test_budget_incompatibility_blocks_fallback():
    primary = FailingTransport()
    fallback = SucceedingTransport()
    config = _make_config(fallback_enabled=True, fallback_route=FULLY_COMPATIBLE_FALLBACK_ROUTE)
    gateway = ModelGateway(config, primary, fallback_transport=fallback, sleep=lambda s: None)

    # embedding.max_items_per_call is 32 - 33 items is over budget before
    # any call is even attempted, so fallback must not be used to retry it.
    oversized_request = EmbedRequest(texts=["x"] * 33)

    with pytest.raises(GatewayFallbackBlockedError, match="budget"):
        gateway.embed(oversized_request)
    assert fallback.calls == 0


def test_chat_budget_incompatibility_blocks_fallback():
    primary = FailingTransport()
    fallback = SucceedingTransport()
    config = _make_config(fallback_enabled=True, fallback_route=FULLY_COMPATIBLE_FALLBACK_ROUTE)
    gateway = ModelGateway(config, primary, fallback_transport=fallback, sleep=lambda s: None)

    # budgets.chat.max_output_tokens is 50.
    over_budget_request = ChatRequest(
        messages=[ChatMessage(role="user", content="hi")], max_output_tokens=500
    )

    with pytest.raises(GatewayFallbackBlockedError, match="budget"):
        gateway.chat(over_budget_request)
    assert fallback.calls == 0


# ── Additional coverage: provider mismatch, no fallback wired, cancellation ─

def test_provider_mismatch_blocks_fallback():
    primary = FailingTransport()
    fallback = SucceedingTransport()
    mismatched_route = RouteEndpoint(
        provider="other-cloud-provider", region="uk-south", data_boundary="uk", risk_class="standard"
    )
    config = _make_config(fallback_enabled=True, fallback_route=mismatched_route)
    gateway = ModelGateway(config, primary, fallback_transport=fallback, sleep=lambda s: None)

    with pytest.raises(GatewayFallbackBlockedError, match="provider"):
        gateway.embed(EmbedRequest(texts=["x"]))
    assert fallback.calls == 0


def test_require_compatibility_false_never_relaxes_a_mismatch_all_axes_are_mandatory():
    # Correction: routing.fallback.require_compatibility used to let a
    # specific axis be turned off, tolerating a mismatch on it. All five
    # axes are now unconditionally mandatory (Task 4 / ADR-003) - a False
    # entry here (which load_gateway_config() rejects outright at YAML
    # load time - see test_model_gateway.py's
    # test_load_gateway_config_rejects_a_disabled_compatibility_axis)
    # must still block a mismatch even when a GatewayConfig is built
    # directly in Python, bypassing that YAML-level validation, exactly
    # as every test in this file does.
    primary = FailingTransport()
    fallback = SucceedingTransport()
    mismatched_region_route = RouteEndpoint(
        provider="microsoft-foundry", region="us-east", data_boundary="uk", risk_class="standard"
    )
    relaxed_requirements = dict(ALL_REQUIRED)
    relaxed_requirements["region"] = False  # would have tolerated this before the correction
    config = _make_config(fallback_enabled=True, fallback_route=mismatched_region_route, require=relaxed_requirements)
    gateway = ModelGateway(config, primary, fallback_transport=fallback, sleep=lambda s: None)

    with pytest.raises(GatewayFallbackBlockedError, match="region"):
        gateway.embed(EmbedRequest(texts=["x"]))
    assert fallback.calls == 0


def test_no_fallback_transport_configured_lets_the_primary_error_propagate_unchanged():
    primary = FailingTransport()
    config = _make_config(fallback_enabled=True, fallback_route=FULLY_COMPATIBLE_FALLBACK_ROUTE)
    gateway = ModelGateway(config, primary, sleep=lambda s: None)  # no fallback_transport at all

    from aico.platform.errors import GatewayRetryCeilingExceededError

    with pytest.raises(GatewayRetryCeilingExceededError):
        gateway.embed(EmbedRequest(texts=["x"]))


def test_cancellation_is_never_treated_as_a_trigger_for_fallback():
    from aico.platform.errors import GatewayCancelledError

    token = CancellationToken()
    token.cancel()
    primary = FailingTransport()
    fallback = SucceedingTransport()
    config = _make_config(fallback_enabled=True, fallback_route=FULLY_COMPATIBLE_FALLBACK_ROUTE)
    gateway = ModelGateway(config, primary, fallback_transport=fallback, sleep=lambda s: None)

    with pytest.raises(GatewayCancelledError):
        gateway.embed(EmbedRequest(texts=["x"], cancellation=token))
    assert primary.calls == 0
    assert fallback.calls == 0


class AuthFailingTransport:
    """Primary transport that always fails with a non-retryable
    authentication error - the credential is wrong, not the route."""

    def __init__(self):
        self.calls = 0

    def embed(self, *, model_alias, texts, timeout_seconds):
        self.calls += 1
        raise GatewayAuthenticationError("primary rejected the identity's token")

    def chat(self, *, model_alias, messages, max_output_tokens, timeout_seconds):
        self.calls += 1
        raise GatewayAuthenticationError("primary rejected the identity's token")


class BadRequestFailingTransport:
    """Primary transport that always fails with a non-retryable bad-request
    error - the request itself is malformed, not the route."""

    def __init__(self):
        self.calls = 0

    def embed(self, *, model_alias, texts, timeout_seconds):
        self.calls += 1
        raise GatewayBadRequestError("primary rejected the request as malformed")

    def chat(self, *, model_alias, messages, max_output_tokens, timeout_seconds):
        self.calls += 1
        raise GatewayBadRequestError("primary rejected the request as malformed")


# ── 7. non-retryable primary failure is never a fallback candidate ─────

def test_authentication_failure_at_primary_never_triggers_fallback():
    # Fallback is fully compatible and enabled - a non-retryable
    # authentication failure must still never be handed to it. Switching
    # routes cannot fix a rejected credential.
    primary = AuthFailingTransport()
    fallback = SucceedingTransport()
    config = _make_config(fallback_enabled=True, fallback_route=FULLY_COMPATIBLE_FALLBACK_ROUTE)
    gateway = ModelGateway(config, primary, fallback_transport=fallback, sleep=lambda s: None)

    with pytest.raises(GatewayAuthenticationError):
        gateway.chat(ChatRequest(messages=[ChatMessage(role="user", content="hi")]))

    assert primary.calls == 1  # non-retryable - no retry either
    assert fallback.calls == 0  # never even considered


def test_bad_request_failure_at_primary_never_triggers_fallback():
    primary = BadRequestFailingTransport()
    fallback = SucceedingTransport()
    config = _make_config(fallback_enabled=True, fallback_route=FULLY_COMPATIBLE_FALLBACK_ROUTE)
    gateway = ModelGateway(config, primary, fallback_transport=fallback, sleep=lambda s: None)

    with pytest.raises(GatewayBadRequestError):
        gateway.embed(EmbedRequest(texts=["x"]))

    assert primary.calls == 1
    assert fallback.calls == 0


# ── 8. aggregated retry_count reflects both legs, never an undercount ──

def test_fallback_retry_count_folds_in_the_primarys_spent_attempts():
    # Primary is retryable (server_error) and exhausts its own ceiling
    # (max_attempts=2 in _make_config -> 2 failed primary calls); fallback
    # then needs one retry of its own before succeeding. The final
    # retry_count must account for every call made, not just the
    # fallback leg's own count.
    primary = FailingTransport()

    class FlakyOnceThenSucceedFallback:
        def __init__(self):
            self.calls = 0

        def chat(self, *, model_alias, messages, max_output_tokens, timeout_seconds):
            self.calls += 1
            if self.calls < 2:
                raise GatewayServerError("fallback flaky once")
            return TransportResult(content="fallback ok", dimensions=None, token_usage=None)

        def embed(self, *, model_alias, texts, timeout_seconds):
            raise AssertionError("not exercised by this test")

    fallback = FlakyOnceThenSucceedFallback()
    config = _make_config(fallback_enabled=True, fallback_route=FULLY_COMPATIBLE_FALLBACK_ROUTE)
    gateway = ModelGateway(config, primary, fallback_transport=fallback, sleep=lambda s: None)

    result = gateway.chat(ChatRequest(messages=[ChatMessage(role="user", content="hi")]))

    assert result.content == "fallback ok"
    assert primary.calls == 2  # exhausted its own 2-attempt ceiling
    assert fallback.calls == 2  # one retry, then success
    # 2 spent primary attempts + 1 fallback retry = 3
    assert result.metadata.retry_count == 3


def test_successful_primary_call_never_touches_the_fallback_transport():
    class OnlySuccessTransport:
        def __init__(self):
            self.calls = 0

        def embed(self, *, model_alias, texts, timeout_seconds):
            self.calls += 1
            return TransportResult(content=[[1.0, 0.0]], dimensions=2, token_usage=None)

        def chat(self, *, model_alias, messages, max_output_tokens, timeout_seconds):
            raise AssertionError("chat should not be called in this test")

    primary = OnlySuccessTransport()
    fallback = SucceedingTransport()
    config = _make_config(fallback_enabled=True, fallback_route=FULLY_COMPATIBLE_FALLBACK_ROUTE)
    gateway = ModelGateway(config, primary, fallback_transport=fallback, sleep=lambda s: None)

    result = gateway.embed(EmbedRequest(texts=["x"]))

    assert result.metadata.used_fallback is False
    assert fallback.calls == 0
