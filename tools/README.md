# Tool Registry

`registry.v1.json` is the committed, governed Tool Registry document for
Day 13 (`data/day13_pack/fixtures/tool_registry_v1.json`, unmodified). It
is **read-only at runtime**: nothing in the request path, model output, or
session memory may create, mutate, or widen an entry in it (Day 13 working
rule: "Request body, model output and session memory cannot self-assert
tool permission" -- registration is the same category of trust decision).

It declares two tools:

- `supplier_status_lookup` (`1.0.0`, `active`) -- the read-only,
  idempotent synthetic lab tool every successful execution path in this
  assignment is built against.
- `supplier_record_update` (`1.0.0`, `disabled`) -- the side-effecting,
  non-idempotent synthetic lab tool that must never execute or retry
  (Day 13 working rule: "The disabled side-effecting lab tool must never
  execute").

Typed models for this document live in `src/aico/tools/models.py`
(`ToolDefinition` / `ToolRegistryDocument` -- Task 1), self-validating
against every rule in `tool_registry_requirements.md`: required
`registry_version`, no duplicate `(tool_id, tool_version)` pairs, strict
semantic versioning, a closed `status`/`transport` enum, a required
`owner`, bounded timeout/retry metadata, structurally valid
`input_schema`/`output_schema`, and no retry-enabled `retry_policy` on a
side-effecting/non-idempotent tool. `src/aico/tools/registry.py`'s
`ToolRegistry` (Task 2) loads this file through those types -- exact
`(tool_id, tool_version)` lookup, active/disabled state, and a typed
`tool_not_found` vs `version_not_found` failure distinction for anything
this document does not govern -- and exposes it read-only to Task 4's
execution policy and beyond.

A new registry version is added as a new `registry.vN.json` file, never by
editing a previously committed version in place.
