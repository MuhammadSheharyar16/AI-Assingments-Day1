# Day 4 Semantic Rules

Contract/schema validation checks shape and types.

Semantic validation checks application-owned meaning after contract validation succeeds.

Implement these deterministic rules for the Day 4 cited-answer contract.

## Rule S1 — Answered responses require at least one citation

If:

```text
status == "answered"
```

then:

```text
citations must contain at least one item
```

## Rule S2 — Insufficient-evidence responses must not claim high confidence

If:

```text
status == "insufficient_evidence"
```

then:

```text
confidence_label must not be "high"
```

## Rule S3 — Citation chunk IDs must be unique inside the response

Duplicate `chunk_id` values in the same answer are semantically invalid.

## Rule S4 — Insufficient-evidence responses must not contain citations

If:

```text
status == "insufficient_evidence"
```

then:

```text
citations must be empty
```

## Rule S5 — Answer text must agree with status

Use this deterministic Day 4 lab convention:

- `answered` → answer must not begin with `INSUFFICIENT_EVIDENCE`
- `insufficient_evidence` → answer must begin with `INSUFFICIENT_EVIDENCE`

This is a lab semantic rule so you can demonstrate that schema-valid data can still fail application meaning checks.

## Important

These rules do not verify whether a citation is grounded in retrieved evidence. That comes on Day 5.
