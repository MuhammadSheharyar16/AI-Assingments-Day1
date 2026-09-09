# Mode-A ontology registry

`registry.v1.json` is the committed, governed Mode-A ontology document for
Day 9 (`data/day09_pack/fixtures/ontology_registry_v1.json`, unmodified).
It is **read-only at runtime**: nothing in the request path, memory layer,
or model output may create, mutate, or widen an entry in it
(`data/day09_pack/ontology_requirements.md`; Day 9 working rules).

Typed models for this document live in `src/aico/control/ontology.py`
(`OntologyDocument` / `Domain` / `Concept` / `Intent` / `LaneId`) — every
field here is validated against those types, including duplicate-id
rejection, dangling relationship/lane references, and status/lane enum
membership. Task 2's `src/aico/control/ontology_registry.py` loads this
file through those types and exposes read-only lookups for Gate-A and the
lane selector.

A new ontology version is added as a new `registry.vN.json` file, never by
editing a previously committed version in place — see the assignment's
optional stretch goal (`registry.v2.json` + a compatibility test proving an
additive, non-breaking change loads without mutating the active runtime
version).
