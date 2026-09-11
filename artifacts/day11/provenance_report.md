# Day 11 — Provenance Report

Generated 2026-09-11 by `scripts/day11_generate_gate_c_artifacts.py` from real `validate_provenance()` calls (Task 4) against the real committed source registry (`source_registry_version` `1.0`, `evidence\source_registry.v1.json`).

## Source Registry Version

`1.0`

## Valid Provenance

- `evidence_id`: `E-VALID`
- `valid`: **True**
- `reasons`: `()`

## Content-Hash Mismatch

- `evidence_id`: `E-HASH`
- `valid`: **False**
- `reasons`: `('content_hash_mismatch',)`

## Source-Version Mismatch

- `evidence_id`: `E-V3`
- `valid`: **False**
- `reasons`: `('source_version_mismatch',)`

## Gate-B Scope Mismatch

- `evidence_id`: `E-SCOPE`
- `valid`: **False**
- `reasons`: `('tenant_out_of_scope',)`

## No Raw Protected Content

Every case above is reported through `ProvenanceItemResult`'s own typed fields (`evidence_id`/`valid`/`reasons`) only — no `EvidenceItem.content` or `.claims` value is ever read for this report, let alone written to it.
