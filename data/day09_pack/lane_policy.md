# Day 9 Lane Policy

Allowed lane IDs:

```text
rag
mode_b
clarify
block
safe_fast_path
```

Required policy examples:

- governed document/policy question → `rag`
- governed structured-data intent → `mode_b`
- ambiguous intent → `clarify`
- unsupported or blocked request → `block`
- explicitly governed deterministic utility/help case → `safe_fast_path`

Rules:

- no arbitrary lane strings
- unknown intent does not default to RAG
- clarify/block do not call retrieval or model generation
- Day 9 selects Mode B but does not implement uncontrolled Mode-B execution
