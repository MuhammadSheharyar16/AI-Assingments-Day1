# AICO Day 6 Resource Pack

Supports **Day 6 — API Surface and Observability**.

This pack contains synthetic examples and deterministic validation cases only.

## Contents

```text
data/day06_pack/
  README.md
  uv_workflow.md
  api_contract_guidance.md
  telemetry_requirements.md
  trace_summary_template.md
```

The synthetic validation fixtures (`api_cases.json`, `identity_claim_cases.json`,
`dependency_health_cases.json`) live under `tests/fixtures/day06/` - read
directly from there by the Day 6 API/identity/health tests - matching how
Day 5's `attack_fixtures.json` moved to `tests/fixtures/day05/attacks/`.

## Important

The pack does not provide:
- production authentication infrastructure
- production tenant/user values
- a real OpenTelemetry backend
- Day 7 evaluation data

Developers must preserve the existing Day 1–5 repository and build Day 6 on top of it.


## Package management

Day 6 requires **`uv`**. Use `pyproject.toml` + committed `uv.lock`, run `uv sync`, and execute project commands with `uv run ...`. See `uv_workflow.md`.
