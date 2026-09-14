# Day 12 — Gate-D Decisions

Generated 2026-09-14 by `scripts/day12_generate_gate_d_artifacts.py` from real `GateD.evaluate()` calls (Task 10) against the real committed Gate-D policy (`policy_version` `1.0`, `policy\gate_d_policy.v1.json`). Every candidate answer/citation/protected-field value below is synthetic demo data this script constructs directly; no raw candidate answer text or protected value is written below, only Gate-D's own typed decision.

## Fully Valid Response

- `decision`: **allow**
- `reason_codes`: `()`
- `validated_citation_ids`: `('E-101',)`
- `safe_failure_code`: `None`
- sub-checks — quality: **passed**, citation: **passed**, disclosure: **passed**, latency: **passed**

## Invalid Citation

- `decision`: **safe_failure**
- `reason_codes`: `('citation_not_gate_c_validated',)`
- `validated_citation_ids`: `()`
- `safe_failure_code`: `FINAL_RESPONSE_REJECTED`
- sub-checks — quality: **passed**, citation: **failed**, disclosure: **passed**, latency: **passed**

## Quality Failure

- `decision`: **safe_failure**
- `reason_codes`: `('answer_too_long',)`
- `validated_citation_ids`: `()`
- `safe_failure_code`: `FINAL_RESPONSE_REJECTED`
- sub-checks — quality: **failed**, citation: **passed**, disclosure: **passed**, latency: **passed**

## Disclosure Leak

- `decision`: **safe_failure**
- `reason_codes`: `('denied_field_value_leaked', 'synthetic_secret_pattern_detected')`
- `validated_citation_ids`: `()`
- `safe_failure_code`: `FINAL_RESPONSE_REJECTED`
- sub-checks — quality: **passed**, citation: **passed**, disclosure: **failed**, latency: **passed**

## Latency Budget Failure

- `decision`: **safe_failure**
- `reason_codes`: `('total_latency_budget_exceeded',)`
- `validated_citation_ids`: `()`
- `safe_failure_code`: `FINAL_RESPONSE_REJECTED`
- sub-checks — quality: **passed**, citation: **passed**, disclosure: **passed**, latency: **failed**

## Invalid Internal Candidate (Reject)

- `decision`: **reject**
- `reason_codes`: `('contract_validation_failed',)`
- `validated_citation_ids`: `()`
- `safe_failure_code`: `None`
- sub-checks — quality: **failed**, citation: **passed**, disclosure: **passed**, latency: **passed**
