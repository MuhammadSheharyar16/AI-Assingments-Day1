# Day 11 Provenance / Freshness Rules

## Provenance

A candidate evidence item should be traceable to:

```text
source_id
source_version
stable evidence/chunk/record ID
content hash
source updated time
retrieved time
tenant / classification
```

Validate the evidence item actually returned for the request.

## Freshness

Use deterministic:

```text
age = as_of - source_updated_at
fresh when age <= configured max_age
```

The exact threshold comes from the governed freshness policy.

Tests must use a fixed/injected `as_of` value.

## Completeness

Completeness is based on required evidence facets, not the number of retrieved chunks.

Example:

```text
required = [payment_terms, invoice_window]
covered = [payment_terms]
→ incomplete
```

## Conflicts

If two valid same-authority sources provide incompatible values for the same facet, the conflict must be surfaced.

Do not ask the model to decide which source should be trusted unless a deterministic governed precedence rule already determines the winner.
