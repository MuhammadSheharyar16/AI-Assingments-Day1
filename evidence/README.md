# Governed source registry

`source_registry.v1.json` is the committed, governed source registry for
Day 11 (`data/day11_pack/fixtures/source_registry_v1.json`, unmodified).
It is **read-only at runtime**: nothing in the request path, retrieval
layer, or model output may create, mutate, widen, or re-enable an entry in
it (`data/day11_pack/evidence_policy_requirements.md`; working rules:
"Evidence source must exist in the governed source registry" / "Disabled,
unknown or unapproved source cannot pass").

Typed models for this document live in
`src/aico/evidence/source_registry.py` (`SourceRegistryDocument` /
`SourceRecord` / `SourceStatus`) -- every field here is validated against
those types, including duplicate `source_id` rejection and an invalid
`status` enum. `SourceRegistry.load()` loads this file through those
types -- cross-checking every source's `allowed_intents` against the real
committed Mode-A ontology (`ontology/registry.v1.json`), the same pattern
`policy_registry.py` already uses for Gate-B's policy -- and exposes
read-only lookups (`get_source`, `is_source_active`, `supports_intent`,
`supports_facet`) for Gate-C (Task 9).

A new registry version is added as a new `source_registry.vN.json` file,
never by editing a previously committed version in place.
