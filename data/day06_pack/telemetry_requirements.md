# Day 6 Telemetry Requirements

Telemetry must remain useful after sensitive-content redaction.

## Structured logs

Useful fields include:

- request_id
- correlation_id
- stage / operation
- outcome
- latency
- normalized error category

Production/default logs must not contain:

- raw user question/prompt
- retrieved evidence
- model completion
- authorization headers/claims
- secrets/tokens
- raw vectors

## Metrics

Capture applicable signals for:

- request latency
- retrieval latency
- model/gateway latency
- token usage
- retry count
- cache behavior
- request outcome/error category

Do not use raw questions/prompts as metric labels.

## Tracing

A successful `/ask` trace must make these stages identifiable:

```text
API
→ policy/input processing
→ retrieval
→ Model Gateway
→ validation
→ response composition
```

The same correlation context should link the request.

A local/in-memory exporter is sufficient for tests.
