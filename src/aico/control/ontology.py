"""
Day 9 -- typed Mode-A ontology model (Task 1).

`src/aico/control/` is the Day 9 control-plane boundary
(`day09_pack/ontology_requirements.md`, `day09_pack/lane_policy.md`). This
module is its single source of truth for what a governed Mode-A ontology
document looks like: `OntologyDocument` (`ontology_version` / `domains` /
`concepts` / `intents` / `lanes`) plus the two governed record types it
carries -- `Concept` and `Intent` -- and the closed `LaneId` set
`lane_policy.md` names. Task 2's `ontology_registry.py` loads the committed
`ontology/registry.v1.json` file through these types and wraps the parsed
result for read-only runtime access; Gate-A (Task 3/4) and the lane selector
(Task 5) only ever see typed `Concept` / `Intent` / `LaneId` values out of
that registry -- never a raw dict.

Every model here sets `extra="forbid"`, matching the Day 4 / Day 8 contract
convention (`contracts/models.py`, `memory/models.py`): an unknown field is a
registry defect, not something silently dropped.

"Unchecked dictionaries are not acceptable as the registry boundary" (Day 9
Task 1) is read literally here: every "Required validation" bullet from
`ontology_requirements.md` is enforced *inside* this module's types, not by
ad hoc `if` statements over a parsed dict somewhere downstream:

    - ontology version required     -> `OntologyDocument.ontology_version`
                                        is a required, non-empty field (no
                                        default).
    - unique concept IDs            -> `OntologyDocument` model_validator.
    - unique intent IDs             -> `OntologyDocument` model_validator.
    - relationship targets exist    -> `OntologyDocument` model_validator
                                        walks every `Concept.relationships`
                                        entry against the registry's own
                                        concept_id set.
    - intent lane IDs are governed  -> `LaneId` enum (an intent can never
                                        even parse with an unrecognized lane
                                        string) plus an `OntologyDocument`
                                        model_validator that further checks
                                        each `Intent.allowed_lanes` entry
                                        against the lanes *this ontology
                                        version itself* declares enabled.
    - status values are valid       -> `LifecycleStatus` enum on every
                                        domain/concept/intent.
    - runtime registry is read-only -> these are plain immutable-by-convention
                                        Pydantic values; Task 2's registry
                                        service is what actually guarantees no
                                        runtime code path can mutate a loaded
                                        document (this module only defines the
                                        shape).
    - registry loads into typed objects -> `OntologyDocument.model_validate(...)`
                                        returns nested `Domain` / `Concept` /
                                        `Intent` objects, never dicts.

A raw JSON document that fails any of these raises `pydantic.ValidationError`
the same way a malformed Day 4 contract does -- one exception type, whether
the defect is a wrong field type or a dangling relationship/lane reference.

Two extensions beyond the pack's explicit bullet list, made for the same
"no unchecked references" reason and cheap to enforce in the same place:
    - a duplicate `domain_id` is rejected, exactly like a duplicate concept
      or intent id -- a registry cannot govern two different domains under
      one name.
    - `Intent.domain` must reference a domain that actually exists in
      `domains` -- an intent governed under an undeclared domain is exactly
      the kind of dangling reference the pack's relationship/lane checks
      exist to catch, just one field over.

What this module deliberately does NOT do: it does not decide *which*
concept/intent a piece of user language maps to (that is Gate-A, Task 3/4),
and it does not decide which single lane an intent's `allowed_lanes` ends up
routed to for a given request (that is the lane selector, Task 5). This
module only defines -- and self-validates -- what a governed Mode-A ontology
document is allowed to look like.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LaneId(str, Enum):
    """The closed set of governed lane identifiers `lane_policy.md` allows
    ("no arbitrary lane strings"). Both `OntologyDocument.lanes` (which
    lanes this ontology version enables) and `Intent.allowed_lanes` (which
    of those enabled lanes a given intent may route through) are typed
    against this enum, so an unrecognized lane id is rejected by Pydantic
    itself, before any registry-level cross-check even runs."""

    RAG = "rag"
    MODE_B = "mode_b"
    CLARIFY = "clarify"
    BLOCK = "block"
    SAFE_FAST_PATH = "safe_fast_path"


class LifecycleStatus(str, Enum):
    """Governance lifecycle for a domain/concept/intent
    (`ontology_requirements.md`: "status values are valid"; "Concepts
    covered today": "ownership and lifecycle"). `ACTIVE` is the only status
    the supplied v1 fixture uses; `DEPRECATED` / `RETIRED` exist so a later
    registry version can wind an entry down without deleting its id out
    from under anything that still references it (e.g. a `relationships`
    target)."""

    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class Domain(BaseModel):
    """A governed Mode-A domain (`ontology_requirements.md`; registry
    fixture: `domain_id` / `name` / `owner` / `status`). A domain groups the
    concepts and intents a request's language may be governed under --
    Gate-A's `unsupported` status (Task 3) is exactly "no domain in this
    registry covers the request."""

    model_config = ConfigDict(extra="forbid")

    domain_id: str = Field(min_length=1, description="Non-empty, unique domain identifier.")
    name: str = Field(min_length=1, description="Human-readable domain name.")
    owner: str = Field(min_length=1, description="Team or role accountable for this domain's definitions.")
    status: LifecycleStatus


class Concept(BaseModel):
    """A governed Mode-A concept (`ontology_requirements.md`; Task 1's
    field list): the unit Gate-A's `matched_concepts` (Task 3) is built
    from. `synonyms` is what lets language like "vendor payment window"
    resolve to the same governed concept as "payment terms"
    (`gate_a_cases.json` GA-002) without the model being allowed to invent
    that mapping itself. `relationships` names other concepts this one is
    governed alongside (e.g. `CON-PAYMENT-TERMS` relates to
    `CON-SUPPLIER`) -- every id listed there must exist elsewhere in the
    same registry, enforced by `OntologyDocument`'s validator below, never
    left as an unchecked string."""

    model_config = ConfigDict(extra="forbid")

    concept_id: str = Field(min_length=1, description="Non-empty, unique concept identifier.")
    name: str = Field(min_length=1, description="Human-readable concept name.")
    description: str = Field(min_length=1, description="Governed meaning of this concept.")
    synonyms: list[str] = Field(default_factory=list, description="Alternate phrasings that resolve to this concept.")
    relationships: list[str] = Field(
        default_factory=list,
        description=(
            "concept_ids of other governed concepts this one relates to. Existence is "
            "validated at the registry level (`OntologyDocument`), not here."
        ),
    )
    owner: str = Field(min_length=1, description="Team or role accountable for this concept's definition.")
    status: LifecycleStatus


class Intent(BaseModel):
    """A governed Mode-A intent (`ontology_requirements.md`; Task 1's field
    list): what Gate-A resolves language onto (Task 3's `intent_id`) before
    the lane selector (Task 5) ever runs. `allowed_lanes` is the intent's
    own closed set of lanes it may ever route through -- the lane selector
    still decides *which one* for a given decision, but it can never pick a
    lane outside this list (`lane_policy.md`: "no arbitrary lane strings").
    `clarification_required_when_ambiguous` is read by Gate-A / Task 6:
    when true, a request that plausibly matches more than one governed
    intent alongside this one must clarify rather than guess.

    `phrases` records the intent's own governed reference phrasings
    (`gate_a_cases.json`'s exact/synonym cases resolve against these, via
    `Concept.synonyms`) -- present in the supplied fixture even though the
    assignment brief's minimum field list for an intent doesn't name it;
    kept as an ordinary field rather than dropped, since a field the
    registry fixture actually ships is not something this module may treat
    as an unchecked/unknown extra."""

    model_config = ConfigDict(extra="forbid")

    intent_id: str = Field(min_length=1, description="Non-empty, unique intent identifier.")
    domain: str = Field(min_length=1, description="domain_id of the domain this intent is governed under.")
    description: str = Field(min_length=1, description="What this intent represents.")
    phrases: list[str] = Field(default_factory=list, description="Governed reference phrasings for this intent.")
    allowed_lanes: list[LaneId] = Field(
        min_length=1,
        description="Closed set of lanes this intent may ever be routed through.",
    )
    clarification_required_when_ambiguous: bool
    status: LifecycleStatus


def _reject_duplicate_ids(kind: str, ids: list[str]) -> None:
    """Shared duplicate-id guard for `OntologyDocument`'s validator --
    raises on the first repeat rather than accumulating a set silently and
    losing which kind of record (`domain`/`concept`/`intent`) it was."""
    seen: set[str] = set()
    for identifier in ids:
        if identifier in seen:
            raise ValueError(f"duplicate {kind}_id: {identifier!r}")
        seen.add(identifier)


class OntologyDocument(BaseModel):
    """The full versioned, typed Mode-A ontology registry document
    (`ontology_requirements.md`; Task 1's top-level field list). Loaded and
    exposed read-only by Task 2's `ontology_registry.py`; nothing at
    runtime is permitted to construct or mutate one from request or model
    output (Day 9 working rules: "Model output cannot create a new
    ontology entry, intent, lane or policy").

    `lanes` is this ontology version's own *enabled* subset of the closed
    `LaneId` set -- a later registry version could in principle add a
    governed lane this one does not yet enable, so `Intent.allowed_lanes`
    is cross-checked against `lanes` below, not just against the `LaneId`
    enum by itself.
    """

    model_config = ConfigDict(extra="forbid")

    ontology_version: str = Field(min_length=1, description="Required governed ontology version identifier.")
    domains: list[Domain] = Field(default_factory=list)
    concepts: list[Concept] = Field(default_factory=list)
    intents: list[Intent] = Field(default_factory=list)
    lanes: list[LaneId] = Field(default_factory=list, description="Lane ids enabled for this ontology version.")

    @model_validator(mode="after")
    def _validate_registry_integrity(self) -> OntologyDocument:
        _reject_duplicate_ids("domain", [d.domain_id for d in self.domains])
        _reject_duplicate_ids("concept", [c.concept_id for c in self.concepts])
        _reject_duplicate_ids("intent", [i.intent_id for i in self.intents])

        domain_ids = {d.domain_id for d in self.domains}
        concept_ids = {c.concept_id for c in self.concepts}
        enabled_lanes = set(self.lanes)

        for concept in self.concepts:
            for target in concept.relationships:
                if target == concept.concept_id:
                    raise ValueError(f"concept {concept.concept_id!r} cannot relate to itself")
                if target not in concept_ids:
                    raise ValueError(
                        f"concept {concept.concept_id!r} has an unknown relationship target {target!r}"
                    )

        for intent in self.intents:
            if intent.domain not in domain_ids:
                raise ValueError(f"intent {intent.intent_id!r} references unknown domain {intent.domain!r}")
            unknown_lanes = [lane.value for lane in intent.allowed_lanes if lane not in enabled_lanes]
            if unknown_lanes:
                raise ValueError(
                    f"intent {intent.intent_id!r} references lane(s) not enabled for this ontology "
                    f"version: {unknown_lanes}"
                )

        return self
