# Day 6 Sanitized Trace Summary

Generated 2026-09-03 by `scripts/day06_generate_trace_artifact.py` from one real `POST /ask` call through the actual FastAPI app (real middleware, real dependency resolution, real `GroundedAnswerService` + Task 9 spans), wired to a fake Model Gateway and a fake retriever - no real network call, per the working rules.

## Request

- Request ID: `d44a7f88-0c84-44a7-91f7-e1b3bf2db083`
- Correlation ID: `870dcd16-9eb1-4267-8d30-c77e0b0926af`
- Result category: `answered`
- Total latency: 2.52 ms

## Trace Stages

| Stage | Span / Operation | Parent | Duration | Sanitized Attributes |
|---|---|---|---:|---|
| API | `api.ask` | (root) | 2.52 ms | correlation_id=870dcd16-9eb1-4267-8d30-c77e0b0926af, request_id=d44a7f88-0c84-44a7-91f7-e1b3bf2db083, response.status=answered |
| Policy/Input | `policy` | api.ask | 0.16 ms | policy.category=benign, policy.outcome=allow |
| Retrieval | `retrieval` | api.ask | 0.31 ms | retrieval.retrieved_count=1 |
| Model Gateway | `model_gateway` | api.ask | 0.35 ms | gateway.latency_ms=42.7, gateway.model_alias=trace-artifact-fake-alias, gateway.retry_count=1, gateway.tokens.completion_tokens=23, gateway.tokens.prompt_tokens=187, gateway.used_fallback=False |
| Validation | `validation` | api.ask | 0.14 ms | validation.citation_count=1, validation.citation_valid=True, validation.result=valid |
| Response Composition | `response_composition` | api.ask | 0.03 ms | response.citation_count=1, response.confidence_label=high |

## Operational Metrics Observed

- Retrieval latency: 0.31 ms
- Model latency: 42.7 ms
- Token usage: prompt=187, completion=23
- Retry count: 1
- Cache hit/miss: not applicable - BM25Retriever (Day 5's default retriever) has no cache concept (lexical search over an in-memory index); see `src/aico/api/instrumentation.py`'s module docstring. `aico.observability.metrics.record_cache_event` exists and is directly unit-tested (`tests/test_day06_observability.py`) for a future cache-aware retriever.

## Redaction Check

Confirmed programmatically (this script asserts each line below before writing the file):

- [x] raw user question/prompt - absent
- [x] retrieved evidence text - absent
- [x] raw model completion - absent
- [x] authorization claims/header - absent
- [x] secrets/tokens - absent

## Notes

This run used a fake Model Gateway/retriever (synthetic data only), so `used_fallback` and `budget_status` are not shown above - both are already available as span/metric attributes (`gateway.used_fallback`, Task 8's `budget_status` metric label) but add nothing distinctive for a single synthetic call; see `tests/test_day06_observability.py` for assertions on them. Cache hit/miss is explained above rather than fabricated, per this file's own instruction.
