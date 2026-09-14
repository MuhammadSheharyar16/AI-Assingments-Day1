# Day 12 — Final Citation Report

Generated 2026-09-14 by `scripts/day12_generate_gate_d_artifacts.py` from real `reconcile_final_citations()` calls (Task 3/4) against the real committed Gate-D citation policy (`policy_version` `1.0`, `policy\gate_d_policy.v1.json`).

## Valid Reconciliation

- Gate-C approved evidence IDs: `('E-101',)`
- Final citation IDs: `('E-101',)`
- `passed`: **True**
- `reason_codes`: `()`
- `valid_citation_evidence_ids`: `('E-101',)`
- `invalid_citation_evidence_ids`: `()`

## Forged Citation Rejection

- Gate-C approved evidence IDs: `()`
- Final citation IDs: `('E-999',)`
- `passed`: **False**
- `reason_codes`: `('citation_not_gate_c_validated',)`
- `valid_citation_evidence_ids`: `()`
- `invalid_citation_evidence_ids`: `('E-999',)`

## Gate-C-Rejected Citation Rejection

- Gate-C approved evidence IDs: `('E-101',)`
- Final citation IDs: `('E-202',)`
- `passed`: **False**
- `reason_codes`: `('citation_not_gate_c_validated',)`
- `valid_citation_evidence_ids`: `()`
- `invalid_citation_evidence_ids`: `('E-202',)`

## No Raw Protected Evidence Content

Every case above is reported through `CitationReconciliationReport`'s own typed fields (`evidence_id`/`chunk_id`/`source_id`/`source_version` identities and governed reason codes only) — no `EvidenceItem`/`GateCEvidenceRecord` content is ever read for this report, let alone written to it.
