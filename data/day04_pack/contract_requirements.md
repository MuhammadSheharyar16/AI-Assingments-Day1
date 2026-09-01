# Day 4 Contract Requirements

Create two versioned Pydantic contracts.

## 1. Cited Answer — v1

Required fields:

| Field | Type / rule |
|---|---|
| `schema_version` | literal/string version `1.0` |
| `status` | enum: `answered`, `insufficient_evidence` |
| `answer` | string, minimum length 1 |
| `citations` | list of citation objects, may be empty only when status is `insufficient_evidence` |
| `confidence_label` | enum: `low`, `medium`, `high` |

Citation object:

| Field | Type / rule |
|---|---|
| `chunk_id` | non-empty string |
| `source_file` | non-empty string |

Unknown/extra fields must be rejected.

### Important

Day 4 validates the structure/semantics of the citation objects as contract data.

It does **not** yet prove that a cited chunk was actually supplied by retrieval. That citation-grounding check belongs to Day 5.

## 2. Shared Response Envelope — v1

Required fields:

| Field | Type / rule |
|---|---|
| `schema_version` | literal/string version `1.0` |
| `request_id` | non-empty string |
| `result` | cited-answer contract |
| `model_alias` | non-empty string |

Optional fields:

| Field | Type / rule |
|---|---|
| `trace_id` | non-empty string when present |
| `warning` | non-empty string when present |

Unknown/extra fields must be rejected.

## Versioning exercise

Prove that adding this optional field is backward compatible:

```text
warning: Optional[str]
```

The existing v1 caller fixture intentionally does not contain `warning`.

Document at least three examples of breaking changes that require a version decision.
