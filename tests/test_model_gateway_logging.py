"""
Task 5 — sanitized metadata and logging.

Sanitized metadata itself (model alias, token usage, latency, retry count,
budget status) has been part of every successful CallMetadata since Task 1
- see test_model_gateway.py. This file proves the *logging* half: the
gateway logs one structured, sanitized line per call/retry/failure event,
and a prompt, a completion, an authorization header or a secret can never
end up in a log line - proven behaviorally with pytest's `caplog`, not just
asserted by comment, per the assignment's "tests should inspect log output
where practical."
"""
from __future__ import annotations

import logging
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
from aico.platform.errors import GatewayBadRequestError, error_for_category
from aico.platform.model_gateway import (
    ChatMessage,
    ChatRequest,
    EmbedRequest,
    ModelGateway,
    TransportResult,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
LOGGER_NAME = "aico.platform.model_gateway"

# Distinctive markers so a substring match in caplog.text is unambiguous -
# these strings only ever appear if the gateway logged raw request/response
# content, which it must never do.
SECRET_PROMPT = "TOP-SECRET-PROMPT-9f3c1a2b"
SECRET_COMPLETION = "TOP-SECRET-COMPLETION-77b2e0d4"
SECRET_BEARER_TOKEN = "Bearer sk-live-should-never-appear-in-a-log-4f9d"


def _make_config(**overrides) -> GatewayConfig:
    defaults = dict(
        version="1.0",
        endpoint_env="AICO_TEST_FOUNDRY_ENDPOINT",
        models=ModelAliases(chat="test-chat-alias", embedding="test-embed-alias"),
        resilience=ResilienceConfig(
            timeout_seconds=5,
            retry=RetryConfig(max_attempts=3, base_delay_ms=10, max_delay_ms=100, jitter=False),
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


class ScriptedTransport:
    """Replays outcomes ("success" or an error category name) one per
    call, and returns a chat completion carrying SECRET_COMPLETION - so any
    test here can prove that value never reaches a log line."""

    def __init__(self, outcomes: list[str]):
        self._outcomes = list(outcomes)
        self.call_count = 0

    def _next(self) -> str:
        self.call_count += 1
        return self._outcomes.pop(0)

    def embed(self, *, model_alias, texts, timeout_seconds):
        outcome = self._next()
        if outcome == "success":
            return TransportResult(content=[[0.1, 0.2]], dimensions=2, token_usage=None)
        raise error_for_category(outcome, f"fake transport outcome: {outcome}")

    def chat(self, *, model_alias, messages, max_output_tokens, timeout_seconds):
        outcome = self._next()
        if outcome == "success":
            return TransportResult(
                content=SECRET_COMPLETION, dimensions=None,
                token_usage={"prompt_tokens": 3, "completion_tokens": 4},
            )
        raise error_for_category(outcome, f"fake transport outcome: {outcome}")


@pytest.fixture(autouse=True)
def _capture_gateway_logger(caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)


# ── Successful call: sanitized fields present, content absent ──────────────

def test_successful_chat_call_logs_sanitized_fields_and_never_the_content(caplog):
    transport = ScriptedTransport(["success"])
    gateway = ModelGateway(_make_config(), transport, sleep=lambda s: None)

    gateway.chat(ChatRequest(messages=[ChatMessage(role="user", content=SECRET_PROMPT)]))

    assert SECRET_PROMPT not in caplog.text
    assert SECRET_COMPLETION not in caplog.text
    assert "call_succeeded" in caplog.text
    assert "test-chat-alias" in caplog.text
    assert "retry_count=0" in caplog.text


def test_successful_embed_call_never_logs_the_input_texts(caplog):
    transport = ScriptedTransport(["success"])
    gateway = ModelGateway(_make_config(), transport, sleep=lambda s: None)

    gateway.embed(EmbedRequest(texts=[SECRET_PROMPT, "another secret chunk of text"]))

    assert SECRET_PROMPT not in caplog.text
    assert "another secret chunk of text" not in caplog.text
    assert "call_succeeded" in caplog.text
    assert "test-embed-alias" in caplog.text


# ── Retry logging: category visible, content never present ────────────────

def test_retry_logging_shows_category_and_attempt_but_never_request_content(caplog):
    transport = ScriptedTransport(["rate_limit", "success"])
    gateway = ModelGateway(_make_config(), transport, sleep=lambda s: None, random_factor=lambda: 1.0)

    gateway.chat(ChatRequest(messages=[ChatMessage(role="user", content=SECRET_PROMPT)]))

    assert SECRET_PROMPT not in caplog.text
    assert "gateway.retry" in caplog.text
    assert "category=rate_limit" in caplog.text
    assert "attempt=1" in caplog.text


def test_retry_ceiling_exceeded_logging_content(caplog):
    transport = ScriptedTransport(["server_error", "server_error", "server_error"])
    gateway = ModelGateway(_make_config(), transport, sleep=lambda s: None)
    from aico.platform.errors import GatewayRetryCeilingExceededError

    with pytest.raises(GatewayRetryCeilingExceededError):
        gateway.chat(ChatRequest(messages=[ChatMessage(role="user", content=SECRET_PROMPT)]))

    assert SECRET_PROMPT not in caplog.text
    assert "gateway.retry_ceiling_exceeded" in caplog.text
    assert "category=server_error" in caplog.text


# ── Non-retryable failure logging ───────────────────────────────────────

def test_non_retryable_failure_logging_never_leaks_request_content(caplog):
    transport = ScriptedTransport(["bad_request"])
    gateway = ModelGateway(_make_config(), transport, sleep=lambda s: None)

    with pytest.raises(GatewayBadRequestError):
        gateway.chat(ChatRequest(messages=[ChatMessage(role="user", content=SECRET_PROMPT)]))

    assert SECRET_PROMPT not in caplog.text
    assert "gateway.call_failed" in caplog.text
    assert "category=bad_request" in caplog.text
    assert "retryable=False" in caplog.text


# ── Unnormalized exception logging ──────────────────────────────────────

def test_unnormalized_exception_logging_never_leaks_request_content(caplog):
    class BuggyTransport:
        def chat(self, **kwargs):
            raise ValueError(f"leaked content would be bad here: {SECRET_PROMPT}")

        def embed(self, **kwargs):
            raise ValueError("unused")

    gateway = ModelGateway(_make_config(), BuggyTransport(), sleep=lambda s: None)

    from aico.platform.errors import ModelGatewayError

    with pytest.raises(ModelGatewayError):
        gateway.chat(ChatRequest(messages=[ChatMessage(role="user", content=SECRET_PROMPT)]))

    # Even though the *exception itself* happens to contain the secret in
    # this adversarial test, the log line must not - the gateway never logs
    # str(exc), only the exception's class name.
    assert SECRET_PROMPT not in caplog.text
    assert "gateway.unnormalized_failure" in caplog.text
    assert "exception_type=ValueError" in caplog.text


# ── Fallback logging ─────────────────────────────────────────────────────

def test_fallback_blocked_logging_never_leaks_content(caplog):
    class FailingTransport:
        def chat(self, **kwargs):
            raise error_for_category("server_error", "primary failed")

        def embed(self, **kwargs):
            raise error_for_category("server_error", "primary failed")

    class NeverCalledFallback:
        def chat(self, **kwargs):
            raise AssertionError("fallback must not be called when policy blocks it")

        def embed(self, **kwargs):
            raise AssertionError("fallback must not be called when policy blocks it")

    config = _make_config()  # fallback.enabled is False
    gateway = ModelGateway(
        config, FailingTransport(), fallback_transport=NeverCalledFallback(), sleep=lambda s: None
    )
    from aico.platform.errors import GatewayFallbackBlockedError

    with pytest.raises(GatewayFallbackBlockedError):
        gateway.chat(ChatRequest(messages=[ChatMessage(role="user", content=SECRET_PROMPT)]))

    assert SECRET_PROMPT not in caplog.text
    assert "gateway.fallback_blocked" in caplog.text
    assert "reason=policy_disabled" in caplog.text


def test_fallback_attempt_logging_never_leaks_content(caplog):
    class FailingTransport:
        def chat(self, **kwargs):
            raise error_for_category("server_error", "primary failed")

        def embed(self, **kwargs):
            raise error_for_category("server_error", "primary failed")

    class SucceedingFallback:
        def chat(self, **kwargs):
            return TransportResult(content=SECRET_COMPLETION, dimensions=None, token_usage=None)

        def embed(self, **kwargs):
            return TransportResult(content=[[0.0]], dimensions=1, token_usage=None)

    same_route = RouteEndpoint(
        provider="microsoft-foundry", region="uk-south", data_boundary="uk", risk_class="standard"
    )
    config = _make_config(
        routing=RoutingPolicy(
            primary=same_route,
            fallback=FallbackPolicy(
                enabled=True, route=same_route,
                require_compatibility={
                    "provider": True, "region": True, "data_boundary": True, "risk": True, "budget": True,
                },
            ),
        )
    )
    gateway = ModelGateway(
        config, FailingTransport(), fallback_transport=SucceedingFallback(), sleep=lambda s: None
    )

    gateway.chat(ChatRequest(messages=[ChatMessage(role="user", content=SECRET_PROMPT)]))

    assert SECRET_PROMPT not in caplog.text
    assert SECRET_COMPLETION not in caplog.text
    assert "gateway.fallback_attempt" in caplog.text
    assert "used_fallback=True" in caplog.text


# ── A credential/authorization header is never a possibility to log ────────

def test_foundry_adapter_never_logs_anything():
    # foundry_adapter.py is the only file that ever builds an Authorization
    # header/bearer token - simplest guarantee that it can't end up in a
    # log line is that this file has no logging call in it at all.
    text = (REPO_ROOT / "src" / "aico" / "platform" / "foundry_adapter.py").read_text(encoding="utf-8")
    assert "logging" not in text.lower()
    assert "logger" not in text.lower()


def test_model_gateway_logger_calls_never_reference_request_or_response_content():
    text = (REPO_ROOT / "src" / "aico" / "platform" / "model_gateway.py").read_text(encoding="utf-8")
    # None of this file's logger call arguments contain a literal '(' or
    # ')' of their own, so matching up to the first unescaped ')' reliably
    # captures one full (possibly multiline) call.
    logger_calls = re.findall(r"logger\.\w+\([^)]*\)", text, re.DOTALL)
    assert len(logger_calls) >= 6, "expected multiple logger calls in model_gateway.py"

    forbidden = ["request.texts", "request.messages", "result.content", "str(exc)"]
    for call in logger_calls:
        for pattern in forbidden:
            assert pattern not in call, f"logger call appears to reference raw content: {call!r}"
