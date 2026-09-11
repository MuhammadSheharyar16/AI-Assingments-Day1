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
