# Day 8 Context Budget Guidance

The developer chooses and documents the exact values.

At minimum define:

```text
max_memory_tokens
max_recent_turns
```

The memory context builder should prefer:

1. current request
2. compacted summary
3. newest recent turns that fit the budget

Required behavior:

- unlimited history is not passed to the model
- token-count method is documented
- older turns compact when the memory budget is exceeded
- compacted source-turn provenance is retained
- current retrieved evidence remains a separate grounding section
