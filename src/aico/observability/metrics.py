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
"""
from __future__ import annotations

from typing import Optional

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


def record_request_latency(latency_ms: float, *, status_code: int) -> None:
    _request_latency_ms.record(latency_ms, {"status_code": str(status_code)})


def record_request_outcome(status: str, category: Optional[str] = None) -> None:
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


def get_metrics_snapshot():
    """Synchronously read every recorded metric - test-facing (and
    diagnostic) only; nothing in the request path calls this."""

    return _reader.get_metrics_data()
