# Day 10 — Disclosure Report

Generated 2026-09-10 by `scripts/day10_generate_gate_b_artifacts.py` from a real `GateB.authorize()` decision (Task 3) and a real `apply_disclosure()` call (Task 9) against the `day10_pack`'s own synthetic demo record (`pii_disclosure_cases.json`'s `synthetic_record` — fixed, fake data). Redacted values shown below are `redaction.mask_value()`'s own deterministic output (Task 9); no raw protected value from this record is written anywhere in this file.

## Applied Policy

- `policy_version`: `1.0`
- Matched `rule_id`: `GB-R001`
- Applied `disclosure_profile`: `policy_reader`

## Public/Internal Field: Allowed

- Field: `supplier_name`
- `action`: **allow**
- Disclosed value: `Synthetic Supplier Alpha`
- A `public`-classified, non-PII field passes through unchanged.

## Contact Field: Deterministically Redacted

- Field: `contact_email`
- `action`: **redact**
- Disclosed value: `a***@example.test`
- A `contact`-category PII field is masked, never shown in full — the value below is the mask, not the original.

## Sensitive Field: Disallowed

- Field: `tax_identifier`
- `action`: **deny**
- Disclosed value: *(none — denied)*
- A `personal_identifier`-category PII field this profile denies outright — no value is disclosed at all, redacted or otherwise.

## No Raw Protected PII Value In This Report

The `contact_email` value shown above is a deterministic mask (`a***@example.test`-shaped, `redaction.mask_value()`), never the original synthetic email. The `tax_identifier` field shows no value at all. `supplier_name` is the one field whose value is shown unmasked — its own governed classification (`public`) and PII category (`none`) are exactly what authorize that.
