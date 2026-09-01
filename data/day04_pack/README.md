# AICO Day 4 — Supplied Resource Pack

This pack supports **Day 4 — Structured AI Contracts**.

```text
day04_pack/
  README.md
  contract_requirements.md
  semantic_rules.md
  fixtures/
    structured_output_cases.json
    existing_caller_v1.json
```

## Purpose

Use these files as fixed learning/test inputs for Day 4.

They are deliberately small so the focus stays on:
- Pydantic contracts
- JSON Schema generation
- deterministic validation
- semantic validation
- bounded repair
- versioning/backward compatibility

Do not edit failing fixtures into passing ones.

## Workflow

1. Implement the two required Pydantic contracts.
2. Generate and commit JSON Schema from those models.
3. Run the fixture cases through parse + contract/schema validation.
4. Run semantic validation only after contract validation passes.
5. Allow at most one repair attempt when your repair policy permits it.
6. Revalidate repaired output through the complete pipeline.
7. Prove the optional-field backward-compatibility case.

## Important boundaries

- Day 3 Model Gateway remains the only model-call boundary.
- Day 4 does not implement final grounded answering or citation verification against retrieved chunks.
- Failure-path tests should use fake gateway/model responses.
- Do not log complete unsafe model outputs by default.
