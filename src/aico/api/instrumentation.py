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
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from aico.observability.metrics import record_gateway_call, record_retrieval_latency
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
