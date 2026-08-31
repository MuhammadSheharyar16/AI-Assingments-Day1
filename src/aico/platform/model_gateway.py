"""
Model Gateway: the single typed boundary all chat and embedding traffic
crosses (Day 3). Application and retrieval code depends on the contract in
this file - `ModelGateway`, `ChatRequest`/`ChatResult`,
`EmbedRequest`/`EmbedResult` - and never on a provider SDK, never on
aico.platform.foundry_adapter directly.

Responsibilities:
- Typed request/result values for chat and embed (no provider-shaped dicts
  leak upward).
- Sanitized, reviewable metadata on every successful call: model_alias,
  token usage (where available), latency, retry count, budget status.
  Prompt/completion text is never put inside metadata.
- A cancellation/timeout seam every call goes through (`CancellationToken`,
  `timeout_seconds`).
- Bounded exponential retry with jitter (Task 3): a retryable failure
  (`ModelGatewayError.retryable`) is retried up to
  `config.resilience.retry.max_attempts` times, waiting
  `min(base_delay_ms * 2**attempt, max_delay_ms)` between attempts (full
  jitter - a random point between 0 and that cap - when
  `retry.jitter` is true). A non-retryable failure fails immediately.
  Retry always stops: at the ceiling (raising
  `GatewayRetryCeilingExceededError`) or the moment `CancellationToken` is
  set, whichever comes first - there is no infinite loop.
- Normalizing every failure into aico.platform.errors.ModelGatewayError -
  a caller of this module never needs to know what raised the underlying
  exception.
- Routing/fallback (Task 4): fallback to a second `Transport` happens ONLY
  when (a) a `fallback_transport` is actually configured, (b)
  `config.routing.fallback.enabled` is true, and (c) every axis
  `routing.fallback.require_compatibility` marks as required (provider/
  region/data_boundary/risk/budget) is actually compatible between the
  primary and fallback routes. Any missing condition raises
  `GatewayFallbackBlockedError` (chaining the primary failure) instead of
  silently trying a different provider/region/data boundary - never
  cancellation, which always propagates as itself. A successful fallback
  call is marked `used_fallback=True` in its metadata, so the caller's
  result is always explainable, never a silent switch.
- Logging (Task 5): every call this module makes logs exactly one
  structured line via the `aico.platform.model_gateway` logger - success,
  a retry, hitting the retry ceiling, a non-retryable failure, an
  unnormalized failure, or a blocked/attempted fallback - built only from
  already-sanitized fields (operation, model_alias, category, attempt/
  retry counts, latency, budget/fallback status). No log call anywhere in
  this file is ever given request texts/messages, response content, or an
  exception's free-text message - so a prompt, a completion, a header or a
  secret can never end up in a log line by construction, not by
  discipline. Nothing here configures handlers/level/output - that is the
  application's responsibility; this module only ever calls
  `logger.info`/`.warning`/`.error`.

This module talks to a `Transport` (a small protocol - `embed`/`chat` in,
`TransportResult` out) rather than to Foundry directly. `FoundryAdapter`
(foundry_adapter.py) is the one real implementation; tests inject a fake
transport instead so gateway behavior is provable without any network
call. `ModelGateway.from_config()` is the only place in this file that
imports the real adapter, and it does so lazily so importing
model_gateway.py never requires config/model-routing.yaml or the Foundry
endpoint to exist.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from aico.platform.config import GatewayConfig, RetryConfig, RouteEndpoint, load_gateway_config
from aico.platform.errors import (
    GatewayCancelledError,
    GatewayFallbackBlockedError,
    GatewayRetryCeilingExceededError,
    ModelGatewayError,
)

# Structured, sanitized operational logging only - see module docstring.
# Every call below passes literal field names/values built from typed,
# already-sanitized data (operation, model_alias, category, counters) -
# never a request/response object, never str(exc). The application wires
# handlers/level; this module never does (no basicConfig, no handler
# attached here).
logger = logging.getLogger(__name__)


class CancellationToken:
    """Cooperative cancellation signal. A caller holds the token, passes it
    into a request, and calls `.cancel()` (from another thread, or on its
    own deadline) to ask an in-flight or not-yet-started call to stop
    instead of running to completion. The gateway checks it before
    dispatching a call and, in the bounded-retry loop, before every retry
    attempt (including while waiting out the backoff delay, in effect,
    since the check happens the moment that wait returns) - it cannot
    interrupt a single HTTP call already in flight against a transport
    that does not support that itself, but it does stop the operation
    from retrying or from ever starting."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


# ── Typed request/result contract ───────────────────────────────────────

@dataclass(frozen=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class ChatRequest:
    messages: list[ChatMessage]
    model_alias: str | None = None  # defaults to config.models.chat
    max_output_tokens: int | None = None
    timeout_seconds: float | None = None  # defaults to config.resilience.timeout_seconds
    cancellation: CancellationToken | None = None


@dataclass(frozen=True)
class EmbedRequest:
    texts: list[str]
    model_alias: str | None = None  # defaults to config.models.embedding
    timeout_seconds: float | None = None
    cancellation: CancellationToken | None = None


@dataclass(frozen=True)
class CallMetadata:
    """Sanitized, reviewable facts about one gateway call. Never contains
    prompt or completion text - see module docstring."""

    operation: str  # "chat" | "embed"
    model_alias: str
    latency_ms: float
    retry_count: int
    token_usage: dict[str, int] | None
    budget_status: str  # "within_budget" | "exceeded" | "unknown"
    used_fallback: bool = False  # true only when the primary route failed and a policy-approved fallback served the call


@dataclass(frozen=True)
class ChatResult:
    content: str
    metadata: CallMetadata


@dataclass(frozen=True)
class EmbedResult:
    vectors: list[list[float]]
    dimensions: int
    metadata: CallMetadata


# ── Transport seam (what an adapter/fake must provide) ──────────────────

@dataclass(frozen=True)
class TransportResult:
    """What a Transport hands back before sanitized metadata is built
    around it. `content` is a list of vectors for embed, a completion
    string for chat - never wrapped in provider-specific response shape."""

    content: object
    dimensions: int | None = None
    token_usage: dict[str, int] | None = None


class Transport(Protocol):
    """Everything the gateway needs from a transport. Satisfied by
    FoundryAdapter (real) and by any fake a test constructs - nothing here
    is provider-SDK-shaped. A transport is expected to raise
    aico.platform.errors.ModelGatewayError subclasses for normalized
    failures; the gateway wraps anything else as a last resort so a raw
    exception never reaches a caller."""

    def embed(self, *, model_alias: str, texts: list[str], timeout_seconds: float) -> TransportResult: ...

    def chat(
        self,
        *,
        model_alias: str,
        messages: list[dict],
        max_output_tokens: int | None,
        timeout_seconds: float,
    ) -> TransportResult: ...


@dataclass(frozen=True)
class FallbackCompatibility:
    """The result of checking a configured fallback route against
    `routing.fallback.require_compatibility` - one bool per axis Task 4
    requires (provider/region/data_boundary/risk/budget). An axis not
    marked required in config is always reported compatible (there was
    nothing to check)."""

    provider_compatible: bool
    region_compatible: bool
    data_boundary_compatible: bool
    risk_compatible: bool
    budget_compatible: bool

    @property
    def blocked_axes(self) -> list[str]:
        return [
            name
            for name, ok in (
                ("provider", self.provider_compatible),
                ("region", self.region_compatible),
                ("data_boundary", self.data_boundary_compatible),
                ("risk", self.risk_compatible),
                ("budget", self.budget_compatible),
            )
            if not ok
        ]

    @property
    def all_compatible(self) -> bool:
        return not self.blocked_axes


class ModelGateway:
    """Typed chat/embed boundary. Construct once (directly with a config +
    transport, or via `from_config()` for the real Foundry path) and call
    `.chat()` / `.embed()` - never the transport, never a provider SDK."""

    def __init__(
        self,
        config: GatewayConfig,
        transport: Transport,
        *,
        fallback_transport: Transport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        random_factor: Callable[[], float] = random.random,
    ):
        self._config = config
        self._transport = transport
        # No fallback happens at all unless this is actually set - policy
        # (config.routing.fallback.enabled) only controls whether an
        # already-configured fallback path may be *used*, it never
        # conjures one up. Real deployments: Task 2/setup wires this the
        # same way `transport` itself is wired (a second FoundryAdapter
        # pointed at the approved fallback resource); tests inject a fake.
        self._fallback_transport = fallback_transport
        self._clock = clock
        # Injectable so tests can prove backoff/jitter math and retry
        # sequencing without an actual test run taking as long as the real
        # delays would, and without depending on real randomness.
        self._sleep = sleep
        self._random_factor = random_factor

    @classmethod
    def from_config(cls, path: str | None = None) -> "ModelGateway":
        # Local import: this is the only path through this file that
        # touches the real adapter (and therefore, transitively, the HTTP
        # client) - constructing a ModelGateway for tests via __init__
        # directly never imports it.
        from aico.platform.foundry_adapter import FoundryAdapter

        config = load_gateway_config(path) if path is not None else load_gateway_config()
        # No fallback endpoint/deployment is part of config/model-routing.yaml
        # today (routing.fallback only describes compatibility metadata, not
        # a second connection target) - so there is nothing to build a real
        # fallback FoundryAdapter from yet. That means routing.fallback.enabled
        # in the real path currently has no fallback_transport to use even
        # when true; see ADR-003. Tests exercise fallback via __init__ directly.
        return cls(config, FoundryAdapter(config))

    @property
    def config(self) -> GatewayConfig:
        return self._config

    def embed(self, request: EmbedRequest) -> EmbedResult:
        model_alias = request.model_alias or self._config.models.embedding
        timeout_seconds = request.timeout_seconds or self._config.resilience.timeout_seconds
        budget_compatible = len(request.texts) <= self._config.budgets.embedding.max_items_per_call

        start = self._clock()
        result, retry_count, used_fallback = self._call_with_fallback(
            operation="embed",
            model_alias=model_alias,
            cancellation=request.cancellation,
            budget_compatible=budget_compatible,
            primary_call=lambda: self._transport.embed(
                model_alias=model_alias, texts=request.texts, timeout_seconds=timeout_seconds
            ),
            fallback_call=lambda: self._fallback_transport.embed(
                model_alias=model_alias, texts=request.texts, timeout_seconds=timeout_seconds
            ),
        )
        latency_ms = (self._clock() - start) * 1000

        dimensions = result.dimensions if result.dimensions is not None else 0
        metadata = CallMetadata(
            operation="embed",
            model_alias=model_alias,
            latency_ms=latency_ms,
            retry_count=retry_count,
            token_usage=result.token_usage,
            budget_status=self._embed_budget_status(len(request.texts)),
            used_fallback=used_fallback,
        )
        self._log_success(metadata)
        return EmbedResult(vectors=result.content, dimensions=dimensions, metadata=metadata)

    def chat(self, request: ChatRequest) -> ChatResult:
        model_alias = request.model_alias or self._config.models.chat
        timeout_seconds = request.timeout_seconds or self._config.resilience.timeout_seconds
        max_output_tokens = request.max_output_tokens
        budget_compatible = (
            max_output_tokens is None or max_output_tokens <= self._config.budgets.chat.max_output_tokens
        )

        messages = [{"role": m.role, "content": m.content} for m in request.messages]

        start = self._clock()
        result, retry_count, used_fallback = self._call_with_fallback(
            operation="chat",
            model_alias=model_alias,
            cancellation=request.cancellation,
            budget_compatible=budget_compatible,
            primary_call=lambda: self._transport.chat(
                model_alias=model_alias,
                messages=messages,
                max_output_tokens=max_output_tokens,
                timeout_seconds=timeout_seconds,
            ),
            fallback_call=lambda: self._fallback_transport.chat(
                model_alias=model_alias,
                messages=messages,
                max_output_tokens=max_output_tokens,
                timeout_seconds=timeout_seconds,
            ),
        )
        latency_ms = (self._clock() - start) * 1000

        metadata = CallMetadata(
            operation="chat",
            model_alias=model_alias,
            latency_ms=latency_ms,
            retry_count=retry_count,
            token_usage=result.token_usage,
            budget_status=self._chat_budget_status(result.token_usage),
            used_fallback=used_fallback,
        )
        self._log_success(metadata)
        return ChatResult(content=result.content, metadata=metadata)

    # ── internals ────────────────────────────────────────────────────

    def _call_with_fallback(
        self,
        *,
        operation: str,
        model_alias: str,
        cancellation: CancellationToken | None,
        budget_compatible: bool,
        primary_call: Callable[[], TransportResult],
        fallback_call: Callable[[], TransportResult],
    ) -> tuple[TransportResult, int, bool]:
        """Run the primary route (with its own bounded retry). On failure,
        fall back ONLY when a fallback transport is configured, policy has
        fallback enabled, and every required compatibility axis passes -
        see FallbackCompatibility / GatewayFallbackBlockedError. Never
        falls back for a cancellation - that always propagates as itself."""
        try:
            result, retry_count = self._call_with_retry(primary_call, cancellation, operation, model_alias)
            return result, retry_count, False
        except GatewayCancelledError:
            raise
        except ModelGatewayError as primary_error:
            if self._fallback_transport is None:
                raise  # no fallback path configured - nothing to fall back to

            policy = self._config.routing.fallback
            if not policy.enabled:
                logger.warning(
                    "gateway.fallback_blocked operation=%s model_alias=%s reason=policy_disabled",
                    operation, model_alias,
                )
                raise GatewayFallbackBlockedError(
                    f"{operation}: fallback is disabled by policy (routing.fallback.enabled=false)",
                    cause=primary_error,
                ) from primary_error

            compatibility = self._evaluate_fallback_compatibility(budget_compatible=budget_compatible)
            if not compatibility.all_compatible:
                logger.warning(
                    "gateway.fallback_blocked operation=%s model_alias=%s blocked_axes=%s",
                    operation, model_alias, ",".join(compatibility.blocked_axes),
                )
                raise GatewayFallbackBlockedError(
                    f"{operation}: fallback blocked by policy - incompatible on: "
                    f"{', '.join(compatibility.blocked_axes)}",
                    cause=primary_error,
                ) from primary_error

            logger.info(
                "gateway.fallback_attempt operation=%s model_alias=%s primary_failure_category=%s",
                operation, model_alias, primary_error.category,
            )
            result, retry_count = self._call_with_retry(fallback_call, cancellation, operation, model_alias)
            return result, retry_count, True

    def _evaluate_fallback_compatibility(self, *, budget_compatible: bool) -> FallbackCompatibility:
        policy = self._config.routing.fallback
        primary = self._config.routing.primary
        route: RouteEndpoint | None = policy.route
        require = policy.require_compatibility

        def axis_ok(axis: str, matches_primary: bool) -> bool:
            # An axis config doesn't mark required is never a reason to
            # block - only axes routing.fallback.require_compatibility
            # actually names are checked, per Task 4.
            return (not require.get(axis, True)) or matches_primary

        return FallbackCompatibility(
            provider_compatible=axis_ok("provider", route is not None and route.provider == primary.provider),
            region_compatible=axis_ok("region", route is not None and route.region == primary.region),
            data_boundary_compatible=axis_ok(
                "data_boundary", route is not None and route.data_boundary == primary.data_boundary
            ),
            risk_compatible=axis_ok("risk", route is not None and route.risk_class == primary.risk_class),
            budget_compatible=axis_ok("budget", budget_compatible),
        )

    def _check_cancellation(self, token: CancellationToken | None, operation: str) -> None:
        if token is not None and token.is_cancelled():
            raise GatewayCancelledError(f"{operation} was cancelled")

    def _call_with_retry(
        self,
        call: Callable[[], TransportResult],
        cancellation: CancellationToken | None,
        operation: str,
        model_alias: str,
    ) -> tuple[TransportResult, int]:
        """Run `call()`, retrying a retryable ModelGatewayError with bounded
        exponential backoff and (optionally) jitter, up to
        `config.resilience.retry.max_attempts` attempts total. Returns the
        successful TransportResult and how many retries it took (0 on a
        first-try success). A non-retryable error, an unnormalized
        exception, cancellation, or exhausting the attempt ceiling all end
        the loop - it never runs unbounded."""
        retry_cfg = self._config.resilience.retry
        attempt = 0  # number of retries already taken (0 == first attempt in flight)

        while True:
            self._check_cancellation(cancellation, operation)
            try:
                return call(), attempt
            except ModelGatewayError as exc:
                if not exc.retryable:
                    logger.warning(
                        "gateway.call_failed operation=%s model_alias=%s category=%s retryable=False",
                        operation, model_alias, exc.category,
                    )
                    raise
                if attempt + 1 >= retry_cfg.max_attempts:
                    logger.warning(
                        "gateway.retry_ceiling_exceeded operation=%s model_alias=%s category=%s max_attempts=%d",
                        operation, model_alias, exc.category, retry_cfg.max_attempts,
                    )
                    raise GatewayRetryCeilingExceededError(
                        f"{operation} did not succeed within {retry_cfg.max_attempts} attempt(s) "
                        f"(last failure category: {exc.category})",
                        cause=exc,
                    ) from exc
                delay_seconds = self._backoff_delay_seconds(attempt, retry_cfg)
                logger.info(
                    "gateway.retry operation=%s model_alias=%s category=%s attempt=%d delay_ms=%.0f",
                    operation, model_alias, exc.category, attempt + 1, delay_seconds * 1000,
                )
                self._sleep(delay_seconds)
                attempt += 1
            except Exception as exc:  # last-resort seam: a transport is expected to normalize
                # Not retried - an un-normalized exception means the
                # transport itself has a bug, not a known-transient
                # provider failure, so blindly retrying it would just
                # repeat whatever went wrong.
                logger.error(
                    "gateway.unnormalized_failure operation=%s model_alias=%s exception_type=%s",
                    operation, model_alias, exc.__class__.__name__,
                )
                raise ModelGatewayError(
                    f"unnormalized transport failure: {exc.__class__.__name__}: {exc}", cause=exc
                ) from exc

    def _backoff_delay_seconds(self, attempt: int, retry_cfg: RetryConfig) -> float:
        """attempt is 0-indexed (0 == delay before the 2nd overall try).
        Capped exponential backoff; full jitter (uniform between 0 and the
        cap) when retry_cfg.jitter is set, so many concurrent callers
        retrying the same failure don't all wake up at the same instant."""
        capped_ms = min(retry_cfg.base_delay_ms * (2 ** attempt), retry_cfg.max_delay_ms)
        delay_ms = capped_ms * self._random_factor() if retry_cfg.jitter else capped_ms
        return delay_ms / 1000

    def _log_success(self, metadata: CallMetadata) -> None:
        # Built entirely from CallMetadata's own already-sanitized fields -
        # never the request texts/messages or the result content, which
        # this method never even receives.
        logger.info(
            "gateway.call_succeeded operation=%s model_alias=%s latency_ms=%.1f retry_count=%d "
            "budget_status=%s used_fallback=%s token_usage=%s",
            metadata.operation, metadata.model_alias, metadata.latency_ms, metadata.retry_count,
            metadata.budget_status, metadata.used_fallback, metadata.token_usage,
        )

    def _embed_budget_status(self, item_count: int) -> str:
        limit = self._config.budgets.embedding.max_items_per_call
        return "exceeded" if item_count > limit else "within_budget"

    def _chat_budget_status(self, usage: dict[str, int] | None) -> str:
        if not usage:
            return "unknown"
        budget = self._config.budgets.chat
        input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
        output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
        if input_tokens is not None and input_tokens > budget.max_input_tokens:
            return "exceeded"
        if output_tokens is not None and output_tokens > budget.max_output_tokens:
            return "exceeded"
        return "within_budget"
