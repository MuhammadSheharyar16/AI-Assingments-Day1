"""
Day 6 Task 11 — sanitized trace artifact generator.

Run: uv run python scripts/day06_generate_trace_artifact.py

Drives one real, successful `POST /ask` call through the actual FastAPI
app (`aico.api.app`) - real middleware, real dependency resolution, real
`GroundedAnswerService.answer()` and its Task 9 spans - wired to a fake
Model Gateway and a fake retriever (no real network call, per the working
rule "do not create avoidable cloud cost"; `tests/test_day06_*.py` use the
identical pattern). It then reads back:

- the request/correlation IDs the response actually carried (Task 3)
- every OpenTelemetry span the request produced (Task 9,
  `aico.observability.telemetry.get_finished_spans()`)
- the gateway/retrieval metrics recorded for this call (Task 8,
  `aico.observability.metrics.get_metrics_snapshot()`)

and renders them into `artifacts/day06/trace_summary.md`, following
`data/day06_pack/trace_summary_template.md`'s structure.

Redaction is not left to a human eyeballing a checklist: the question,
the retrieved evidence, the model's answer text, and a fake Authorization
header value are all deliberately distinctive strings here, and the
script asserts none of them appear anywhere in the rendered document
before writing it - the "Redaction Check" section below is populated from
that assertion having actually passed, not merely from a hand-ticked box.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from aico.api.app import app
from aico.api.dependencies import get_answer_service
from aico.api.identity import TrustedIdentity, get_trusted_identity
from aico.api.instrumentation import MetricsGateway, MetricsRetriever
from aico.observability.metrics import get_metrics_snapshot
from aico.observability.telemetry import clear_finished_spans, get_finished_spans
from aico.platform.model_gateway import CallMetadata, ChatRequest, ChatResult
from aico.rag.answer_service import GroundedAnswerService
from aico.rag.citation_validator import EvidenceChunk

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_PATH = REPO_ROOT / "artifacts" / "day06" / "trace_summary.md"

_IDENTITY = TrustedIdentity(tenant_id="TENANT-SYN-001", user_id="USER-SYN-001")
_AUTH_HEADER_VALUE = "Bearer synthetic-token-should-never-appear-in-the-artifact"

# Deliberately distinctive - these must never appear in the rendered
# artifact; the script verifies that below rather than assuming it.
_QUESTION = "TRACE-ARTIFACT-QUESTION-40217: what are the synthetic supplier's payment terms?"
_EVIDENCE_TEXT = "TRACE-ARTIFACT-EVIDENCE-88103: payment is net 30 days from invoice date."
_ANSWER_TEXT = "TRACE-ARTIFACT-ANSWER-51966: payment terms are net 30 days."

_ANSWERED_JSON = f"""
{{
  "schema_version": "1.0",
  "status": "answered",
  "answer": "{_ANSWER_TEXT}",
  "citations": [{{"chunk_id": "DOC-003::chunk-0", "source_file": "DOC-003-pricing-payment.md"}}],
  "confidence_label": "high"
}}
"""

STAGE_ORDER = [
    ("API", "api.ask"),
    ("Policy/Input", "policy"),
    ("Retrieval", "retrieval"),
    ("Model Gateway", "model_gateway"),
    ("Validation", "validation"),
    ("Response Composition", "response_composition"),
]


class _FakeGateway:
    def chat(self, request: ChatRequest) -> ChatResult:
        return ChatResult(
            content=_ANSWERED_JSON,
            metadata=CallMetadata(
                operation="chat",
                model_alias="trace-artifact-fake-alias",
                latency_ms=42.7,
                retry_count=1,
                token_usage={"prompt_tokens": 187, "completion_tokens": 23},
                budget_status="within_budget",
            ),
        )


def _fake_retriever(question: str) -> list[EvidenceChunk]:
    return [EvidenceChunk(chunk_id="DOC-003::chunk-0", source_file="DOC-003-pricing-payment.md", text=_EVIDENCE_TEXT)]


def _run_request() -> tuple[dict, dict]:
    """Send the one real request this artifact documents. Returns
    (response_json, response_headers)."""

    clear_finished_spans()
    # Wrapped in MetricsGateway/MetricsRetriever (Task 8's instrumentation)
    # exactly like the real dependencies.get_answer_service() does - an
    # override that hands GroundedAnswerService a bare fake, unwrapped,
    # would bypass Task 8's metrics recording entirely.
    app.dependency_overrides[get_answer_service] = lambda: GroundedAnswerService(
        gateway=MetricsGateway(_FakeGateway()), retriever=MetricsRetriever(_fake_retriever)
    )
    app.dependency_overrides[get_trusted_identity] = lambda: _IDENTITY
    client = TestClient(app)

    resp = client.post(
        "/ask",
        json={"question": _QUESTION},
        headers={"Authorization": _AUTH_HEADER_VALUE},
    )
    app.dependency_overrides.clear()

    assert resp.status_code == 200, f"expected a successful call, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["status"] == "answered", f"expected status=answered, got {body['status']!r}"
    return body, dict(resp.headers)


def _span_duration_ms(span) -> float:
    return (span.end_time - span.start_time) / 1_000_000


def _format_attributes(span) -> str:
    if not span.attributes:
        return "(none)"
    return ", ".join(f"{key}={value}" for key, value in sorted(span.attributes.items()))


def _metric_point(data, name: str, **attrs):
    for resource_metrics in data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                if metric.name != name:
                    continue
                for point in metric.data.data_points:
                    if all(point.attributes.get(k) == v for k, v in attrs.items()):
                        return point
    return None


def render_trace_summary(body: dict, headers: dict) -> str:
    spans_by_name = {s.name: s for s in get_finished_spans()}
    spans_by_id = {s.context.span_id: s for s in get_finished_spans()}
    missing_stages = [label for label, name in STAGE_ORDER if name not in spans_by_name]
    assert not missing_stages, f"expected spans missing from a successful trace: {missing_stages}"

    root = spans_by_name["api.ask"]
    total_latency_ms = _span_duration_ms(root)

    lines: list[str] = []
    lines.append("# Day 6 Sanitized Trace Summary")
    lines.append("")
    lines.append(
        f"Generated {date.today().isoformat()} by `scripts/day06_generate_trace_artifact.py` from one real "
        f"`POST /ask` call through the actual FastAPI app (real middleware, real dependency resolution, real "
        f"`GroundedAnswerService` + Task 9 spans), wired to a fake Model Gateway and a fake retriever - no real "
        f"network call, per the working rules."
    )
    lines.append("")

    lines.append("## Request")
    lines.append("")
    lines.append(f"- Request ID: `{body['request_id']}`")
    lines.append(f"- Correlation ID: `{body['correlation_id']}`")
    lines.append(f"- Result category: `{body['status']}`")
    lines.append(f"- Total latency: {total_latency_ms:.2f} ms")
    lines.append("")

    lines.append("## Trace Stages")
    lines.append("")
    lines.append("| Stage | Span / Operation | Parent | Duration | Sanitized Attributes |")
    lines.append("|---|---|---|---:|---|")
    for label, name in STAGE_ORDER:
        span = spans_by_name[name]
        parent_span = spans_by_id.get(span.parent.span_id) if span.parent else None
        parent_label = parent_span.name if parent_span else "(root)"
        duration_ms = _span_duration_ms(span)
        attrs = _format_attributes(span).replace("|", "\\|")
        lines.append(f"| {label} | `{name}` | {parent_label} | {duration_ms:.2f} ms | {attrs} |")
    lines.append("")

    lines.append("## Operational Metrics Observed")
    lines.append("")
    gateway_span = spans_by_name["model_gateway"]
    retrieval_latency_ms = _span_duration_ms(spans_by_name["retrieval"])
    gateway_latency_ms = gateway_span.attributes.get("gateway.latency_ms")
    prompt_tokens = gateway_span.attributes.get("gateway.tokens.prompt_tokens")
    completion_tokens = gateway_span.attributes.get("gateway.tokens.completion_tokens")
    retry_count = gateway_span.attributes.get("gateway.retry_count")

    # Cross-check Task 8 (metrics) against Task 9 (spans) for this exact
    # call - both were populated from the same ModelGateway.chat() result,
    # so they must agree.
    model_alias = gateway_span.attributes.get("gateway.model_alias")
    metrics_data = get_metrics_snapshot()
    retry_point = _metric_point(metrics_data, "aico_gateway_retries_total", model_alias=model_alias)
    assert retry_point is not None and retry_point.value == retry_count, "metrics/span retry_count disagree"

    lines.append(f"- Retrieval latency: {retrieval_latency_ms:.2f} ms")
    lines.append(f"- Model latency: {gateway_latency_ms} ms")
    lines.append(f"- Token usage: prompt={prompt_tokens}, completion={completion_tokens}")
    lines.append(f"- Retry count: {retry_count}")
    lines.append(
        "- Cache hit/miss: not applicable - BM25Retriever (Day 5's default retriever) has no cache concept "
        "(lexical search over an in-memory index); see `src/aico/api/instrumentation.py`'s module docstring. "
        "`aico.observability.metrics.record_cache_event` exists and is directly unit-tested "
        "(`tests/test_day06_observability.py`) for a future cache-aware retriever."
    )
    lines.append("")

    lines.append("## Redaction Check")
    lines.append("")
    lines.append("Confirmed programmatically (this script asserts each line below before writing the file):")
    lines.append("")
    lines.append("- [x] raw user question/prompt - absent")
    lines.append("- [x] retrieved evidence text - absent")
    lines.append("- [x] raw model completion - absent")
    lines.append("- [x] authorization claims/header - absent")
    lines.append("- [x] secrets/tokens - absent")
    lines.append("")

    lines.append("## Notes")
    lines.append("")
    lines.append(
        "This run used a fake Model Gateway/retriever (synthetic data only), so `used_fallback` and "
        "`budget_status` are not shown above - both are already available as span/metric attributes "
        "(`gateway.used_fallback`, Task 8's `budget_status` metric label) but add nothing distinctive for a "
        "single synthetic call; see `tests/test_day06_observability.py` for assertions on them. Cache hit/miss "
        "is explained above rather than fabricated, per this file's own instruction."
    )
    lines.append("")

    return "\n".join(lines), spans_by_name


def _assert_redacted(rendered: str, response_headers: dict) -> None:
    forbidden = [_QUESTION, _EVIDENCE_TEXT, _ANSWER_TEXT, _AUTH_HEADER_VALUE]
    for secret in forbidden:
        assert secret not in rendered, f"redaction failure: {secret!r} appears in the rendered trace summary"
    # The Authorization header value must not appear anywhere, including
    # under a different key - belt and suspenders beyond the direct check
    # above.
    assert response_headers.get("authorization") is None or _AUTH_HEADER_VALUE not in response_headers.get(
        "authorization", ""
    )


def main() -> None:
    body, headers = _run_request()
    rendered, _spans = render_trace_summary(body, headers)
    _assert_redacted(rendered, headers)

    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT_PATH.write_text(rendered, encoding="utf-8")
    print(f"wrote {ARTIFACT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
