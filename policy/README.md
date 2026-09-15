# Gate-B policy

`gate_b_policy.v1.json` is the committed, governed Gate-B policy document
for Day 10 (`data/day10_pack/fixtures/gate_b_policy_v1.json`, unmodified).
It is **read-only at runtime**: nothing in the request path, memory layer,
or model output may create, mutate, or widen an entry in it
(`data/day10_pack/gate_b_policy_requirements.md`; Day 10 working rules:
"Session memory cannot grant a permission" / "Permission scope is never
repaired/widened by an LLM").

Typed models for this document live in `src/aico/control/policy_models.py`
(`GateBPolicyDocument` / `Role` / `PermissionRule` / `DisclosureProfile` /
`DataClassification` / `PiiCategory` / `DisclosureAction` /
`TenantScopeKind`) -- every field here is validated against those types,
including duplicate-id rejection, unknown role/permission/classification/
PII-category/disclosure-profile references, and status/lane enum
membership. `src/aico/control/policy_registry.py` loads this file through
those types -- cross-checking every rule's `intent_id` against the real
committed Mode-A ontology (`ontology/registry.v1.json`) -- and exposes
read-only lookups for Gate-B (Task 3).

A new policy version is added as a new `gate_b_policy.vN.json` file, never
by editing a previously committed version in place -- see the assignment's
optional stretch goal (a `gate_b_policy.v2.json` + a compatibility test
proving a deliberate policy update can be reviewed/activated without
runtime mutation of the previous version).

# Gate-C policy

`gate_c_policy.v1.json` is the committed, governed evidence-quality policy
for Day 11 (`data/day11_pack/fixtures/evidence_policy_v1.json`,
unmodified, just renamed to match this directory's `<name>.vN.json`
convention). It is **read-only at runtime**: nothing in the request path,
retrieval layer, or model output may create, mutate, or widen an entry in
it (`data/day11_pack/evidence_policy_requirements.md`).

Typed models for this document live in `src/aico/evidence/policy.py`
(`GateCPolicyDocument` / `IntentEvidenceRequirement` / `FreshnessPolicy` /
`ConflictPolicy`) -- not in `src/aico/control/policy_models.py`, even
though the model/registry split mirrors Gate-B's: Gate-C policy governs
*evidence quality* (trusted source types, freshness thresholds, required
facets, conflict policy, minimum evidence), which Day 11's required
structure places under `src/aico/evidence/`, not `src/aico/control/`
(which owns request authorization/disclosure policy, Gate-B's concern).
Every field here is validated against those types, including duplicate
freshness `policy_id`/`rule_id` rejection and an invalid `status` enum.
`GateCPolicyRegistry.load()` loads this file through those types --
cross-checking every rule's `intent_id` against the real committed Mode-A
ontology (`ontology/registry.v1.json`) and every rule's
`allowed_source_types` against the real committed source registry
(`evidence/source_registry.v1.json`), plus the reverse check that every
governed source's own `freshness_policy_id` (Task 2) names a freshness
policy this document actually declares -- and exposes read-only lookups
for Gate-C (Task 9).

A new policy version is added as a new `gate_c_policy.vN.json` file, never
by editing a previously committed version in place.

# Gate-D policy

`gate_d_policy.v1.json` is the committed, governed final-response policy
for Day 12 (`data/day12_pack/fixtures/gate_d_policy_v1.json`, unmodified,
just renamed to match this directory's `<name>.vN.json` convention). It is
**read-only at runtime**: nothing in the request path, generation layer,
or model output may create, mutate, or widen an entry in it
(`data/day12_pack/gate_d_policy_requirements.md`).

Typed models for this document live in `src/aico/control/policy_models.py`
(`GateDPolicyDocument` / `CitationPolicy` / `QualityPolicy` /
`GateDDisclosurePolicy` / `LatencyBudgets` / `SafeFailureSpec`) -- kept
beside Gate-B's own policy models rather than a new file, since Day 12's
required structure names none for it. Every field here is validated
against those types, including a required, non-empty `policy_version`, a
closed/non-duplicated `allowed_response_statuses` set, and positive
(never zero/negative) `max_answer_chars`/latency-budget values.
`src/aico/control/policy_registry.py`'s `GateDPolicyRegistry` loads this
file through those types -- cross-checking `has_disclosure_profile()`
against the real committed Gate-B policy's own governed disclosure
profiles (`policy/gate_b_policy.v1.json`) -- and exposes read-only lookups
for Gate-D (Task 10).

A new policy version is added as a new `gate_d_policy.vN.json` file, never
by editing a previously committed version in place.

# Tool execution policy

`tool_execution_policy.v1.json` is the committed, governed Tool Execution
Policy for Day 13 (`data/day13_pack/fixtures/tool_execution_policy_v1.json`,
unmodified). It is **read-only at runtime**: nothing in the request path,
model output, or session memory may create, mutate, or widen a rule in it
(Day 13 working rule: "Execution policy defaults to deny").

It declares two rules, one per registered tool/version
(`tools/registry.v1.json`):

- `TOOL-R001` -- `supplier_status_lookup@1.0.0`, `allowed: true`, requiring
  `read_structured_supplier` and the `synthetic-procurement-mcp` server
  alias at `max_risk_level: low`.
- `TOOL-R002` -- `supplier_record_update@1.0.0`, `allowed: false` (the tool
  itself is also `disabled` in the registry, so `tool_disabled` denies it
  before this rule's own `allowed` is ever consulted -- Day 13 working
  rule: "The disabled side-effecting lab tool must never execute").

Typed models for this document live in `src/aico/tools/policy.py`
(`ToolExecutionPolicyDocument` / `ToolExecutionPolicyRule` -- Task 4),
self-validating a required `policy_version`, a `default_decision` pinned
to `"deny"`, no duplicate `rule_id`, and no more than one rule governing
the same `(tool_id, tool_version)` pair. The same module's
`ToolExecutionPolicy.authorize()` is the one deterministic, default-deny
decision engine -- registered tool/version, active/disabled status
(tool's own and the rule's), required trusted permission
(`resolve_trusted_permissions()`, Task 3 -- never `request.arguments`),
approved server alias, risk-level ceiling, and side-effecting/idempotent
retry safety -- returning a typed `ToolExecutionPolicyDecision`, never a
fall-through to allow.

A new policy version is added as a new `tool_execution_policy.vN.json`
file, never by editing a previously committed version in place.
