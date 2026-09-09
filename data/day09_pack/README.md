# AICO Day 9 Resource Pack

Supports **Day 9 — Ontology Registry, Gate-A and Lane Selection**.

## Contents

```text
day09_pack/
  README.md
  ontology_requirements.md
  lane_policy.md
  fixtures/
    ontology_registry_v1.json
    gate_a_cases.json
    lane_selection_cases.json
    ambiguity_cases.json
```

## Core distinctions

- Mode A defines governed meaning and control.
- Mode B contains authoritative facts.
- Gate-A classifies domain/intent before lane selection.
- Lane Selector chooses RAG, Mode B, clarify, block, or safe fast path.
- Gate-B permission/PII/disclosure is not part of Day 9.

Do not edit fixed fixtures to improve results.
