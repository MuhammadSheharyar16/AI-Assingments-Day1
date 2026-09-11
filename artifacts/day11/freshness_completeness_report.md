# Day 11 — Freshness & Completeness Report

Generated 2026-09-11 by `scripts/day11_generate_gate_c_artifacts.py` from real `evaluate_freshness()`/`validate_completeness()`/`evaluate_conflict()`/`GateC.evaluate()` calls (Task 6/7/8/9) against the real committed Gate-C policy.

## Governed Freshness Thresholds

- `FRESH-POLICY-30D`: `max_age_hours=720`
- `FRESH-CONTRACT-7D`: `max_age_hours=168`

## Freshness Cases

### Fresh

- `as_of`: `2026-09-11T12:00:00+05:00`
- `source_updated_at`: `2026-09-01T12:00:00+05:00`
- `max_age_hours`: `720`
- `status`: **fresh**

### Threshold-Edge (Exactly At Max Age)

- `as_of`: `2026-09-11T12:00:00+05:00`
- `source_updated_at`: `2026-08-12T12:00:00+05:00`
- `max_age_hours`: `720`
- `status`: **fresh**

### Stale

- `as_of`: `2026-09-11T12:00:00+05:00`
- `source_updated_at`: `2026-08-12T11:59:59+05:00`
- `max_age_hours`: `720`
- `status`: **stale**

## Completeness

- Required facets: `['supplier_identity', 'payment_terms', 'invoice_window']`
- Covered facets: `['supplier_identity', 'payment_terms']`
- Missing facets: `['invoice_window']`

## Conflict Cases

### Unresolved (Same Authority)

- Facet: `payment_terms`
- `outcome`: **unresolved_conflict**

### Resolved By Governed Authority

- Facet: `payment_terms`
- `outcome`: **resolved_by_governed_authority**
- `winning_value`: `net 30`

## Final Gate-C Decision (Combined Scenario)

Two fresh, non-conflicting, source-governed items covering every required facet (`payment_and_invoice`):

- `decision`: **allow**
- `validated_evidence_ids`: `('E-FINAL-A', 'E-FINAL-B')`
- `missing_facets`: `()`
- `conflict_facets`: `()`
- `freshness_summary`: `fresh=2;stale=0;other=0`
