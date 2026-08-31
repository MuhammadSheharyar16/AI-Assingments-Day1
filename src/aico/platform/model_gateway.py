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
  `timeout_seconds`) - see Task 3 for the full bounded-retry policy built
  on top of it.
- Normalizing every failure into aico.platform.errors.ModelGatewayError -
  a caller of this module never needs to know what raised the underlying
  exception.

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

import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from aico.platform.config import GatewayConfig, load_gateway_config
from aico.platform.errors import ModelGatewayError


class CancellationToken:
    """Cooperative cancellation signal. A caller holds the token, passes it
    into a request, and calls `.cancel()` (from another thread, or on its
    own deadline) to ask an in-flight or not-yet-started call to stop
    instead of running to completion. The gateway checks it before
    dispatching a call and, in the bounded-retry loop (Task 3), between
    attempts - it cannot interrupt a single HTTP call already in flight
    against a transport that does not support that itself, but it does
    stop the operation from retrying or from ever starting."""

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


class ModelGateway:
    """Typed chat/embed boundary. Construct once (directly with a config +
    transport, or via `from_config()` for the real Foundry path) and call
    `.chat()` / `.embed()` - never the transport, never a provider SDK."""

    def __init__(
        self,
        config: GatewayConfig,
        transport: Transport,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._config = config
        self._transport = transport
        self._clock = clock

    @classmethod
    def from_config(cls, path: str | None = None) -> "ModelGateway":
        # Local import: this is the only path through this file that
        # touches the real adapter (and therefore, transitively, the HTTP
        # client) - constructing a ModelGateway for tests via __init__
        # directly never imports it.
        from aico.platform.foundry_adapter import FoundryAdapter

        config = load_gateway_config(path) if path is not None else load_gateway_config()
        return cls(config, FoundryAdapter(config))

    @property
    def config(self) -> GatewayConfig:
        return self._config

    def embed(self, request: EmbedRequest) -> EmbedResult:
        model_alias = request.model_alias or self._config.models.embedding
        timeout_seconds = request.timeout_seconds or self._config.resilience.timeout_seconds
        self._check_cancellation(request.cancellation, "embed")

        start = self._clock()
        result = self._call_transport(
            lambda: self._transport.embed(
                model_alias=model_alias, texts=request.texts, timeout_seconds=timeout_seconds
            )
        )
        latency_ms = (self._clock() - start) * 1000

        dimensions = result.dimensions if result.dimensions is not None else 0
        metadata = CallMetadata(
            operation="embed",
            model_alias=model_alias,
            latency_ms=latency_ms,
            retry_count=0,  # bounded retry ceiling/backoff/jitter: Task 3
            token_usage=result.token_usage,
            budget_status=self._embed_budget_status(len(request.texts)),
        )
        return EmbedResult(vectors=result.content, dimensions=dimensions, metadata=metadata)

    def chat(self, request: ChatRequest) -> ChatResult:
        model_alias = request.model_alias or self._config.models.chat
        timeout_seconds = request.timeout_seconds or self._config.resilience.timeout_seconds
        self._check_cancellation(request.cancellation, "chat")

        messages = [{"role": m.role, "content": m.content} for m in request.messages]

        start = self._clock()
        result = self._call_transport(
            lambda: self._transport.chat(
                model_alias=model_alias,
                messages=messages,
                max_output_tokens=request.max_output_tokens,
                timeout_seconds=timeout_seconds,
            )
        )
        latency_ms = (self._clock() - start) * 1000

        metadata = CallMetadata(
            operation="chat",
            model_alias=model_alias,
            latency_ms=latency_ms,
            retry_count=0,
            token_usage=result.token_usage,
            budget_status=self._chat_budget_status(result.token_usage),
        )
        return ChatResult(content=result.content, metadata=metadata)

    # ── internals ────────────────────────────────────────────────────

    def _check_cancellation(self, token: CancellationToken | None, operation: str) -> None:
        if token is not None and token.is_cancelled():
            from aico.platform.errors import GatewayCancelledError

            raise GatewayCancelledError(f"{operation} was cancelled before it started")

    def _call_transport(self, call: Callable[[], TransportResult]) -> TransportResult:
        try:
            return call()
        except ModelGatewayError:
            raise
        except Exception as exc:  # last-resort seam: a transport is expected to normalize
            raise ModelGatewayError(
                f"unnormalized transport failure: {exc.__class__.__name__}: {exc}", cause=exc
            ) from exc

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
