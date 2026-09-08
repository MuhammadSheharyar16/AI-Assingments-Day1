# AICO Day 8 Resource Pack

Supports:

**Day 8 — Session State and Memory**

This pack contains deterministic synthetic test scenarios.

## Contents

```text
day08_pack/
  README.md
  memory_contract_guidance.md
  context_budget_guidance.md
  fixtures/
    session_lifecycle_cases.json
    isolation_cases.json
    context_compaction_cases.json
    memory_safety_cases.json
```

## Core rule

**Memory helps interpret the conversation. Retrieved evidence still determines what is true.**

The resource pack does not prescribe:
- exact class names
- exact TTL
- exact token budget
- exact SQLite schema

Those are implementation decisions that must be documented and tested.
