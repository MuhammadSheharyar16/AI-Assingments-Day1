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
  `timeout_seconds`) - including while a call is actually in flight
  against the transport, not only in the gaps before dispatch and between
  retries: `_dispatch_with_cancellation` runs the transport call on a
  background thread and polls the token, so `.cancel()` unblocks the
  caller within one `cancellation_poll_interval_seconds` tick instead of
  making it wait out the whole call. See `CancellationToken`'s docstring
  for exactly what that does and doesn't guarantee.
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
  when (a) the primary failure is `GatewayRetryCeilingExceededError` -
  i.e. a *retryable* category (timeout/rate_limit/server_error) that
  exhausted its own retry ceiling; a non-retryable primary failure
  (authentication/bad_request/an unnormalized transport bug) is never a
  fallback candidate at all, because the credential or the request is
  wrong, not the route, and no policy check can fix that, (b) a
  `fallback_transport` is actually configured, (c)
  `config.routing.fallback.enabled` is true, and (d) every axis
  `routing.fallback.require_compatibility` marks as required (provider/
  region/data_boundary/risk/budget) is actually compatible between the
  primary and fallback routes - unconditionally: every axis is mandatory,
  there is no config-driven way to relax one (see `_evaluate_fallback_
  compatibility`; `config.routing.fallback.require_compatibility` is
  validated at load time to be all-true - see aico.platform.config).
  Any missing condition raises
  `GatewayFallbackBlockedError` (chaining the primary failure) instead of
  silently trying a different provider/region/data boundary - never
  cancellation, which always propagates as itself. A successful fallback
  call is marked `used_fallback=True` in its metadata, and its
  `retry_count` folds in the attempts the primary already spent (via
  `GatewayRetryCeilingExceededError.attempts_made`) plus whatever the
  fallback leg itself needed - so the caller's result is always
  explainable and its retry accounting is never an undercount.
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
`TransportResult` out) rather than to any one provider directly.
`FoundryAdapter` (foundry_adapter.py) is the one real implementation today;
tests inject a fake transport instead so gateway behavior is provable
without any network call. `ModelGateway.from_config()` picks which real
adapter to build by looking `config.routing.primary.provider` up in the
`_PROVIDER_TRANSPORTS` registry below it - the only place in this file that
imports a real adapter, and it does so lazily (one lazy import per
provider, only the selected one ever runs) so importing model_gateway.py
never requires config/model-routing.yaml, a provider SDK, or any provider
endpoint to exist. Because that dispatch is config-driven, adding a second
provider's adapter module and registering it here makes every later switch
between registered providers a single config/model-routing.yaml edit - see
the registry's docstring for how to add one.
"""
from __future__ import annotations

import logging
import queue
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from aico.platform.config import GatewayConfig, RetryConfig, RouteEndpoint, load_gateway_config
from aico.platform.errors import (
    GatewayCancelledError,
    GatewayConfigurationError,
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
    instead of running to completion. The gateway checks it:
    - before dispatching a call at all,
    - in the bounded-retry loop, before every retry attempt (including
      while waiting out the backoff delay, in effect, since the check
      happens the moment that wait returns), and
    - while a call is actually in flight against the transport
      (`ModelGateway._dispatch_with_cancellation`): `.cancel()` there
      unblocks the caller within one `cancellation_poll_interval_seconds`
      tick, rather than making it wait for the transport call to finish
      on its own.

    What it still cannot do: force the transport call itself to stop.
    Python has no safe way to abort an arbitrary blocking call running on
    another thread, so an in-flight call that gets cancelled keeps running
    to completion in the background and its result is discarded - exactly
    like a caller abandoning an HTTP request whose response it no longer
    wants. What's guaranteed is that the *caller* is never left waiting on
    it."""

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


# ── Provider registry (what makes a provider switch a one-file change) ──
#
# `from_config()` never hardcodes which real Transport to build. It reads
# `config.routing.primary.provider` (config/model-routing.yaml) and looks
# it up here. Every entry is a zero-arg-of-its-own factory that does its own
# lazy import - constructing a ModelGateway for tests via __init__ directly
# never imports any of these, and importing this module never requires a
# provider SDK/HTTP client to be installed.
#
# To add support for a new provider: write one adapter module implementing
# Transport (embed/chat in, TransportResult out - see FoundryAdapter) and
# add one line here. After that, switching which provider a deployment
# actually calls is exactly one file: change `routing.primary.provider` in
# config/model-routing.yaml to a key already registered below. No Python
# file needs to change for that switch.
def _foundry_transport(config: GatewayConfig) -> Transport:
    from aico.platform.foundry_adapter import FoundryAdapter

    return FoundryAdapter(config)


_PROVIDER_TRANSPORTS: dict[str, Callable[[GatewayConfig], Transport]] = {
    "microsoft-foundry": _foundry_transport,
}


def _build_transport(config: GatewayConfig) -> Transport:
    provider = config.routing.primary.provider
    factory = _PROVIDER_TRANSPORTS.get(provider)
    if factory is None:
        raise GatewayConfigurationError(
            f"routing.primary.provider={provider!r} (config/model-routing.yaml) has no "
            f"registered transport - known providers: {', '.join(sorted(_PROVIDER_TRANSPORTS))}"
        )
    return factory(config)


class ModelGateway:
    """Typed chat/embed boundary. Construct once (directly with a config +
    transport, or via `from_config()` for the real provider path - see the
    provider registry above) and call `.chat()` / `.embed()` - never the
    transport, never a provider SDK."""

    def __init__(
        self,
        config: GatewayConfig,
        transport: Transport,
        *,
        fallback_transport: Transport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        random_factor: Callable[[], float] = random.random,
        cancellation_poll_interval_seconds: float = 0.02,
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
        # How often _dispatch_with_cancellation re-checks the token while
        # a call is in flight - small enough that cancellation feels
        # immediate, injectable so tests don't have to wait a full tick.
        self._cancellation_poll_interval_seconds = cancellation_poll_interval_seconds

    @classmethod
    def from_config(cls, path: str | None = None) -> ModelGateway:
        config = load_gateway_config(path) if path is not None else load_gateway_config()
        # No fallback endpoint/deployment is part of config/model-routing.yaml
        # today (routing.fallback only describes compatibility metadata, not
        # a second connection target) - so there is nothing to build a real
        # fallback transport from yet. That means routing.fallback.enabled
        # in the real path currently has no fallback_transport to use even
        # when true; see ADR-003. Tests exercise fallback via __init__ directly.
        return cls(config, _build_transport(config))

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

            if not isinstance(primary_error, GatewayRetryCeilingExceededError):
                # The primary failed with a non-retryable category (auth,
                # bad request, or an unnormalized transport bug) - that
                # means the credential or the request itself is wrong,
                # never the route, so switching routes cannot fix it.
                # Fallback is never even considered for this, regardless
                # of policy - only a retryable failure that exhausted its
                # own retry ceiling (GatewayRetryCeilingExceededError) is
                # ever a fallback candidate. See Task 4 / ADR-003.
                logger.warning(
                    "gateway.fallback_not_applicable operation=%s model_alias=%s "
                    "reason=primary_failure_not_retryable category=%s",
                    operation, model_alias, primary_error.category,
                )
                raise

            # attempts actually spent on the primary before giving up on
            # it - folded into the final retry_count below so a caller
            # that only sees the fallback leg's own count never
            # under-reports how many transport calls the operation took.
            primary_attempts = primary_error.attempts_made or 0

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
            result, fallback_retry_count = self._call_with_retry(fallback_call, cancellation, operation, model_alias)
            return result, primary_attempts + fallback_retry_count, True

    def _evaluate_fallback_compatibility(self, *, budget_compatible: bool) -> FallbackCompatibility:
        # All five axes are unconditionally mandatory (Task 4 / ADR-003) -
        # routing.fallback.require_compatibility is validated at config
        # load time to be all-true (see aico.platform.config) and is
        # never consulted here: even a GatewayConfig built directly in
        # Python (bypassing that YAML-level validation, as most tests do)
        # can never relax a compatibility axis. A mismatch on any axis
        # always blocks fallback - there is no config-driven way around
        # that.
        policy = self._config.routing.fallback
        primary = self._config.routing.primary
        route: RouteEndpoint | None = policy.route

        return FallbackCompatibility(
            provider_compatible=route is not None and route.provider == primary.provider,
            region_compatible=route is not None and route.region == primary.region,
            data_boundary_compatible=route is not None and route.data_boundary == primary.data_boundary,
            risk_compatible=route is not None and route.risk_class == primary.risk_class,
            budget_compatible=budget_compatible,
        )

    def _check_cancellation(self, token: CancellationToken | None, operation: str) -> None:
        if token is not None and token.is_cancelled():
            raise GatewayCancelledError(f"{operation} was cancelled")

    def _dispatch_with_cancellation(
        self,
        call: Callable[[], TransportResult],
        cancellation: CancellationToken | None,
        operation: str,
    ) -> TransportResult:
        """Run `call()`, and - only when a CancellationToken is actually
        attached - make waiting for it interruptible: `.cancel()` reaching
        in *while the call is already in flight* now unblocks the caller
        within one `_cancellation_poll_interval_seconds` tick, instead of
        only being noticed before the next attempt. No token, no thread
        hop: the overwhelming majority of calls (nothing passes
        `cancellation=`) go straight through `call()` with zero added
        overhead or timing change.

        How: `call()` runs on a daemon background thread; this method
        polls the token and the thread's result queue in a loop. Python
        has no safe way to forcibly abort an arbitrary blocking call
        running on another thread, so cancelling does not stop the
        transport call itself - it keeps running to completion in the
        background (harmlessly, since it's a daemon thread - it is never
        a reason the interpreter hangs on exit) and its result is
        discarded, exactly like a caller abandoning an HTTP request whose
        response it no longer wants. What IS guaranteed: the *caller* is
        unblocked the moment cancellation fires, not after the call
        finishes on its own."""
        if cancellation is None:
            return call()

        outcome: queue.Queue = queue.Queue(maxsize=1)

        def _run() -> None:
            try:
                outcome.put(("result", call()))
            except BaseException as exc:  # forwarded to the waiting thread as-is
                outcome.put(("error", exc))

        threading.Thread(target=_run, daemon=True, name=f"gateway-{operation}-call").start()

        while True:
            if cancellation.is_cancelled():
                raise GatewayCancelledError(f"{operation} was cancelled while in flight")
            try:
                kind, payload = outcome.get(timeout=self._cancellation_poll_interval_seconds)
            except queue.Empty:
                continue
            if kind == "error":
                raise payload
            return payload

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
        exception, cancellation (before dispatch, during backoff, or while
        a call is actually in flight - see `_dispatch_with_cancellation`),
        or exhausting the attempt ceiling all end the loop - it never runs
        unbounded."""
        retry_cfg = self._config.resilience.retry
        attempt = 0  # number of retries already taken (0 == first attempt in flight)

        while True:
            self._check_cancellation(cancellation, operation)
            try:
                result = self._dispatch_with_cancellation(call, cancellation, operation)
                return result, attempt
            except GatewayCancelledError:
                # Never logged as a call failure and never retried -
                # propagates as itself, whether cancellation fired before
                # dispatch, during backoff, or while the call was in
                # flight.
                raise
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
                        attempts_made=attempt + 1,
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
