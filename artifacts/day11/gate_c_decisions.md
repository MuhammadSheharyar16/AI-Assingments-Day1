# Day 11 — Gate-C Decisions

Generated 2026-09-11 by `scripts/day11_generate_gate_c_artifacts.py` from real `GateC.evaluate()` calls (Task 9-12) against the real committed source registry (`source_registry_version` `1.0`, `evidence\source_registry.v1.json`) and Gate-C policy (`policy_version` `1.0`, `policy\gate_c_policy.v1.json`). Every evidence item is synthetic demo data this script constructs directly; no raw evidence content is written below, only Gate-C's own typed decision.

## Fully Valid Evidence

- `decision`: **allow**
- `validated_evidence_ids`: `('E-VALID',)`
- `rejected_evidence_ids`: `()`
- `reason_codes`: `()`
- `freshness_summary`: `fresh=1;stale=0;other=0`
- Model Gateway calls: **1** (reached, exactly once)

## Unknown Source

- `decision`: **reject**
- `validated_evidence_ids`: `()`
- `rejected_evidence_ids`: `('E-UNKNOWN',)`
- `reason_codes`: `('unknown_source', 'no_valid_evidence')`
- `freshness_summary`: `fresh=0;stale=0;other=1`
- Model Gateway calls: **0** (zero — Task 11 no-fall-through)

## Broken Provenance

- `decision`: **reject**
- `validated_evidence_ids`: `()`
- `rejected_evidence_ids`: `('E-BROKEN',)`
- `reason_codes`: `('content_hash_mismatch', 'no_valid_evidence')`
- `freshness_summary`: `fresh=1;stale=0;other=0`
- Model Gateway calls: **0** (zero — Task 11 no-fall-through)

## Stale Evidence

- `decision`: **reject**
- `validated_evidence_ids`: `()`
- `rejected_evidence_ids`: `('E-STALE',)`
- `reason_codes`: `('stale', 'no_valid_evidence')`
- `freshness_summary`: `fresh=0;stale=1;other=0`
- Model Gateway calls: **0** (zero — Task 11 no-fall-through)

## Incomplete Evidence

- `decision`: **insufficient_evidence**
- `validated_evidence_ids`: `()`
- `rejected_evidence_ids`: `()`
- `reason_codes`: `('missing_required_facets',)`
- `missing_facets`: `('payment_terms',)`
- `freshness_summary`: `fresh=1;stale=0;other=0`
- Model Gateway calls: **0** (zero — Task 11 no-fall-through)

## Unresolved Conflict

- `decision`: **reject**
- `validated_evidence_ids`: `()`
- `rejected_evidence_ids`: `()`
- `reason_codes`: `('unresolved_conflict',)`
- `conflict_facets`: `('payment_terms',)`
- `freshness_summary`: `fresh=2;stale=0;other=0`
- Model Gateway calls: **0** (zero — Task 11 no-fall-through)
