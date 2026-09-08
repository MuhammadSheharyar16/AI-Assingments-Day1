# Day 8 Memory Contract Guidance

A session must be scoped by:

```text
trusted tenant_id
trusted user_id
session_id
```

Recommended typed responsibilities:

```text
session_id
tenant_id
user_id
created_at
updated_at
expires_at
version
recent_turns
summary
```

A turn should have:

```text
turn_id
role
timestamp
content/result representation
```

A compacted summary should preserve:

```text
summary_version
source_turn_ids
created_at
model_alias/version metadata when model-generated
```

## Trust rule

Session memory is conversational context.

It is **not** authoritative evidence and is **not** an acceptable evidence citation source.

The Day 5 citation validator must still validate factual citations against current retrieved evidence.
