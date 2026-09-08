"""
Day 6 Task 8 — metrics instrumentation wrappers.

`GroundedAnswerService` (Day 5) is duck-typed against a `Retriever`
protocol and a `ModelGateway`-shaped `.chat()` boundary - it never checks
`isinstance`. That means metrics can be recorded by wrapping the real
gateway/retriever at the dependency-injection boundary (dependencies.py)
rather than editing Day 5 itself, keeping the working rule "Keep Day 5 as
the internal RAG application flow" intact: `answer_service.py` is
unmodified by Task 8.

`MetricsGateway.chat()` records latency/retry/token metrics from the
real `ModelGateway`'s own already-sanitized `CallMetadata` (Day 3) -
`ChatResult.content` (the actual completion text) passes through
untouched and unread by this wrapper, so it can never end up in a metric
label.

`MetricsRetriever` records retrieval-stage latency. BM25Retriever (Day 5's
default) has no cache concept - lexical search over an in-memory index,
nothing to hit or miss - so no cache metric is recorded for it; the
retrieval cache metric (`observability.metrics.record_cache_event`) exists
and is directly tested, ready for a future cache-aware retriever to call,
but is not fabricated here for one that does not exist (Task 8's own
"where applicable").

Day 8 Task 11 — `MetricsSessionStore` wraps any `SessionStore`-shaped
object (real `SqliteSessionStore`/`InMemorySessionStore` or a test fake)
at the exact same dependency-injection boundary (`dependencies.py`'s
`get_session_store`), the same wrap-not-edit pattern as
`MetricsGateway`/`MetricsRetriever` above: `aico.memory.store` is
unmodified. It records ONLY sanitized counts/categories - a session
event's type, an isolation denial's already-safe `reason` - never a
session id, tenant/user id, or any turn/summary content (Task 11's "do
not log raw session conversation content" rule); `SessionState`/
exceptions pass through completely untouched, so this wrapper can never
change what a caller sees, only what gets recorded alongside it."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from aico.memory.errors import SessionNotFoundError
from aico.memory.models import SessionState
from aico.memory.store import SessionStore
from aico.observability.metrics import (
    record_gateway_call,
    record_isolation_denial,
    record_retrieval_latency,
    record_session_event,
)
from aico.platform.model_gateway import ChatRequest, ChatResult, EmbedRequest, EmbedResult
from aico.rag.answer_service import Retriever
from aico.rag.citation_validator import EvidenceChunk


class _ChatCapableGateway(Protocol):
    def chat(self, request: ChatRequest) -> ChatResult: ...


@dataclass
class MetricsGateway:
    """Wraps any `ModelGateway`-shaped object (real or fake), recording
    gateway metrics on every `.chat()` call. `embed()` passes through
    unmetered - `GroundedAnswerService` never calls it."""

    inner: _ChatCapableGateway

    def chat(self, request: ChatRequest) -> ChatResult:
        result = self.inner.chat(request)
        record_gateway_call(result.metadata)
        return result

    def embed(self, request: EmbedRequest) -> EmbedResult:  # pragma: no cover - unused by GroundedAnswerService
        return self.inner.embed(request)


@dataclass
class MetricsRetriever:
    """Wraps any `Retriever`-shaped callable, recording retrieval
    latency. Never inspects/logs the question or the retrieved chunk
    text - only timing."""

    inner: Retriever

    def __call__(self, question: str) -> list[EvidenceChunk]:
        start = time.monotonic()
        result = self.inner(question)
        record_retrieval_latency((time.monotonic() - start) * 1000)
        return result


@dataclass
class MetricsSessionStore:
    """Wraps any `SessionStore`-shaped object, recording a sanitized
    session-lifecycle event (Day 8 Task 11) on every successful
    `create`/`get`/`clear`/`delete`/`expire` call, and an isolation-denial
    event (by `SessionNotFoundError.reason` alone) whenever one of those
    calls fails closed instead. `save` is deliberately NOT wrapped here -
    Task 11's own event list is create/load/expire/clear, and a save's
    memory-context/compaction metrics need values (`MemoryContext.token_count`,
    whether `compact_session` actually changed anything) this store-level
    wrapper never has access to; `app.py` records those directly, where
    they are already computed (Task 5/6)."""

    inner: SessionStore

    def create(self, **kwargs) -> SessionState:  # type: ignore[no-untyped-def]
        session = self.inner.create(**kwargs)
        record_session_event("created")
        return session

    def get(self, **kwargs) -> SessionState:  # type: ignore[no-untyped-def]
        try:
            session = self.inner.get(**kwargs)
        except SessionNotFoundError as exc:
            record_isolation_denial(exc.reason)
            raise
        record_session_event("loaded")
        return session

    def save(self, session: SessionState) -> SessionState:
        return self.inner.save(session)

    def clear(self, **kwargs) -> SessionState:  # type: ignore[no-untyped-def]
        try:
            session = self.inner.clear(**kwargs)
        except SessionNotFoundError as exc:
            record_isolation_denial(exc.reason)
            raise
        record_session_event("cleared")
        return session

    def delete(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        try:
            self.inner.delete(**kwargs)
        except SessionNotFoundError as exc:
            record_isolation_denial(exc.reason)
            raise
        record_session_event("deleted")

    def expire(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        try:
            self.inner.expire(**kwargs)
        except SessionNotFoundError as exc:
            record_isolation_denial(exc.reason)
            raise
        record_session_event("expired")
