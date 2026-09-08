"""
Day 6 Task 8 — metrics.

Uses the OpenTelemetry Metrics API/SDK (already required for Task 9's
tracing, so this reuses the same dependency rather than adding a second
metrics stack) with an `InMemoryMetricReader` - readable synchronously
via `get_metrics_snapshot()`, so tests assert exact recorded values
without a real Prometheus/OTLP backend (no avoidable real network call,
per the working rules; a local/in-memory exporter is explicitly
acceptable per the assignment for Task 9's tracing, and the same applies
here).

Instruments (all "where applicable" per the assignment - a metric with
nothing to report simply is not recorded, never fabricated):

    aico_request_latency_ms        histogram   end-to-end HTTP request latency
    aico_request_outcome_total     counter     one per finished /ask request,
                                                labelled by its typed AskStatus
    aico_retrieval_latency_ms      histogram   retrieval-stage latency
    aico_retrieval_cache_total     counter     hit/miss, only recorded by a
                                                retriever that actually has a
                                                cache concept (BM25 does not -
                                                see instrumentation.py)
    aico_gateway_latency_ms        histogram   Model Gateway call latency
                                                (from ModelGateway's own
                                                already-sanitized CallMetadata)
    aico_gateway_tokens_total      counter     token usage, by token type
                                                (prompt/completion) - counts
                                                only, never token content
    aico_gateway_retries_total     counter     retry count per gateway call

Every label used below is a bounded, low-cardinality, already-safe value
this codebase already treats as safe to log (status codes, AskStatus
values, model aliases, budget status, token-usage key names) - never a
user question, retrieved evidence, a model completion, or anything
else with unbounded cardinality (working rule: "Avoid unbounded/
high-cardinality labels such as full user questions").

Day 8 Task 11 adds session/memory instruments, same rules:

    aico_session_event_total           counter     session store operations
                                                    by type (created/loaded/
                                                    cleared/deleted/expired) -
                                                    session_id/tenant/user are
                                                    never labels (unbounded
                                                    cardinality)
    aico_session_isolation_denial_total counter    denied session accesses,
                                                    labelled only by
                                                    SessionNotFoundError's
                                                    already-sanitized `reason`
                                                    ("not_found"/"expired") -
                                                    never which tenant/session
                                                    was denied
    aico_memory_context_tokens         histogram   MemoryContext.token_count
                                                    built per request - a
                                                    count, never the memory
                                                    text itself
    aico_memory_context_recent_turns   histogram   count of recent turns
                                                    included per request
    aico_compaction_total              counter     session-save operations,
                                                    labelled by whether
                                                    compact_session actually
                                                    changed anything
                                                    ("occurred": "true"/"false")
"""
from __future__ import annotations

from opentelemetry import metrics as otel_metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from aico.platform.model_gateway import CallMetadata

_reader = InMemoryMetricReader()
_provider = MeterProvider(metric_readers=[_reader])
otel_metrics.set_meter_provider(_provider)

_meter = otel_metrics.get_meter("aico.api")

_request_latency_ms = _meter.create_histogram(
    name="aico_request_latency_ms", unit="ms", description="End-to-end HTTP request latency"
)
_request_outcome_total = _meter.create_counter(
    name="aico_request_outcome_total", description="Count of finished /ask requests by outcome"
)
_retrieval_latency_ms = _meter.create_histogram(
    name="aico_retrieval_latency_ms", unit="ms", description="Retrieval-stage latency"
)
_retrieval_cache_total = _meter.create_counter(
    name="aico_retrieval_cache_total", description="Retrieval cache hit/miss count"
)
_gateway_latency_ms = _meter.create_histogram(
    name="aico_gateway_latency_ms", unit="ms", description="Model Gateway call latency"
)
_gateway_tokens_total = _meter.create_counter(
    name="aico_gateway_tokens_total", unit="tokens", description="Model Gateway token usage by token type"
)
_gateway_retries_total = _meter.create_counter(
    name="aico_gateway_retries_total", description="Model Gateway retry count"
)
_session_event_total = _meter.create_counter(
    name="aico_session_event_total", description="Count of session store operations by type"
)
_session_isolation_denial_total = _meter.create_counter(
    name="aico_session_isolation_denial_total", description="Count of denied session accesses by reason"
)
_memory_context_tokens = _meter.create_histogram(
    name="aico_memory_context_tokens", unit="tokens", description="Token count of the memory context built per request"
)
_memory_context_recent_turns = _meter.create_histogram(
    name="aico_memory_context_recent_turns", description="Recent-turn count included in the memory context built per request"
)
_compaction_total = _meter.create_counter(
    name="aico_compaction_total", description="Count of session-save operations, by whether compaction actually occurred"
)


def record_request_latency(latency_ms: float, *, status_code: int) -> None:
    _request_latency_ms.record(latency_ms, {"status_code": str(status_code)})


def record_request_outcome(status: str, category: str | None = None) -> None:
    _request_outcome_total.add(1, {"status": status, "category": category or "none"})


def record_retrieval_latency(latency_ms: float) -> None:
    _retrieval_latency_ms.record(latency_ms)


def record_cache_event(*, hit: bool) -> None:
    _retrieval_cache_total.add(1, {"result": "hit" if hit else "miss"})


def record_gateway_call(metadata: CallMetadata) -> None:
    """Records latency/retry/token metrics from a `ModelGateway` call's
    own already-sanitized `CallMetadata` (Day 3) - never sees, and
    therefore can never leak, the prompt or the completion."""

    labels = {"model_alias": metadata.model_alias, "budget_status": metadata.budget_status}
    _gateway_latency_ms.record(metadata.latency_ms, labels)
    _gateway_retries_total.add(metadata.retry_count, {"model_alias": metadata.model_alias})
    if metadata.token_usage:
        for token_type, count in metadata.token_usage.items():
            _gateway_tokens_total.add(count, {"model_alias": metadata.model_alias, "token_type": token_type})


def record_session_event(event: str) -> None:
    """`event` is one of "created"/"loaded"/"cleared"/"deleted"/"expired"
    (Day 8 Task 11) - a fixed, bounded vocabulary, never a session id."""

    _session_event_total.add(1, {"event": event})


def record_isolation_denial(reason: str) -> None:
    """`reason` is `SessionNotFoundError.reason` ("not_found"/"expired") -
    already the safe, non-disclosing category Task 3 designed for exactly
    this telemetry use, never which tenant/session was denied."""

    _session_isolation_denial_total.add(1, {"reason": reason})


def record_memory_context(*, token_count: int, recent_turn_count: int) -> None:
    _memory_context_tokens.record(token_count)
    _memory_context_recent_turns.record(recent_turn_count)


def record_compaction(*, occurred: bool) -> None:
    _compaction_total.add(1, {"occurred": "true" if occurred else "false"})


def get_metrics_snapshot():
    """Synchronously read every recorded metric - test-facing (and
    diagnostic) only; nothing in the request path calls this."""

    return _reader.get_metrics_data()
