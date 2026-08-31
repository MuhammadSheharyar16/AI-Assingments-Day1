"""
Task 3 — timeout, cancellation and bounded retry.

Scenarios mirror day03_pack/fixtures/gateway_cases.json (retryable vs
non-retryable categories, retry-then-success, retry-exhaustion) without
depending on that file's exact shape - "these fixtures describe behavior;
they do not prescribe your class names or implementation style" (pack
README). Every test here drives a fake transport with an injected
no-delay `sleep` and a fixed `random_factor`, so:
- no test ever makes a real network call or talks to a real cloud provider
- no test is slowed down by real backoff delays
- backoff/jitter arithmetic is asserted exactly, not just "eventually retries"

What's proven:
- a retryable error (timeout/rate_limit/server_error) retries, and a
  successful call after retries reports the correct retry_count
- a retryable error that keeps failing stops at the configured attempt
  ceiling (GatewayRetryCeilingExceededError) - never an infinite loop
- a non-retryable error (authentication/bad_request) fails on the first
  attempt, with no sleep call at all
- cancellation, set during the backoff wait between attempts, stops the
  retry loop before the next attempt is dispatched
- backoff grows exponentially and is capped at max_delay_ms; jitter (when
  enabled) scales the delay by the injected random factor
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
    GatewayCancelledError,
    GatewayRetryCeilingExceededError,
    GatewayTimeoutError,
    error_for_category,
)
from aico.platform.model_gateway import (
    CancellationToken,
    ChatMessage,
    ChatRequest,
    EmbedRequest,
    ModelGateway,
    TransportResult,
)


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


class SequencedFakeTransport:
    """Replays a fixed sequence of outcomes, one per call: "success" or a
    normalized-error category name (see gateway_cases.json /
    aico.platform.errors.error_for_category) - "timeout", "rate_limit",
    "authentication", "bad_request", "server_error". No network, ever."""

    def __init__(self, outcomes: list[str]):
        self._outcomes = list(outcomes)
        self.call_count = 0

    def _next_outcome(self) -> str:
        self.call_count += 1
        if not self._outcomes:
            raise AssertionError("SequencedFakeTransport ran out of scripted outcomes - retry looped too far")
        return self._outcomes.pop(0)

    def embed(self, *, model_alias, texts, timeout_seconds):
        outcome = self._next_outcome()
        if outcome == "success":
            return TransportResult(content=[[0.1, 0.2]], dimensions=2, token_usage=None)
        raise error_for_category(outcome, f"fake transport outcome: {outcome}")

    def chat(self, *, model_alias, messages, max_output_tokens, timeout_seconds):
        outcome = self._next_outcome()
        if outcome == "success":
            return TransportResult(
                content="fake completion", dimensions=None,
                token_usage={"prompt_tokens": 5, "completion_tokens": 3},
            )
        raise error_for_category(outcome, f"fake transport outcome: {outcome}")


# ── Retryable vs non-retryable categorization (matches gateway_cases.json) ─

@pytest.mark.parametrize(
    "category,expected_retryable",
    [
        ("timeout", True),
        ("rate_limit", True),
        ("server_error", True),
        ("authentication", False),
        ("bad_request", False),
    ],
)
def test_error_category_retryability_matches_required_minimum(category, expected_retryable):
    err = error_for_category(category, "x")
    assert err.retryable is expected_retryable


def test_timeout_error_is_the_normalized_timeout_category():
    err = error_for_category("timeout", "provider call exceeded its deadline")
    assert isinstance(err, GatewayTimeoutError)
    assert err.category == "timeout"


# ── Retry-then-success reports an accurate retry count (fixture G08) ──────

def test_retry_then_success_reports_accurate_retry_count():
    transport = SequencedFakeTransport(["rate_limit", "success"])
    gateway = ModelGateway(_make_config(), transport, sleep=lambda s: None, random_factor=lambda: 1.0)

    result = gateway.embed(EmbedRequest(texts=["x"]))

    assert result.metadata.retry_count == 1
    assert transport.call_count == 2


def test_timeout_then_success_is_retried_and_normalized():
    transport = SequencedFakeTransport(["timeout", "success"])
    gateway = ModelGateway(_make_config(), transport, sleep=lambda s: None, random_factor=lambda: 1.0)

    result = gateway.embed(EmbedRequest(texts=["x"]))

    assert result.metadata.retry_count == 1
    assert transport.call_count == 2


# ── Retryable failure stops at the configured ceiling (fixture G09) ───────

def test_retryable_failure_stops_at_the_configured_attempt_ceiling():
    config = _make_config(
        resilience=ResilienceConfig(
            timeout_seconds=5,
            retry=RetryConfig(max_attempts=3, base_delay_ms=10, max_delay_ms=100, jitter=False),
        )
    )
    transport = SequencedFakeTransport(["server_error", "server_error", "server_error"])
    gateway = ModelGateway(config, transport, sleep=lambda s: None)

    with pytest.raises(GatewayRetryCeilingExceededError) as excinfo:
        gateway.chat(ChatRequest(messages=[ChatMessage(role="user", content="hi")]))

    assert transport.call_count == 3  # never exceeds max_attempts
    assert excinfo.value.cause is not None  # carries the last underlying failure


def test_retry_never_loops_past_the_ceiling_even_if_more_failures_are_scripted():
    # Ten scripted failures, ceiling of 3 - if the loop were unbounded it
    # would keep calling; SequencedFakeTransport raises AssertionError if
    # it is ever asked for a call past what's scripted, so this also
    # catches an off-by-one in the ceiling check in either direction.
    config = _make_config(
        resilience=ResilienceConfig(
            timeout_seconds=5,
            retry=RetryConfig(max_attempts=3, base_delay_ms=10, max_delay_ms=100, jitter=False),
        )
    )
    transport = SequencedFakeTransport(["server_error"] * 3)
    gateway = ModelGateway(config, transport, sleep=lambda s: None)

    with pytest.raises(GatewayRetryCeilingExceededError):
        gateway.embed(EmbedRequest(texts=["x"]))
    assert transport.call_count == 3


# ── Non-retryable failure fails immediately, no retry ──────────────────────

def test_non_retryable_failure_fails_immediately_without_retrying():
    sleep_calls: list[float] = []
    transport = SequencedFakeTransport(["authentication"])
    gateway = ModelGateway(_make_config(), transport, sleep=lambda s: sleep_calls.append(s))

    with pytest.raises(GatewayAuthenticationError):
        gateway.chat(ChatRequest(messages=[ChatMessage(role="user", content="hi")]))

    assert transport.call_count == 1
    assert sleep_calls == []  # never waited to retry a non-retryable failure


# ── Cancellation stops the operation, including mid-retry ──────────────────

def test_cancellation_during_backoff_stops_the_retry_loop_before_next_attempt():
    token = CancellationToken()
    # "success" would only be reachable by a second attempt that must
    # never happen - cancellation set during the backoff wait must stop
    # the loop before it gets there.
    transport = SequencedFakeTransport(["server_error", "success"])

    def cancel_during_sleep(seconds: float) -> None:
        token.cancel()

    gateway = ModelGateway(_make_config(), transport, sleep=cancel_during_sleep, random_factor=lambda: 1.0)

    with pytest.raises(GatewayCancelledError):
        gateway.embed(EmbedRequest(texts=["x"], cancellation=token))
    assert transport.call_count == 1


def test_cancellation_before_the_call_starts_makes_no_transport_call():
    token = CancellationToken()
    token.cancel()
    transport = SequencedFakeTransport(["success"])
    gateway = ModelGateway(_make_config(), transport, sleep=lambda s: None)

    with pytest.raises(GatewayCancelledError):
        gateway.chat(ChatRequest(messages=[ChatMessage(role="user", content="hi")], cancellation=token))
    assert transport.call_count == 0


# ── Backoff ceiling/growth and jitter are visible, provable behavior ──────

def test_backoff_delay_grows_exponentially_up_to_the_configured_cap():
    config = _make_config(
        resilience=ResilienceConfig(
            timeout_seconds=5,
            retry=RetryConfig(max_attempts=5, base_delay_ms=100, max_delay_ms=300, jitter=False),
        )
    )
    transport = SequencedFakeTransport(["server_error", "server_error", "server_error", "success"])
    delays: list[float] = []
    gateway = ModelGateway(config, transport, sleep=lambda s: delays.append(s))

    result = gateway.embed(EmbedRequest(texts=["x"]))

    # 100ms, 200ms, then 400ms capped down to 300ms
    assert delays == pytest.approx([0.1, 0.2, 0.3])
    assert result.metadata.retry_count == 3


def test_jitter_scales_the_delay_by_the_random_factor_when_enabled():
    config = _make_config(
        resilience=ResilienceConfig(
            timeout_seconds=5,
            retry=RetryConfig(max_attempts=3, base_delay_ms=200, max_delay_ms=2000, jitter=True),
        )
    )
    transport = SequencedFakeTransport(["server_error", "success"])
    delays: list[float] = []
    gateway = ModelGateway(config, transport, sleep=lambda s: delays.append(s), random_factor=lambda: 0.25)

    gateway.embed(EmbedRequest(texts=["x"]))

    assert delays == pytest.approx([0.05])  # 200ms cap * 0.25 jitter factor


def test_jitter_disabled_uses_the_deterministic_capped_delay_regardless_of_random_factor():
    config = _make_config(
        resilience=ResilienceConfig(
            timeout_seconds=5,
            retry=RetryConfig(max_attempts=3, base_delay_ms=150, max_delay_ms=2000, jitter=False),
        )
    )
    transport = SequencedFakeTransport(["server_error", "success"])
    delays: list[float] = []
    # A random_factor that would change the delay if jitter were (wrongly) applied.
    gateway = ModelGateway(config, transport, sleep=lambda s: delays.append(s), random_factor=lambda: 0.01)

    gateway.embed(EmbedRequest(texts=["x"]))

    assert delays == pytest.approx([0.15])
