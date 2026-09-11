"""
Day 11 Task 2 -- the governed source registry.

`evidence_policy_requirements.md`'s "source registry" is the governed
answer to "may Gate-C even consider evidence that claims to come from
this `source_id` at all" -- kept strictly separate from *this specific
item's* provenance (Task 4: does the returned record actually match what
the source says), freshness (Task 6) and completeness (Task 7). This
module owns that one boundary: `SourceRecord` (`registry_version` /
`source_id` / `source_type` / `authority_level` / `owner` / `status` /
`allowed_intents` / `supported_facets` / `freshness_policy_id`, Task 2's
field list) is the typed shape of one governed source; `SourceRegistry`
loads the committed `evidence/source_registry.v1.json` through it and
exposes it read-only -- the same split `ontology.py`/`ontology_registry.py`
and `policy_models.py`/`policy_registry.py` draw, just folded into one
file here since Day 11's own required structure gives evidence/ a single
file per concern rather than a model/registry pair.

Required behavior (`Day 11 Task.pdf`, TASK 2), each traced to where it is
enforced:

    - duplicate source ID rejected      -> `SourceRegistryDocument`
                                            model_validator.
    - unknown/disabled source cannot
      pass Gate-C                       -> `SourceRegistry.get_source()`
                                            raises `SourceRegistryLookupError`
                                            for an unknown id;
                                            `SourceRegistry.is_source_active()`
                                            folds "unknown" and "disabled"
                                            into one `False` a caller can
                                            check without a try/except, for
                                            exactly the fail-closed
                                            "cannot pass" outcome this
                                            bullet describes.
    - source intent compatibility
      is enforced                       -> `SourceRegistry.supports_intent()`
                                            (built on `SourceRecord.
                                            supports_intent()`); also
                                            cross-validated at load time --
                                            see "Ontology cross-reference"
                                            below.
    - source supported facets are
      enforced                          -> `SourceRegistry.supports_facet()`
                                            (built on `SourceRecord.
                                            supports_facet()`).
    - registry is read-only at
      runtime                           -> no method here ever writes to
                                            the loaded document; every
                                            collection accessor (`sources`)
                                            returns a fresh `tuple`, never a
                                            reference into the document's
                                            own list -- the identical
                                            contract `OntologyRegistry`/
                                            `PolicyRegistry` already give.
    - source-registry version is
      available for decision
      provenance                        -> `SourceRegistry.registry_version`
                                            -- Task 9's `GateCDecision`
                                            (`source_registry_version`) and
                                            Task 14's observability metadata
                                            are both expected to read this.

## Ontology cross-reference

Exactly like `policy_registry.py`'s Gate-B policy loading: `SourceRecord.
allowed_intents` must each reference a real governed Mode-A intent (Day
9's `OntologyRegistry`), not merely a non-empty string, but
`SourceRegistryDocument` itself intentionally does not import or depend on
a specific `OntologyRegistry` instance -- it reads an optional
`known_intent_ids: set[str]` out of Pydantic's own validation context, the
same `known_intent_ids` context key `GateBPolicyDocument` already uses (so
a caller who has both a Gate-B policy and a source registry to validate
resolves the ontology's intent ids once and reuses the same context dict
for both). `SourceRegistry.load()` always resolves an `OntologyRegistry`
first (the real committed `ontology/registry.v1.json` by default, or one a
caller supplies) and passes it, making the check mandatory in practice for
the one registry actually served to the rest of the system.

## Status

`SourceStatus` is deliberately closed to the two values the committed
fixture and `evidence_policy_requirements.md`'s failure table actually
name (`active` / `disabled`) -- unlike `ontology.py`'s `LifecycleStatus`
(which also has `deprecated`/`retired` for a domain/concept/intent that is
winding down but still referenced), a Gate-C source has no governed
"winding down but still consultable" state in this pack: it is either
usable or it is not. A status this closed set doesn't recognize (a typo,
or a future value not yet governed here) is rejected by Pydantic itself,
the same "invalid status enum" fail-closed behavior every other governed
document in this codebase gives.
"""
from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator, model_validator

from aico.control.ontology_registry import OntologyRegistry
from aico.evidence.errors import SourceRegistryLoadError, SourceRegistryLookupError

# Relative to the process working directory, matching
# `ontology_registry.py`'s `DEFAULT_REGISTRY_PATH` / `policy_registry.py`'s
# `DEFAULT_POLICY_PATH` convention -- `uv run` always runs from the
# repository root.
DEFAULT_SOURCE_REGISTRY_PATH = Path("evidence/source_registry.v1.json")


class SourceStatus(str, Enum):
    """The closed set of governed source lifecycle states this pack
    defines -- see module docstring's "Status" section for why this is
    narrower than `ontology.py`'s `LifecycleStatus`."""

    ACTIVE = "active"
    DISABLED = "disabled"


def _no_blank_entries(value: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    for entry in value:
        if not entry.strip():
            raise ValueError(f"{field_name} entries must be non-empty")
    return value


class SourceRecord(BaseModel):
    """One governed evidence source (Task 2's field list). `allowed_intents`
    is the closed set of governed intent ids this source may back evidence
    for at all (checked against the real ontology at load time -- see
    module docstring); `supported_facets` is the closed set of governed
    facets this source's content can ever satisfy (Task 7's completeness
    check may only credit a facet to an item whose *source* is actually
    governed to supply it)."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, description="Non-empty, unique governed source identifier.")
    source_type: str = Field(min_length=1, description="Governed category of this source, e.g. contract_record.")
    authority_level: int = Field(
        ge=0, description="Governed precedence used to resolve same-facet conflicts (Task 8) -- higher wins."
    )
    owner: str = Field(min_length=1, description="Team or role accountable for this source's governance.")
    status: SourceStatus
    allowed_intents: tuple[str, ...] = Field(
        default_factory=tuple, description="Governed intent_ids this source may back evidence for."
    )
    supported_facets: tuple[str, ...] = Field(
        default_factory=tuple, description="Governed facets this source's content can ever satisfy (Task 7)."
    )
    freshness_policy_id: str = Field(
        min_length=1, description="Governed freshness policy id this source's evidence is aged against (Task 6)."
    )

    @field_validator("allowed_intents")
    @classmethod
    def _validate_allowed_intents(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _no_blank_entries(value, field_name="allowed_intents")

    @field_validator("supported_facets")
    @classmethod
    def _validate_supported_facets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _no_blank_entries(value, field_name="supported_facets")

    @property
    def is_active(self) -> bool:
        """`False` for any status other than `SourceStatus.ACTIVE` --
        Task 2's "disabled source cannot pass Gate-C" bullet."""
        return self.status is SourceStatus.ACTIVE

    def supports_intent(self, intent_id: str) -> bool:
        """Task 2's "source intent compatibility is enforced" bullet, for
        one already-resolved record."""
        return intent_id in self.allowed_intents

    def supports_facet(self, facet: str) -> bool:
        """Task 2's "source supported facets are enforced" bullet, for one
        already-resolved record."""
        return facet in self.supported_facets


class SourceRegistryDocument(BaseModel):
    """The full versioned, typed governed source registry document. Loaded
    and exposed read-only by `SourceRegistry` below; nothing at runtime is
    permitted to construct or mutate one from request or model output --
    the identical guarantee `OntologyDocument`/`GateBPolicyDocument` give
    their own governed data."""

    model_config = ConfigDict(extra="forbid")

    registry_version: str = Field(min_length=1, description="Required governed source-registry version identifier.")
    sources: tuple[SourceRecord, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _validate_registry_integrity(self, info: ValidationInfo) -> SourceRegistryDocument:
        seen: set[str] = set()
        for source in self.sources:
            if source.source_id in seen:
                raise ValueError(f"duplicate source_id: {source.source_id!r}")
            seen.add(source.source_id)

        context = info.context or {}
        known_intent_ids: set[str] | None = context.get("known_intent_ids")
        if known_intent_ids is not None:
            for source in self.sources:
                unknown = [i for i in source.allowed_intents if i not in known_intent_ids]
                if unknown:
                    raise ValueError(
                        f"source {source.source_id!r} references unknown intent(s): {unknown}"
                    )

        return self


class SourceRegistry:
    """Read-only, typed access to one loaded governed source registry.

    Construct via `SourceRegistry.load(...)` in production code (loads and
    validates the committed file, cross-checked against a governed
    ontology); tests may instead build a `SourceRegistryDocument` directly
    and pass it to the plain constructor -- the constructor itself never
    touches disk, it only wraps an already-validated document and builds
    a lookup index over it.
    """

    def __init__(self, document: SourceRegistryDocument):
        self._document = document
        self._sources_by_id: dict[str, SourceRecord] = {s.source_id: s for s in document.sources}

    # ── Loading ──────────────────────────────────────────────────────

    @classmethod
    def load(
        cls,
        path: str | Path = DEFAULT_SOURCE_REGISTRY_PATH,
        *,
        ontology_registry: OntologyRegistry | None = None,
    ) -> SourceRegistry:
        """Load and validate the committed registry file at `path`
        (default `evidence/source_registry.v1.json` -- committed,
        read-only governed data; see `evidence/README.md`). Raises
        `SourceRegistryLoadError` for anything wrong with the file itself:
        missing, unreadable, not valid JSON, failing
        `SourceRegistryDocument`'s typed validation (duplicate
        `source_id`, an `allowed_intents` entry naming an ungoverned
        intent, invalid `status`, missing `registry_version`, ...), or
        failing this registry's own integrity check. Never falls back to
        an empty/default/permissive registry.

        `ontology_registry` defaults to `OntologyRegistry.load()` (the
        real committed Mode-A ontology) -- pass an explicit instance only
        to test against a different/throwaway ontology document."""
        if ontology_registry is None:
            ontology_registry = OntologyRegistry.load()

        resolved = Path(path)
        if not resolved.exists():
            raise SourceRegistryLoadError(f"source registry not found: {resolved}")
        try:
            raw_text = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            raise SourceRegistryLoadError(f"could not read source registry {resolved}: {exc}") from exc

        try:
            raw_data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise SourceRegistryLoadError(f"source registry {resolved} is not valid JSON: {exc}") from exc

        known_intent_ids = {intent.intent_id for intent in ontology_registry.intents}
        try:
            document = SourceRegistryDocument.model_validate(raw_data, context={"known_intent_ids": known_intent_ids})
        except ValidationError as exc:
            raise SourceRegistryLoadError(f"source registry {resolved} failed validation: {exc}") from exc

        return cls(document)

    # ── Active version ───────────────────────────────────────────────

    @property
    def registry_version(self) -> str:
        """The active governed source-registry version this registry
        loaded -- Task 2's "source-registry version is available for
        decision provenance" bullet, read by Task 9's `GateCDecision`
        (`source_registry_version`) and Task 14's observability metadata."""
        return self._document.registry_version

    # ── Read-only collection lookups ─────────────────────────────────

    @property
    def sources(self) -> tuple[SourceRecord, ...]:
        """Every governed source, in registry order. A fresh tuple, not a
        reference into the loaded document's own list."""
        return tuple(self._document.sources)

    # ── Resolve by id ────────────────────────────────────────────────

    def has_source(self, source_id: str) -> bool:
        return source_id in self._sources_by_id

    def get_source(self, source_id: str) -> SourceRecord:
        """Resolve a governed `source_id` to its typed `SourceRecord`.
        Raises `SourceRegistryLookupError` for an id this registry does
        not govern -- never returns `None` or a synthesized default."""
        try:
            return self._sources_by_id[source_id]
        except KeyError:
            raise SourceRegistryLookupError("source", source_id) from None

    # ── Task 2 "Required behavior" predicates ───────────────────────

    def is_source_active(self, source_id: str) -> bool:
        """`False` for both an unknown `source_id` and a known-but-disabled
        one -- Task 2's "unknown/disabled source cannot pass Gate-C"
        bullet folded into the one boolean a caller (Gate-C, Task 9)
        checks before considering the source further, without needing a
        try/except around `get_source()` just to find out."""
        record = self._sources_by_id.get(source_id)
        return record is not None and record.is_active

    def supports_intent(self, source_id: str, intent_id: str) -> bool:
        """Task 2's "source intent compatibility is enforced" bullet.
        Raises `SourceRegistryLookupError` for an unknown `source_id` --
        callers are expected to have already checked `is_source_active()`
        (or to want the lookup failure to propagate) before asking whether
        a known source supports a given intent."""
        return self.get_source(source_id).supports_intent(intent_id)

    def supports_facet(self, source_id: str, facet: str) -> bool:
        """Task 2's "source supported facets are enforced" bullet. Raises
        `SourceRegistryLookupError` for an unknown `source_id`, the same
        contract `supports_intent()` gives."""
        return self.get_source(source_id).supports_facet(facet)
