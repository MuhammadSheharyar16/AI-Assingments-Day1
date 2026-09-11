"""
Day 11 Task 1 -- the typed evidence envelope.

Gate-C's whole premise is that retrieval success is not evidence validity
(`Day 11 Task.pdf`, "Build outcome": "A chunk being returned does not prove
that its source is trusted, current, complete or appropriate for the
request"). That premise only holds if what Gate-C actually evaluates is a
typed, self-validating shape -- never a raw dict retrieval/the protected
data adapter happened to return (working rule: "Do not pass unchecked
dictionaries into Gate-C"). This module is that shape, built from
`data/day11_pack/evidence_policy_requirements.md` and
`provenance_freshness_rules.md`.

Two models:

- `EvidenceItem` -- one candidate evidence record as actually returned for
  this request, carrying the minimum field list Task 1 names
  (`evidence_id` / `chunk_id` / `source_id` / `source_version` /
  `source_updated_at` / `retrieved_at` / `content_hash` / `tenant_id` /
  `data_classification` / `evidence_facets` / `content`), plus `claims`
  (added for Task 8, not part of Task 1's own minimum list -- see below).
  `chunk_id` is this envelope's stable provenance identifier -- Task 1's
  "missing provenance identifier" reject case is a missing/blank
  `chunk_id`, distinct from `content_hash` (integrity, Task 5) and
  `source_id`/`source_version` (registry identity, Task 2/4).
- `EvidencePackage` -- the package-level request context Task 1 also
  requires (`request_id` / `intent_id` / `lane` / `as_of` /
  `required_facets` / `items`): what was asked, against which governed
  intent/lane, as of what reference time, needing which facets, backed by
  which candidate items. `as_of` is the deterministic/injectable reference
  time Task 6's freshness policy is evaluated against -- never wall-clock
  `datetime.now()` (working rule).

`data_classification` reuses Day 10's own governed `DataClassification`
enum (`control/policy_models.py`) rather than a second, competing
vocabulary -- Gate-C must validate evidence classification against the
identical closed set Gate-B's effective scope is expressed in (Task 4:
"tenant/data classification stays within Gate-B effective scope"; Task 12:
"Gate-C must not widen Gate-B authorization"), so there is exactly one
governed classification vocabulary in this codebase, not two that could
silently drift apart. `lane` reuses Day 9's `LaneId` for the same reason:
the lane an evidence package was retrieved for is always one of the five
governed lanes `lane_selector.py` already produces, never a second,
ad hoc lane string.

Every model sets `extra="forbid"` (an unknown field is a malformed
envelope, not something silently dropped -- the same contract-boundary
convention `contracts/models.py` and `control/models.py` already use) and
uses Pydantic's `AwareDatetime` for every timestamp field, so a naive
datetime (no tzinfo) or an unparsable timestamp string is rejected by
Pydantic itself -- Task 1's "invalid timestamps" reject case -- before any
downstream freshness/provenance logic ever runs.

What this module deliberately does NOT do: it proves an envelope is
well-formed, not that it is trustworthy. Whether a `source_id` actually
exists in the governed source registry (Task 2), whether `content_hash`
matches the returned `content` (Task 5), whether the item is fresh enough
(Task 6) or complete enough (Task 7), and whether it conflicts with another
item (Task 8) are all later, separate boundaries -- deliberately not
duplicated here, the same shape/semantic split `contracts/models.py`
already draws for Day 4's typed output contract.

## `claims` (Task 8)

`gate_c_cases.json`'s own items carry a `claims` field (e.g. GC-001:
`{"supplier_identity": "Synthetic Supplier Alpha", "payment_terms": "net
30"}`) that Task 1 deliberately left out of the minimum field list --
conflict detection (Task 8) is the first thing that actually needs
per-facet *values* to compare across items, not merely which facets an
item covers (`evidence_facets`, already present since Task 1). Added here,
not as a separate Task 8 type, because it is a property of the returned
item itself (what it asserts), the same way `evidence_facets` is -- kept
optional (`default_factory=dict`) since not every item carries structured
per-facet claims (a prose-only item may cover a facet through its
`content` without asserting a single extractable value for it), and
additive: every Task 1-7 committed fixture/test predates this field and
none of them ever needed to set it, so its absence (an empty dict) is a
fully backward-compatible default, not a behavior change to anything
already built."""
from __future__ import annotations

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from aico.control.ontology import LaneId
from aico.control.policy_models import DataClassification
from aico.evidence.errors import EvidenceEnvelopeError


def _no_blank_entries(value: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    for entry in value:
        if not entry.strip():
            raise ValueError(f"{field_name} entries must be non-empty")
    return value


def _non_blank(value: str, *, field_name: str) -> str:
    """`min_length=1` alone accepts a whitespace-only string (`"   "`) as
    non-empty -- every identifier field below also rejects a value that is
    blank once stripped, so a padded-but-empty id cannot slip past Task 1's
    "missing provenance identifier"/"missing source ID" reject cases."""
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


class EvidenceItem(BaseModel):
    """One candidate evidence record as actually returned by retrieval /
    the protected data adapter for this request -- never a record the
    corpus merely happens to contain elsewhere (working rule: "Gate-C
    evaluates the actual evidence returned by retrieval"). See module
    docstring for the field-by-field rationale."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1, description="Unique id of this returned evidence record within its package.")
    chunk_id: str = Field(
        min_length=1,
        description="Stable chunk/record identifier -- this envelope's provenance identifier (Task 1/4).",
    )
    source_id: str = Field(min_length=1, description="Governed source_id this item claims to come from (Task 2/4).")
    source_version: str = Field(min_length=1, description="Version of the source this item was retrieved at (Task 4/5).")
    source_updated_at: AwareDatetime = Field(description="When the governed source itself was last updated (Task 6).")
    retrieved_at: AwareDatetime = Field(description="When retrieval/the protected data adapter returned this item.")
    content_hash: str = Field(
        min_length=1,
        description="This item's expected/asserted content hash, from the governed ingestion/source path (Task 5).",
    )
    tenant_id: str = Field(min_length=1, description="Tenant this item belongs to -- checked against Gate-B scope (Task 12).")
    data_classification: DataClassification = Field(
        description="Governed classification of this item's content -- checked against Gate-B scope (Task 12)."
    )
    evidence_facets: tuple[str, ...] = Field(
        default_factory=tuple, description="Governed facets this item's content actually covers (Task 7)."
    )
    content: str = Field(min_length=1, description="The evidence text itself.")
    claims: dict[str, str] = Field(
        default_factory=dict,
        description="Per-facet claimed values this item asserts, keyed by governed facet (Task 8's conflict-"
        "detection input). Empty for an item with no structured claims beyond its raw content.",
    )

    @field_validator("evidence_id", "chunk_id", "source_id", "source_version", "content_hash", "tenant_id")
    @classmethod
    def _validate_non_blank_identifiers(cls, value: str, info: ValidationInfo) -> str:
        return _non_blank(value, field_name=info.field_name)

    @field_validator("evidence_facets")
    @classmethod
    def _validate_evidence_facets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _no_blank_entries(value, field_name="evidence_facets")

    @field_validator("claims")
    @classmethod
    def _validate_claims(cls, value: dict[str, str]) -> dict[str, str]:
        for facet, claimed_value in value.items():
            if not facet.strip():
                raise ValueError("claims keys must be non-empty")
            if not claimed_value.strip():
                raise ValueError("claims values must be non-empty")
        return value


class EvidencePackage(BaseModel):
    """The package-level request context Task 1 also requires: what was
    asked, against which governed intent/lane, as of what deterministic
    reference time, needing which facets, backed by which candidate
    `EvidenceItem`s. See module docstring for the field-by-field
    rationale."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, description="Non-empty caller-supplied request id.")
    intent_id: str = Field(min_length=1, description="Governed intent_id this package was retrieved for.")
    lane: LaneId = Field(description="Governed lane this package was retrieved for -- always LaneId.RAG in practice.")
    as_of: AwareDatetime = Field(description="Deterministic/injectable reference time freshness (Task 6) is evaluated against.")
    required_facets: tuple[str, ...] = Field(
        default_factory=tuple, description="Governed facets this request needs covered (Task 7), from Gate-C policy."
    )
    items: tuple[EvidenceItem, ...] = Field(
        default_factory=tuple, description="Candidate evidence items actually returned for this request."
    )

    @field_validator("request_id", "intent_id")
    @classmethod
    def _validate_non_blank_identifiers(cls, value: str, info: ValidationInfo) -> str:
        return _non_blank(value, field_name=info.field_name)

    @field_validator("required_facets")
    @classmethod
    def _validate_required_facets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _no_blank_entries(value, field_name="required_facets")

    @model_validator(mode="after")
    def _validate_no_duplicate_evidence_ids(self) -> EvidencePackage:
        seen: set[str] = set()
        for item in self.items:
            if item.evidence_id in seen:
                raise ValueError(f"duplicate evidence_id in evidence package: {item.evidence_id!r}")
            seen.add(item.evidence_id)
        return self


def _field_path(exc: ValidationError) -> str | None:
    loc = exc.errors(include_url=False)[0]["loc"]
    return ".".join(str(part) for part in loc) if loc else None


def _first_error_message(exc: ValidationError) -> str:
    first = exc.errors(include_url=False)[0]
    field_path = _field_path(exc)
    prefix = f"{field_path}: " if field_path else ""
    return f"{prefix}{first['msg']}"


def parse_evidence_item(data: dict) -> EvidenceItem:
    """Boundary parser: a raw dict (as actually returned by retrieval /
    the protected data adapter) -> a typed `EvidenceItem`, or a sanitized
    `EvidenceEnvelopeError` -- never a raw `pydantic.ValidationError`
    escaping to a caller, and never an unchecked dict passed further into
    Gate-C (working rule: "Do not pass unchecked dictionaries into
    Gate-C")."""
    try:
        return EvidenceItem.model_validate(data)
    except ValidationError as exc:
        raise EvidenceEnvelopeError(_first_error_message(exc), field_path=_field_path(exc)) from exc


def parse_evidence_package(data: dict) -> EvidencePackage:
    """Boundary parser for a full candidate evidence package -- the entry
    point everything upstream of Gate-C (retrieval integration, Task 13)
    should call instead of handing Gate-C a raw dict. Delegates entirely to
    `EvidencePackage`/`EvidenceItem` (nothing here duplicates a validation
    rule those models already enforce) and translates Pydantic's
    `ValidationError` into one sanitized `EvidenceEnvelopeError`."""
    try:
        return EvidencePackage.model_validate(data)
    except ValidationError as exc:
        raise EvidenceEnvelopeError(_first_error_message(exc), field_path=_field_path(exc)) from exc
