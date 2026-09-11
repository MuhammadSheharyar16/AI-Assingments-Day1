"""
Day 11 Task 3 -- Gate-C policy: the governed evidence-quality policy.

`evidence_policy_requirements.md`'s "trusted source types / freshness
thresholds / required facets by intent / conflict policy / minimum
evidence requirement" (Task 3's five governed concepts) are what decides
*how much and what kind* of validated evidence a request needs before
Gate-C (Task 9) may return `allow` -- distinct from `source_registry.py`
(Task 2), which decides whether one candidate item's claimed source may be
trusted at all. This module is that policy: `IntentEvidenceRequirement`
(`rule_id` / `intent_id` / `request_kind` / `required_facets` /
`minimum_valid_items` / `allowed_source_types` / `conflict_policy`, Task
3's field list) is the typed shape of one governed evidence-quality rule;
`FreshnessPolicy` (`policy_id` / `max_age_hours`) is one governed
freshness threshold, referenced by `source_id` (Task 2's `SourceRecord.
freshness_policy_id`), not by intent. `GateCPolicyDocument` wraps both
under one versioned, typed document; `GateCPolicyRegistry` loads the
committed `policy/gate_c_policy.v1.json` through it and exposes it
read-only for Gate-C (Task 9) -- the identical model/registry split
`policy_models.py`/`policy_registry.py` draw for Gate-B, just folded into
one file here (like `source_registry.py`, Task 2) since Day 11's own
required structure has no separate file for it.

Required validation (`Day 11 Task.pdf`, TASK 3), each traced to where it
is enforced:

    - policy version required          -> `GateCPolicyDocument.
                                           policy_version` is a required,
                                           non-empty field.
    - unknown source reference
      rejected                        -> `IntentEvidenceRequirement.
                                           allowed_source_types` cross-
                                           checked against the real
                                           `SourceRegistry`'s governed
                                           `source_type`s -- see "Cross-
                                           references" below. (No field
                                           anywhere in the governed policy
                                           names a specific `source_id`;
                                           `allowed_source_types` -- Task
                                           3's own "trusted source types"
                                           governed concept -- is the only
                                           source reference this document
                                           carries.)
    - unknown intent rejected          -> `IntentEvidenceRequirement.
                                           intent_id` cross-checked against
                                           the real `OntologyRegistry`'s
                                           governed intents -- the
                                           identical `known_intent_ids`
                                           context mechanism `policy_
                                           models.py`'s `GateBPolicyDocument`
                                           already uses.
    - unknown freshness policy
      rejected                        -> the *reverse* direction: every
                                           governed source's (Task 2)
                                           `freshness_policy_id` must name
                                           a `FreshnessPolicy.policy_id`
                                           this document itself declares
                                           -- see "Cross-references" below.
    - duplicate policy IDs/rules
      rejected                        -> `GateCPolicyDocument` model_
                                           validator rejects a duplicate
                                           freshness `policy_id` and a
                                           duplicate `rule_id`;
                                           `GateCPolicyRegistry.__init__`
                                           additionally rejects two rules
                                           governing the same
                                           `(intent_id, request_kind)`
                                           pair, the ambiguous-combination
                                           check `policy_registry.py`'s
                                           `PolicyRegistry` already makes
                                           for Gate-B.
    - invalid status rejected          -> `GateCPolicyDocument.status` is
                                           typed `aico.control.ontology.
                                           LifecycleStatus` (reused, the
                                           same closed enum `GateBPolicy
                                           Document.status` already uses).
    - policy is read-only at runtime   -> no method here ever writes to
                                           the loaded document; every
                                           collection accessor returns a
                                           fresh `tuple`, never a
                                           reference into the document's
                                           own list -- the identical
                                           contract every other governed
                                           registry in this codebase
                                           gives.

## Cross-references

`GateCPolicyDocument` cross-checks against two *earlier*-built governed
resources, via the identical optional-Pydantic-validation-context pattern
`GateBPolicyDocument` uses for its own ontology cross-reference:
`known_intent_ids` (Day 9's `OntologyRegistry`) and `known_source_types`
(Task 2's `SourceRegistry`, `{s.source_type for s in registry.sources}`).
Both are the document's own fields (`intent_requirements[].intent_id`/
`allowed_source_types`) checked against externally supplied knowledge --
skipped when no context is supplied (a test building a throwaway document
in isolation), mandatory in practice once `GateCPolicyRegistry.load()`
resolves both real registries by default.

The freshness-policy check runs the *other* direction and cannot use that
same context mechanism: nothing on `GateCPolicyDocument` itself needs
checking against outside data here -- instead, Task 2's already-loaded
`SourceRegistry` (built before Gate-C policy existed, with no way to know
this document's `freshness_policies` set in advance) carries the
`freshness_policy_id` values that need checking against *this* document's
own declared set. That can only happen once both documents exist, so it is
a post-load check (`_validate_source_freshness_references`) run by
`GateCPolicyRegistry.load()` after `GateCPolicyDocument` itself parses
successfully -- not a `model_validator`, the same reason
`PolicyRegistry.__init__`'s ambiguous-rule-combination check is not one
either (it needs the already-built document plus, here, a second already-
loaded registry).
"""
from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator, model_validator

from aico.control.ontology import LifecycleStatus
from aico.control.ontology_registry import OntologyRegistry
from aico.evidence.errors import GateCPolicyLoadError, GateCPolicyLookupError
from aico.evidence.source_registry import SourceRegistry

# Relative to the process working directory, matching
# `source_registry.py`'s `DEFAULT_SOURCE_REGISTRY_PATH` /
# `policy_registry.py`'s `DEFAULT_POLICY_PATH` convention -- `uv run`
# always runs from the repository root.
DEFAULT_GATE_C_POLICY_PATH = Path("policy/gate_c_policy.v1.json")


class ConflictPolicy(str, Enum):
    """The closed set of governed conflict-resolution strategies this pack
    defines (`provenance_freshness_rules.md`: "Do not ask the model to
    decide which source should be trusted unless a deterministic governed
    precedence rule already determines the winner"). `AUTHORITY_THEN_
    REJECT_TIE` is the only strategy the supplied v1 policy governs:
    resolve a same-facet disagreement by each item's source
    `authority_level` (Task 2); when two same-authority items still
    disagree, reject rather than silently pick one (Task 8) -- never a
    model call to "decide which source looks better" (working rule)."""

    AUTHORITY_THEN_REJECT_TIE = "authority_then_reject_tie"


class FreshnessPolicy(BaseModel):
    """One governed freshness threshold. Referenced by `source_id` (Task
    2's `SourceRecord.freshness_policy_id`), not by intent -- Task 6's
    freshness check looks this up from the evidence item's own source, not
    from the request."""

    model_config = ConfigDict(extra="forbid")

    policy_id: str = Field(min_length=1, description="Non-empty, unique freshness-policy identifier.")
    max_age_hours: int = Field(
        gt=0, description="Maximum age, in hours, evidence governed by this policy may reach before it is stale (Task 6)."
    )


def _validate_non_blank_entries(value: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    for entry in value:
        if not entry.strip():
            raise ValueError(f"{field_name} entries must be non-empty")
    return value


class IntentEvidenceRequirement(BaseModel):
    """One governed evidence-quality rule (Task 3's field list): what
    facets must be covered, how many valid items are needed at minimum,
    which source types may back it, and how a same-facet conflict among
    its evidence is resolved. `request_kind` distinguishes more than one
    governed requirement under the same `intent_id` (the real fixture's
    `GC-R001`/`GC-R002` both govern `INT-POLICY-QUESTION`, for a narrower
    and a wider request shape) -- Gate-C (Task 9) is expected to match on
    the `(intent_id, request_kind)` pair via `GateCPolicyRegistry.
    find_rule()`, never `intent_id` alone.

    `required_facets` and `allowed_source_types` both require at least one
    entry (`min_length=1`, an extension beyond Task 3's bare field list,
    the same "a governed list of nothing isn't a requirement" reasoning
    `ontology.py`'s `Intent.allowed_lanes` already applies) -- a rule that
    requires zero facets or permits zero source types could never be a
    meaningful governed requirement."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1, description="Non-empty, unique rule identifier.")
    intent_id: str = Field(min_length=1, description="Governed Mode-A intent_id this requirement governs.")
    request_kind: str = Field(min_length=1, description="Narrower governed request shape under intent_id.")
    required_facets: tuple[str, ...] = Field(
        min_length=1, description="Governed facets evidence must cover for this request (Task 7)."
    )
    minimum_valid_items: int = Field(ge=1, description="Minimum count of validated evidence items required.")
    allowed_source_types: tuple[str, ...] = Field(
        min_length=1, description="Governed source_types (Task 2) permitted to back evidence for this request."
    )
    conflict_policy: ConflictPolicy

    @field_validator("required_facets")
    @classmethod
    def _validate_required_facets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_non_blank_entries(value, field_name="required_facets")

    @field_validator("allowed_source_types")
    @classmethod
    def _validate_allowed_source_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_non_blank_entries(value, field_name="allowed_source_types")


class GateCPolicyDocument(BaseModel):
    """The full versioned, typed Gate-C evidence-quality policy document.
    Loaded and exposed read-only by `GateCPolicyRegistry` below; nothing
    at runtime is permitted to construct or mutate one from request or
    model output -- the identical guarantee `OntologyDocument`/
    `GateBPolicyDocument`/`SourceRegistryDocument` give their own governed
    data."""

    model_config = ConfigDict(extra="forbid")

    policy_version: str = Field(min_length=1, description="Required governed Gate-C policy version identifier.")
    status: LifecycleStatus
    freshness_policies: tuple[FreshnessPolicy, ...] = Field(default_factory=tuple)
    intent_requirements: tuple[IntentEvidenceRequirement, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _validate_policy_integrity(self, info: ValidationInfo) -> GateCPolicyDocument:
        seen_freshness_ids: set[str] = set()
        for freshness_policy in self.freshness_policies:
            if freshness_policy.policy_id in seen_freshness_ids:
                raise ValueError(f"duplicate freshness policy_id: {freshness_policy.policy_id!r}")
            seen_freshness_ids.add(freshness_policy.policy_id)

        seen_rule_ids: set[str] = set()
        for rule in self.intent_requirements:
            if rule.rule_id in seen_rule_ids:
                raise ValueError(f"duplicate rule_id: {rule.rule_id!r}")
            seen_rule_ids.add(rule.rule_id)

        context = info.context or {}
        known_intent_ids: set[str] | None = context.get("known_intent_ids")
        if known_intent_ids is not None:
            for rule in self.intent_requirements:
                if rule.intent_id not in known_intent_ids:
                    raise ValueError(f"rule {rule.rule_id!r} references unknown ontology intent {rule.intent_id!r}")

        known_source_types: set[str] | None = context.get("known_source_types")
        if known_source_types is not None:
            for rule in self.intent_requirements:
                unknown = [t for t in rule.allowed_source_types if t not in known_source_types]
                if unknown:
                    raise ValueError(f"rule {rule.rule_id!r} references unknown source type(s): {unknown}")

        return self


def _validate_source_freshness_references(document: GateCPolicyDocument, source_registry: SourceRegistry) -> None:
    """Task 3's "unknown freshness policy rejected", the reverse-direction
    half of the cross-reference -- see module docstring's "Cross-
    references" section for why this cannot be a `model_validator`."""
    known_freshness_ids = {policy.policy_id for policy in document.freshness_policies}
    for source in source_registry.sources:
        if source.freshness_policy_id not in known_freshness_ids:
            raise GateCPolicyLoadError(
                f"source {source.source_id!r} references unknown freshness policy "
                f"{source.freshness_policy_id!r}"
            )


class GateCPolicyRegistry:
    """Read-only, typed access to one loaded Gate-C policy document.

    Construct via `GateCPolicyRegistry.load(...)` in production code
    (loads and validates the committed file, cross-checked against a
    governed ontology and source registry); tests may instead build a
    `GateCPolicyDocument` directly and pass it to the plain constructor --
    the constructor itself never touches disk, it only wraps an
    already-validated document and builds lookup indices over it (and, by
    construction, never runs the freshness cross-reference check -- that
    check needs a `SourceRegistry`, only available through `load()`).
    """

    def __init__(self, document: GateCPolicyDocument):
        self._document = document
        self._freshness_by_id: dict[str, FreshnessPolicy] = {p.policy_id: p for p in document.freshness_policies}
        self._rules_by_id: dict[str, IntentEvidenceRequirement] = {
            r.rule_id: r for r in document.intent_requirements
        }

        self._rules_by_combination: dict[tuple[str, str], IntentEvidenceRequirement] = {}
        for rule in document.intent_requirements:
            key = (rule.intent_id, rule.request_kind)
            existing = self._rules_by_combination.get(key)
            if existing is not None:
                raise GateCPolicyLoadError(
                    f"ambiguous policy: rules {existing.rule_id!r} and {rule.rule_id!r} both govern "
                    f"intent_id={rule.intent_id!r} request_kind={rule.request_kind!r}"
                )
            self._rules_by_combination[key] = rule

    # ── Loading ──────────────────────────────────────────────────────

    @classmethod
    def load(
        cls,
        path: str | Path = DEFAULT_GATE_C_POLICY_PATH,
        *,
        ontology_registry: OntologyRegistry | None = None,
        source_registry: SourceRegistry | None = None,
    ) -> GateCPolicyRegistry:
        """Load and validate the committed policy file at `path` (default
        `policy/gate_c_policy.v1.json` -- committed, read-only governed
        data; see `policy/README.md`). Raises `GateCPolicyLoadError` for
        anything wrong with the file itself: missing, unreadable, not
        valid JSON, failing `GateCPolicyDocument`'s typed validation
        (duplicate freshness `policy_id`, duplicate `rule_id`, a rule
        referencing an ungoverned intent or source type, invalid `status`,
        missing `policy_version`, ...), failing this registry's own
        ambiguous-rule-combination check, or a governed source (Task 2)
        referencing a freshness policy this document does not declare.
        Never falls back to an empty/default/permissive policy.

        `ontology_registry`/`source_registry` each default to the real
        committed `OntologyRegistry.load()`/`SourceRegistry.load()` --
        pass an explicit instance only to test against a different/
        throwaway document."""
        if ontology_registry is None:
            ontology_registry = OntologyRegistry.load()
        if source_registry is None:
            source_registry = SourceRegistry.load(ontology_registry=ontology_registry)

        resolved = Path(path)
        if not resolved.exists():
            raise GateCPolicyLoadError(f"Gate-C policy not found: {resolved}")
        try:
            raw_text = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            raise GateCPolicyLoadError(f"could not read Gate-C policy {resolved}: {exc}") from exc

        try:
            raw_data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise GateCPolicyLoadError(f"Gate-C policy {resolved} is not valid JSON: {exc}") from exc

        known_intent_ids = {intent.intent_id for intent in ontology_registry.intents}
        known_source_types = {source.source_type for source in source_registry.sources}
        try:
            document = GateCPolicyDocument.model_validate(
                raw_data,
                context={"known_intent_ids": known_intent_ids, "known_source_types": known_source_types},
            )
        except ValidationError as exc:
            raise GateCPolicyLoadError(f"Gate-C policy {resolved} failed validation: {exc}") from exc

        _validate_source_freshness_references(document, source_registry)

        return cls(document)

    # ── Active version ───────────────────────────────────────────────

    @property
    def policy_version(self) -> str:
        """The active governed Gate-C policy version this registry loaded
        -- included in every Gate-C decision (Task 9's `GateCDecision.
        policy_version`) and Task 14's observability metadata."""
        return self._document.policy_version

    @property
    def status(self) -> LifecycleStatus:
        return self._document.status

    # ── Read-only collection lookups ─────────────────────────────────

    @property
    def freshness_policies(self) -> tuple[FreshnessPolicy, ...]:
        """Every governed freshness policy, in policy order. A fresh
        tuple, not a reference into the loaded document's own tuple."""
        return tuple(self._document.freshness_policies)

    @property
    def intent_requirements(self) -> tuple[IntentEvidenceRequirement, ...]:
        """Every governed evidence-quality rule, in policy order."""
        return tuple(self._document.intent_requirements)

    # ── Resolve by id ────────────────────────────────────────────────

    def has_freshness_policy(self, policy_id: str) -> bool:
        return policy_id in self._freshness_by_id

    def get_freshness_policy(self, policy_id: str) -> FreshnessPolicy:
        """Resolve a governed `policy_id` to its typed `FreshnessPolicy`.
        Raises `GateCPolicyLookupError` for an id this policy does not
        govern -- never returns `None` or a synthesized default."""
        try:
            return self._freshness_by_id[policy_id]
        except KeyError:
            raise GateCPolicyLookupError("freshness_policy", policy_id) from None

    def has_rule(self, rule_id: str) -> bool:
        return rule_id in self._rules_by_id

    def get_rule(self, rule_id: str) -> IntentEvidenceRequirement:
        """Resolve a governed `rule_id` to its typed
        `IntentEvidenceRequirement`. Raises `GateCPolicyLookupError` for an
        id this policy does not govern."""
        try:
            return self._rules_by_id[rule_id]
        except KeyError:
            raise GateCPolicyLookupError("rule", rule_id) from None

    # ── Rule matching ────────────────────────────────────────────────

    def find_rule(self, intent_id: str, request_kind: str) -> IntentEvidenceRequirement | None:
        """Resolve the single governed `IntentEvidenceRequirement` (if
        any) matching one governed `intent_id` / `request_kind`
        combination -- the primitive Gate-C (Task 9) matches a request
        against. Returns `None` when no rule governs this combination at
        all; never guesses or falls back to a partial match. `load()`/
        `__init__` guarantee at most one rule can ever exist per
        combination, so this lookup is always unambiguous."""
        return self._rules_by_combination.get((intent_id, request_kind))
