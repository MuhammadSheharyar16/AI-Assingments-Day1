"""
Day 11 Task 4 -- provenance validation.
Day 11 Task 5 -- integrity / mutation check.

Gate-C's critical rule (`Day 11 Task.pdf`, Task 4): do not validate "the
corpus contains a valid chunk somewhere" -- validate "the evidence item
actually returned to this request is traceable and valid." This module
owns that boundary: it never looks at the corpus, an index, or any source
other than the `EvidenceItem`s an `EvidencePackage` (Task 1) actually
carries, checked against the two things allowed to *govern* whether they
may be trusted -- Task 2's `SourceRegistry` and Day 10's `GateBDecision`.

Task 4's five "At minimum prove" bullets, each traced to where it is
enforced:

    - evidence source_id exists        -> `source_registry.has_source()`.
                                           Deliberately existence only --
                                           whether that source is *active*
                                           is Task 2's own "unknown/
                                           disabled source cannot pass
                                           Gate-C" bullet
                                           (`SourceRegistry.is_source_active()`),
                                           not re-implemented here; Gate-C
                                           (Task 9) composes both.
    - content hash matches the
      returned content                 -> `stable_content_hash(item.content)
                                           == item.content_hash` -- the
                                           validator recomputes the
                                           *current* hash from what was
                                           actually returned and compares
                                           it against the hash the item
                                           itself carries (Task 5's "the
                                           validator may compute the
                                           current content hash for
                                           comparison"). Catches "content
                                           changed but old hash retained"
                                           directly: if `content` was
                                           mutated after the hash was set,
                                           a fresh hash of the mutated text
                                           will not match the stale value.
    - tenant/data classification
      stays within Gate-B effective
      scope                            -> `item.tenant_id` checked against
                                           `gate_b_decision.
                                           effective_tenant_scope`,
                                           `item.data_classification`
                                           against `gate_b_decision.
                                           effective_data_classes` -- the
                                           real Day 10 `GateBDecision`
                                           Gate-C is handed (working rule:
                                           "Gate-C must not widen Gate-B
                                           authorization scope"; Task 12).
                                           A non-`ALLOW` decision carries
                                           empty effective scopes by
                                           construction (`GateBDecision`'s
                                           own docstring), so every item
                                           fails this check automatically
                                           in that case -- fail closed,
                                           not a special case here.
    - evidence source_version matches
      the governed/returned source
      version                          -> see "Source version" below.
    - evidence record/chunk carries
      stable identity                  -> see "Stable identity" below.

## Source version

`SourceRecord` (Task 2) deliberately carries no single "current version"
field -- a governed source is identified by `source_id` plus its status/
authority/policy, not pinned to one canonical version, since two different,
both-legitimately-governed evidence items can validly cite the same source
at different historical versions. There is therefore no external per-
source "ground truth version" this module could compare `EvidenceItem.
source_version` against.

What this module *can* and does check: within one `EvidencePackage`,
retrieval returning two items that both claim `source_id="SRC-X"` but
disagree about `source_version` is retrieval contradicting itself about
what state the source was actually in for this one request -- the exact
"broken provenance" Task 4 names, catchable only at the package level.
`validate_provenance()` therefore checks cross-item source-version
consistency across the whole package, flagging every item on either side
of a disagreement, not merely comparing one item against a per-field
constraint Task 1's envelope already enforces (non-blank `source_version`
is already guaranteed there; this module's job starts where that ends).

## Stable identity

Task 1's envelope already guarantees `chunk_id` is present and non-blank
(Task 1's "provenance identifier"). What "stable identity" adds beyond
that: two items in the same package claiming the identical `chunk_id` must
actually agree on what that identity *means* -- the same `source_id` and
the same `content_hash`. Two items sharing a `chunk_id` while disagreeing
on either is the same "retrieval contradicting itself" failure the source-
version check catches, one field over: the "identity" is not stable if it
resolves to two different records depending on which item you look at.
Checked at the package level, alongside source-version consistency.

`gate_c_cases.json`'s fixture hash values (`expected_content_hash`/
`provided_content_hash`) are documented as synthetic placeholders, not
real hashes of their accompanying `content` text (the pack's own note:
"implementation may map them into its normal hash/provenance
representation") -- this module's tests build `EvidenceItem`s whose
`content_hash` is the real `stable_content_hash()` of their `content` for
cases the fixture expects to pass, and a deliberately non-matching value
for cases it expects to fail on integrity grounds, rather than feeding the
fixture's own placeholder strings through a real hash comparison.

## Task 5 -- integrity / mutation check

`validate_provenance()` (Task 4) proves an item is *self-consistent* --
its own `content_hash` matches a fresh hash of its own `content`. That
catches naive corruption but not a coordinated forgery: an attacker (or a
bug) that mutates `content` *and* recomputes `content_hash` to match would
sail through a self-consistency check undetected. Task 5's own words are
the tell: "the evidence must carry/derive its expected provenance value
from the governed ingestion/source path" -- the *expected* value has to
come from somewhere the returned item itself cannot influence, exactly
`gate_c_cases.json`'s `expected_content_hash` (what governed ingestion
actually recorded) versus `provided_content_hash` (whatever the returned
item happens to carry) split.

`validate_integrity()` is that stronger, independent check:
`GovernedProvenanceRecord` (`source_id` / `chunk_id` / `source_version` /
`content_hash`) is what the governed ingestion/source path recorded for
one evidence record, resolved by `GovernedProvenanceIndex` -- an
in-memory lookup a caller populates from wherever real governed
provenance metadata lives (this pack ships no committed provenance-index
file, unlike the source registry/Gate-C policy, so there is nothing to
load from disk here; Task 1's own docstring already anticipated this:
"If your ingestion pipeline already produces stronger provenance
metadata, preserve and validate it"). Every governed source's own hash/
version, once resolved, is compared against the *returned* item on two
independent axes, plus the internal self-consistency check Task 4 already
performs:

    - unchanged content + same
      version -> integrity passes   -> all three below hold: the item is
                                        self-consistent AND matches the
                                        governed record on both hash and
                                        version.
    - content changed but old hash
      retained -> fail              -> `stable_content_hash(item.content)
                                        != item.content_hash` (the same
                                        self-consistency check Task 4
                                        performs, reused rather than
                                        duplicated).
    - source version mismatch
      -> fail                       -> `item.source_version !=
                                        expected.source_version`.
    - missing hash -> fail          -> "hash" here is the *expected*
                                        governed hash, not the item's own
                                        `content_hash` (Task 1's envelope
                                        already makes that field required
                                        and non-blank, so it can never
                                        itself be "missing" by the time it
                                        reaches this module) -- no
                                        `GovernedProvenanceRecord` found
                                        for this item at all fails closed
                                        (`MISSING_EXPECTED_RECORD`)
                                        rather than falling back to
                                        Task 4's self-consistency result
                                        or any other silent default.

The explicit anti-pattern Task 5 names -- "Do not silently recompute a
missing/incorrect expected hash and then treat the item as valid" -- is
why `validate_integrity()` never invents a `GovernedProvenanceRecord`
when `GovernedProvenanceIndex.get()` returns `None`: a missing governed
reference is exactly as fatal as a mismatched one, never treated as "no
opinion, so allow."
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from aico.control.models import GateBDecision
from aico.evidence.models import EvidenceItem, EvidencePackage
from aico.evidence.source_registry import SourceRegistry


def stable_content_hash(content: str) -> str:
    """The one governed way to hash evidence content in this codebase --
    identical scheme to `retrieval/chunker.py`'s own `_content_hash()`
    (full SHA-256 hex digest of the UTF-8 encoded text), so a real
    ingestion pipeline's hash and this validator's recomputation are
    directly comparable, not two independently-invented schemes that
    happen to both be called "hash"."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class ProvenanceFailureReason(str, Enum):
    """The closed set of reasons `validate_provenance()` ever cites for
    marking one evidence item invalid -- Gate-C (Task 9) is expected to
    fold these into its own typed `reason_codes`, never invent a new,
    undocumented provenance failure string."""

    UNKNOWN_SOURCE = "unknown_source"
    CONTENT_HASH_MISMATCH = "content_hash_mismatch"
    TENANT_OUT_OF_SCOPE = "tenant_out_of_scope"
    DATA_CLASSIFICATION_OUT_OF_SCOPE = "data_classification_out_of_scope"
    SOURCE_VERSION_MISMATCH = "source_version_mismatch"
    UNSTABLE_IDENTITY = "unstable_identity"


class ProvenanceItemResult(BaseModel):
    """Provenance's per-item verdict: `evidence_id` this result is for,
    whether it passed every Task 4 check, and -- when it did not -- every
    reason it failed (not just the first one Gate-C might otherwise have
    to re-derive by re-running checks itself)."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    valid: bool
    reasons: tuple[ProvenanceFailureReason, ...] = Field(default_factory=tuple)


class ProvenanceReport(BaseModel):
    """The full package-level provenance result: which evidence_ids
    passed, which were rejected, and the per-item detail behind that
    split -- `validate_provenance()`'s one return value, the shape Gate-C
    (Task 9) is expected to consume rather than calling the per-item
    checks itself."""

    model_config = ConfigDict(extra="forbid")

    validated_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    rejected_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    item_results: tuple[ProvenanceItemResult, ...] = Field(default_factory=tuple)

    def result_for(self, evidence_id: str) -> ProvenanceItemResult:
        """Resolve one item's result by `evidence_id`. Raises `KeyError`
        for an id this report has no result for (a package it was not
        built from) -- never returns `None` or a synthesized default,
        matching every other typed lookup in this codebase."""
        for result in self.item_results:
            if result.evidence_id == evidence_id:
                return result
        raise KeyError(evidence_id)


def _check_source_exists(item: EvidenceItem, source_registry: SourceRegistry) -> ProvenanceFailureReason | None:
    if not source_registry.has_source(item.source_id):
        return ProvenanceFailureReason.UNKNOWN_SOURCE
    return None


def _check_content_hash(item: EvidenceItem) -> ProvenanceFailureReason | None:
    if stable_content_hash(item.content) != item.content_hash:
        return ProvenanceFailureReason.CONTENT_HASH_MISMATCH
    return None


def _check_gate_b_scope(item: EvidenceItem, gate_b_decision: GateBDecision) -> tuple[ProvenanceFailureReason, ...]:
    reasons: list[ProvenanceFailureReason] = []
    if item.tenant_id not in gate_b_decision.effective_tenant_scope:
        reasons.append(ProvenanceFailureReason.TENANT_OUT_OF_SCOPE)
    if item.data_classification not in gate_b_decision.effective_data_classes:
        reasons.append(ProvenanceFailureReason.DATA_CLASSIFICATION_OUT_OF_SCOPE)
    return tuple(reasons)


def _find_source_version_conflicts(items: tuple[EvidenceItem, ...]) -> frozenset[str]:
    """`evidence_id`s of every item whose `source_version` disagrees with
    another item sharing the same `source_id` -- see module docstring's
    "Source version" section."""
    version_by_source: dict[str, str] = {}
    conflicting_sources: set[str] = set()
    for item in items:
        seen_version = version_by_source.get(item.source_id)
        if seen_version is None:
            version_by_source[item.source_id] = item.source_version
        elif seen_version != item.source_version:
            conflicting_sources.add(item.source_id)
    return frozenset(item.evidence_id for item in items if item.source_id in conflicting_sources)


def _find_unstable_identity_conflicts(items: tuple[EvidenceItem, ...]) -> frozenset[str]:
    """`evidence_id`s of every item whose `chunk_id` is shared with
    another item that disagrees on `source_id`/`content_hash` -- see
    module docstring's "Stable identity" section."""
    identity_by_chunk: dict[str, tuple[str, str]] = {}
    conflicting_chunks: set[str] = set()
    for item in items:
        identity = (item.source_id, item.content_hash)
        seen_identity = identity_by_chunk.get(item.chunk_id)
        if seen_identity is None:
            identity_by_chunk[item.chunk_id] = identity
        elif seen_identity != identity:
            conflicting_chunks.add(item.chunk_id)
    return frozenset(item.evidence_id for item in items if item.chunk_id in conflicting_chunks)


def validate_provenance(
    package: EvidencePackage,
    *,
    source_registry: SourceRegistry,
    gate_b_decision: GateBDecision,
) -> ProvenanceReport:
    """Validate every item `package` actually carries against Task 4's
    five bullets -- never the corpus, never a source's identity/status
    beyond what `source_registry` governs, never anything wider than
    `gate_b_decision`'s own effective scope. Returns a full
    `ProvenanceReport`; does not raise for an individual item's failure
    (an invalid item is a normal, typed outcome here -- exactly like
    Gate-A/Gate-B's own allow/deny/clarify results are typed outcomes, not
    exceptions) and never mutates `package`/`source_registry`/
    `gate_b_decision`."""
    version_conflicts = _find_source_version_conflicts(package.items)
    identity_conflicts = _find_unstable_identity_conflicts(package.items)

    item_results: list[ProvenanceItemResult] = []
    validated_ids: list[str] = []
    rejected_ids: list[str] = []

    for item in package.items:
        reasons: list[ProvenanceFailureReason] = []

        source_reason = _check_source_exists(item, source_registry)
        if source_reason is not None:
            reasons.append(source_reason)

        hash_reason = _check_content_hash(item)
        if hash_reason is not None:
            reasons.append(hash_reason)

        reasons.extend(_check_gate_b_scope(item, gate_b_decision))

        if item.evidence_id in version_conflicts:
            reasons.append(ProvenanceFailureReason.SOURCE_VERSION_MISMATCH)
        if item.evidence_id in identity_conflicts:
            reasons.append(ProvenanceFailureReason.UNSTABLE_IDENTITY)

        valid = not reasons
        item_results.append(ProvenanceItemResult(evidence_id=item.evidence_id, valid=valid, reasons=tuple(reasons)))
        (validated_ids if valid else rejected_ids).append(item.evidence_id)

    return ProvenanceReport(
        validated_evidence_ids=tuple(validated_ids),
        rejected_evidence_ids=tuple(rejected_ids),
        item_results=tuple(item_results),
    )


# ===========================================================================
# Task 5 -- integrity / mutation check
# ===========================================================================


class GovernedProvenanceRecord(BaseModel):
    """What the governed ingestion/source path recorded for one evidence
    record -- Task 5's "expected provenance value" -- independent of
    whatever the returned `EvidenceItem` claims about itself. Resolved by
    `source_id` + `chunk_id`, never derived from the returned item's own
    (possibly mutated) `content`/`content_hash`."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)


class GovernedProvenanceIndex:
    """A read-only, in-memory lookup from `(source_id, chunk_id)` to the
    `GovernedProvenanceRecord` a caller has resolved for it. Not a
    committed/loaded-from-disk registry like `SourceRegistry`/
    `GateCPolicyRegistry` -- this pack ships no committed provenance-index
    file, so there is nothing to `load()`; a caller (a test, or a future
    real ingestion adapter) populates one from whatever governed
    provenance metadata it actually has. `get()` returns `None` for an
    id this index carries no record for -- `validate_integrity()` treats
    that as `MISSING_EXPECTED_RECORD`, never as "no opinion, so allow"
    (Task 5's own anti-pattern warning)."""

    def __init__(self, records: Iterable[GovernedProvenanceRecord] = ()):
        self._records: dict[tuple[str, str], GovernedProvenanceRecord] = {
            (record.source_id, record.chunk_id): record for record in records
        }

    def get(self, source_id: str, chunk_id: str) -> GovernedProvenanceRecord | None:
        return self._records.get((source_id, chunk_id))


class IntegrityFailureReason(str, Enum):
    """The closed set of reasons `validate_integrity()` ever cites for
    marking one evidence item invalid."""

    CONTENT_HASH_MISMATCH = "content_hash_mismatch"
    EXPECTED_HASH_MISMATCH = "expected_hash_mismatch"
    SOURCE_VERSION_MISMATCH = "source_version_mismatch"
    MISSING_EXPECTED_RECORD = "missing_expected_record"


class IntegrityResult(BaseModel):
    """Integrity's per-item verdict -- the `IntegrityFailureReason` analog
    of `ProvenanceItemResult`."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    valid: bool
    reasons: tuple[IntegrityFailureReason, ...] = Field(default_factory=tuple)


class IntegrityReport(BaseModel):
    """The full package-level integrity result -- the `IntegrityResult`
    analog of `ProvenanceReport`."""

    model_config = ConfigDict(extra="forbid")

    validated_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    rejected_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    item_results: tuple[IntegrityResult, ...] = Field(default_factory=tuple)

    def result_for(self, evidence_id: str) -> IntegrityResult:
        """Resolve one item's result by `evidence_id`. Raises `KeyError`
        for an id this report has no result for, matching
        `ProvenanceReport.result_for()`'s identical contract."""
        for result in self.item_results:
            if result.evidence_id == evidence_id:
                return result
        raise KeyError(evidence_id)


def _validate_item_integrity(item: EvidenceItem, expected: GovernedProvenanceRecord | None) -> IntegrityResult:
    if expected is None:
        return IntegrityResult(
            evidence_id=item.evidence_id,
            valid=False,
            reasons=(IntegrityFailureReason.MISSING_EXPECTED_RECORD,),
        )

    reasons: list[IntegrityFailureReason] = []

    if stable_content_hash(item.content) != item.content_hash:
        reasons.append(IntegrityFailureReason.CONTENT_HASH_MISMATCH)

    if item.content_hash != expected.content_hash:
        reasons.append(IntegrityFailureReason.EXPECTED_HASH_MISMATCH)

    if item.source_version != expected.source_version:
        reasons.append(IntegrityFailureReason.SOURCE_VERSION_MISMATCH)

    return IntegrityResult(evidence_id=item.evidence_id, valid=not reasons, reasons=tuple(reasons))


def validate_integrity(package: EvidencePackage, *, provenance_index: GovernedProvenanceIndex) -> IntegrityReport:
    """Validate every item `package` carries against Task 5's four
    required behaviors, each checked against an independent governed
    reference (`provenance_index`) rather than only the item's own
    self-reported fields -- see module docstring's "Task 5" section for
    why this is a stronger check than `validate_provenance()`'s
    self-consistency check alone, and why a missing governed reference
    fails closed rather than falling back to that weaker check. Returns
    one `IntegrityReport`; never raises for an individual item's failure,
    never mutates `package`/`provenance_index`."""
    item_results = tuple(
        _validate_item_integrity(item, provenance_index.get(item.source_id, item.chunk_id)) for item in package.items
    )
    return IntegrityReport(
        validated_evidence_ids=tuple(r.evidence_id for r in item_results if r.valid),
        rejected_evidence_ids=tuple(r.evidence_id for r in item_results if not r.valid),
        item_results=item_results,
    )
