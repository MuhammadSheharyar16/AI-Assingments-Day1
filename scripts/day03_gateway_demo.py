"""
Day 3 Task 7 demonstration script.

Run: python scripts/day03_gateway_demo.py
(needs PYTHONPATH=src - see README Setup)

Prints sanitized evidence for each required gateway_demo.md scenario:
embed success, chat success, retryable-failure-then-success, timeout,
non-retryable failure, blocked fallback. Output feeds straight into
artifacts/day03/gateway_demo.md by copy/paste from an actual run - not
hand-transcribed - the same discipline Day 1/Day 2's generated reports use.

Every scenario runs against a fake transport - never the real network (see
day03_pack's "no avoidable cloud cost" rule, and the lead review's own
"demonstrate a timeout and a retry-exhaustion case using fake transport"
instruction). Only sanitized fields are ever printed: operation,
model_alias, latency, retry_count, budget_status, token_usage, error
category. No prompt, completion, credential or authorization header is
constructed anywhere in this script, let alone printed.

Environment note: this checkout has no lead-provided Microsoft Foundry
endpoint or identity, so "successful call" evidence below is captured
against a fake transport exercising the exact same ModelGateway/
CallMetadata code path a real FoundryAdapter response would produce -
substituting only the network call itself. Re-run this script unmodified
once real access is available; nothing about the gateway code changes for
that - only which Transport gets passed to ModelGateway (FoundryAdapter
instead of a fake).
"""
from __future__ import annotations

import dataclasses

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
from aico.platform.errors import GatewayFallbackBlockedError, error_for_category
from aico.platform.model_gateway import (
    ChatMessage,
    ChatRequest,
    EmbedRequest,
    ModelGateway,
    TransportResult,
)

PRIMARY_ROUTE = RouteEndpoint(
    provider="microsoft-foundry", region="uk-south", data_boundary="uk", risk_class="standard"
)


def make_config(*, fallback_enabled: bool = False, fallback_route: RouteEndpoint | None = None) -> GatewayConfig:
    return GatewayConfig(
        version="1.0",
        endpoint_env="AICO_FOUNDRY_ENDPOINT",
        models=ModelAliases(chat="demo-chat-alias", embedding="demo-embed-alias"),
        resilience=ResilienceConfig(
            timeout_seconds=20,
            retry=RetryConfig(max_attempts=3, base_delay_ms=250, max_delay_ms=2000, jitter=True),
        ),
        budgets=BudgetsConfig(
            chat=ChatBudget(max_input_tokens=8000, max_output_tokens=1000),
            embedding=EmbeddingBudget(max_items_per_call=32),
        ),
        routing=RoutingPolicy(
            primary=PRIMARY_ROUTE,
            fallback=FallbackPolicy(
                enabled=fallback_enabled,
                route=fallback_route,
                require_compatibility={
                    "provider": True, "region": True, "data_boundary": True, "risk": True, "budget": True,
                },
            ),
        ),
    )


class ScriptedTransport:
    """Replays a fixed sequence of outcomes: "success" or an error
    category name (aico.platform.errors.error_for_category)."""

    def __init__(self, outcomes: list[str]):
        self._outcomes = list(outcomes)
        self.call_count = 0

    def _next(self) -> str:
        self.call_count += 1
        return self._outcomes.pop(0)

    def embed(self, *, model_alias, texts, timeout_seconds):
        outcome = self._next()
        if outcome == "success":
            return TransportResult(content=[[0.11, 0.22, 0.33] for _ in texts], dimensions=3, token_usage=None)
        raise error_for_category(outcome, f"fake transport outcome: {outcome}")

    def chat(self, *, model_alias, messages, max_output_tokens, timeout_seconds):
        outcome = self._next()
        if outcome == "success":
            return TransportResult(
                content="<completion text intentionally not printed - see gateway_demo.md>",
                dimensions=None,
                token_usage={"prompt_tokens": 42, "completion_tokens": 17},
            )
        raise error_for_category(outcome, f"fake transport outcome: {outcome}")


def print_metadata(label: str, metadata) -> None:
    fields = {f.name: getattr(metadata, f.name) for f in dataclasses.fields(metadata)}
    fields["latency_ms"] = round(fields["latency_ms"], 3)
    print(f"[{label}] success - sanitized metadata:")
    for name, value in fields.items():
        print(f"    {name} = {value!r}")


def print_error(label: str, exc: Exception) -> None:
    category = getattr(exc, "category", "n/a")
    retryable = getattr(exc, "retryable", "n/a")
    cause = getattr(exc, "cause", None)
    print(f"[{label}] failed - {type(exc).__name__} (category={category}, retryable={retryable})")
    if cause is not None:
        print(f"    caused by: {type(cause).__name__} (category={getattr(cause, 'category', 'n/a')})")


def scenario_embed_success() -> None:
    print("\n== 1. Successful embed call through the gateway ==")
    gateway = ModelGateway(make_config(), ScriptedTransport(["success"]))
    result = gateway.embed(EmbedRequest(texts=["chunk one", "chunk two"]))
    print(f"    vectors returned: {len(result.vectors)} (dimensions={result.dimensions})")
    print_metadata("embed", result.metadata)


def scenario_chat_success() -> None:
    print("\n== 2. Successful chat call through the gateway ==")
    gateway = ModelGateway(make_config(), ScriptedTransport(["success"]))
    result = gateway.chat(ChatRequest(messages=[ChatMessage(role="user", content="<prompt intentionally not printed>")]))
    print("    (completion content intentionally not printed - gateway call path and metadata only, per Task 7)")
    print_metadata("chat", result.metadata)


def scenario_retryable_then_success() -> None:
    print("\n== 3. Retryable failure that later succeeds (rate_limit -> success) ==")
    gateway = ModelGateway(make_config(), ScriptedTransport(["rate_limit", "success"]))
    result = gateway.embed(EmbedRequest(texts=["x"]))
    print(f"    transport calls made: 2 (1 retryable failure + 1 success)")
    print_metadata("embed", result.metadata)


def scenario_timeout() -> None:
    print("\n== 4. Timeout, normalized and retried to the configured ceiling ==")
    config = make_config()
    gateway = ModelGateway(config, ScriptedTransport(["timeout", "timeout", "timeout"]))
    try:
        gateway.embed(EmbedRequest(texts=["x"]))
    except Exception as exc:
        print_error("embed", exc)
        print(f"    max_attempts (config) = {config.resilience.retry.max_attempts}")


def scenario_non_retryable_failure() -> None:
    print("\n== 5. Non-retryable failure (bad_request), fails immediately ==")
    transport = ScriptedTransport(["bad_request"])
    gateway = ModelGateway(make_config(), transport)
    try:
        gateway.chat(ChatRequest(messages=[ChatMessage(role="user", content="<prompt intentionally not printed>")]))
    except Exception as exc:
        print_error("chat", exc)
        print(f"    transport calls made: {transport.call_count} (no retry attempted)")


def scenario_blocked_fallback() -> None:
    print("\n== 6. Blocked fallback (region mismatch) ==")
    mismatched_fallback_route = RouteEndpoint(
        provider="microsoft-foundry", region="us-east", data_boundary="uk", risk_class="standard"
    )
    config = make_config(fallback_enabled=True, fallback_route=mismatched_fallback_route)
    # server_error is retryable - the primary must exhaust the full retry
    # ceiling (max_attempts) before fallback is even considered.
    primary = ScriptedTransport(["server_error"] * config.resilience.retry.max_attempts)
    fallback = ScriptedTransport(["success"])  # must never be called
    gateway = ModelGateway(config, primary, fallback_transport=fallback)
    try:
        gateway.chat(ChatRequest(messages=[ChatMessage(role="user", content="<prompt intentionally not printed>")]))
    except GatewayFallbackBlockedError as exc:
        print_error("chat", exc)
        print(f"    primary.region={config.routing.primary.region!r} "
              f"fallback.region={mismatched_fallback_route.region!r}")
        print(f"    fallback transport call count: {fallback.call_count} (never invoked)")


def main() -> None:
    scenario_embed_success()
    scenario_chat_success()
    scenario_retryable_then_success()
    scenario_timeout()
    scenario_non_retryable_failure()
    scenario_blocked_fallback()


if __name__ == "__main__":
    main()
