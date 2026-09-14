# Day 12 — Disclosure & Latency Report

Generated 2026-09-14 by `scripts/day12_generate_gate_d_artifacts.py` from real `check_final_disclosure()`/`detect_protected_value_leak()`/`check_latency_budget()`/`build_safe_failure_response()` calls (Task 6/7/8/9) against the real committed Gate-D policy (`policy_version` `1.0`, `policy\gate_d_policy.v1.json`). No raw protected value or matched secret substring is ever printed below — only governed field names, resolved `DisclosureAction`s, matched pattern *names*, and pass/fail results.

## Disclosure Cases

### Allowed Disclosure Case

- field check `passed`: **True**
- leaked field names: `()`
- field check `reason_codes`: `()`
- secret-pattern check `passed`: **True**
- matched pattern names: `()`

### Redaction Leak Rejection

- field check `passed`: **False**
- leaked field names: `('contact_email',)`
- field check `reason_codes`: `('redactable_field_value_leaked',)`
- secret-pattern check `passed`: **True**
- matched pattern names: `()`

### Denied Protected-Value Leak Rejection

- field check `passed`: **False**
- leaked field names: `('tax_identifier',)`
- field check `reason_codes`: `('denied_field_value_leaked',)`
- secret-pattern check `passed`: **False**
- matched pattern names: `('synthetic_tax_identifier',)`

### Secret-Pattern Leak Rejection

- field check `passed`: **True**
- leaked field names: `()`
- field check `reason_codes`: `()`
- secret-pattern check `passed`: **False**
- matched pattern names: `('synthetic_bearer_token',)`

## Governed Latency Budgets

- `max_total_latency_ms`: `2500`
- `max_model_latency_ms`: `1400`

## Latency Cases

### Within Budget

- `total_latency_ms`: `2400` (budget `2500`)
- `model_latency_ms`: `1300` (budget `1400`)
- `passed`: **True**
- `reason_codes`: `()`

### Exactly-At-Threshold Result

- `total_latency_ms`: `2500` (budget `2500`)
- `model_latency_ms`: `1400` (budget `1400`)
- `passed`: **True**
- `reason_codes`: `()`

### Model Budget Exceeded

- `total_latency_ms`: `2400` (budget `2500`)
- `model_latency_ms`: `1401` (budget `1400`)
- `passed`: **False**
- `reason_codes`: `('model_latency_budget_exceeded',)`

### Total Budget Exceeded

- `total_latency_ms`: `2501` (budget `2500`)
- `model_latency_ms`: `1300` (budget `1400`)
- `passed`: **False**
- `reason_codes`: `('total_latency_budget_exceeded',)`

## Safe-Failure Result

- `status`: `safe_failure`
- `error_code`: `FINAL_RESPONSE_REJECTED`
- `message`: `The response could not be safely returned.`
- `reason_codes`: `('total_latency_budget_exceeded', 'denied_field_value_leaked')`
- Fixed, policy-authored text (Task 9) — never the failing candidate's own answer text, regardless of which check(s) actually failed.
