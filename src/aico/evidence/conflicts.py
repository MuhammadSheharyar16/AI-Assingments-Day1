"""
Day 11 Task 8 -- conflict detection.

`provenance_freshness_rules.md`'s conflict rule is the whole spec: "If two
valid same-authority sources provide incompatible values for the same
facet, the conflict must be surfaced. Do not ask the model to decide which
source should be trusted unless a deterministic governed precedence rule
already determines the winner." This module owns exactly that decision,
built in two layers matching `freshness.py`'s own split:

- `evaluate_conflict()` -- the pure, deterministic core `data/day11_pack/
  fixtures/conflict_cases.json` is shaped for: one governed facet, the
  claims made about it (`evidence_id` / `authority` / `value`), and the
  governed `ConflictPolicy` (Task 3) to resolve under -- in, one
  `ConflictResolution` out. No `EvidenceItem`, no registry lookup -- just
  the decision, unit-testable against the fixture's own three cases
  directly.
- `validate_conflicts()` -- the package-level entry point Gate-C (Task 9)
  is expected to call: groups every *valid* item's `EvidenceItem.claims`
  (Task 1's field added for this task) by facet, resolves each facet's
  authority from Task 2's `SourceRegistry` (never from the item itself --
  an item cannot assert its own trustworthiness), and runs
  `evaluate_conflict()` once per facet that actually has more than one
  claim.

## Required behavior, each traced to where it is enforced

    - non-conflicting evidence
      passes conflict check           -> `evaluate_conflict()`: a single
                                          distinct claimed value (however
                                          many items assert it) ->
                                          `ConflictOutcome.NO_CONFLICT`.
    - exact duplicate/supporting
      evidence is not a conflict      -> the same check -- two items
                                          asserting the identical value for
                                          a facet collapse to one distinct
                                          value, not two claims to
                                          reconcile (`conflict_cases.json`
                                          CONFLICT-001).
    - contradictory same-authority
      evidence is detected            -> when more than one distinct value
                                          is claimed and the highest
                                          authority is shared by more than
                                          one of them (a tie at the top),
                                          `ConflictOutcome.
                                          UNRESOLVED_CONFLICT`
                                          (CONFLICT-002) -- a tie is not
                                          resolved by picking either side.
    - configured authority precedence
      may resolve a conflict only
      when explicitly governed        -> resolution is only ever attempted
                                          for a `ConflictPolicy` this
                                          function actually recognizes
                                          (today, only `AUTHORITY_THEN_
                                          REJECT_TIE`, the sole strategy
                                          `policy.py` governs) -- and even
                                          then, only when exactly one
                                          distinct value holds the single
                                          highest authority
                                          (CONFLICT-003). A future governed
                                          policy value this function does
                                          not recognize falls through to
                                          `UNRESOLVED_CONFLICT` rather than
                                          silently applying authority
                                          precedence anyway.
    - unresolved conflict cannot
      silently proceed to the model
      as if evidence were consistent  -> `validate_conflicts()`'s
                                          `has_unresolved_conflict`/
                                          `unresolved_facets` are read by
                                          Gate-C (Task 9) before ever
                                          reaching the Model Gateway --
                                          this module itself never calls
                                          it, has no code path that could.

"The model must not be asked to 'pick whichever source looks better'" is
structural here too: neither `evaluate_conflict()` nor
`validate_conflicts()` has a parameter for a model call, a prompt, or a
generated preference -- the only inputs are governed authority (Task 2)
and the governed policy (Task 3).
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from aico.evidence.models import EvidencePackage
from aico.evidence.policy import ConflictPolicy
from aico.evidence.source_registry import SourceRegistry


class ConflictClaim(BaseModel):
    """One item's asserted value for one governed facet -- the shape
    `conflict_cases.json`'s own `items` entries are already in
    (`evidence_id` / `authority` / `value`)."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    authority: int = Field(ge=0, description="The claiming item's own source's governed authority_level (Task 2).")
    value: str = Field(min_length=1)


class ConflictOutcome(str, Enum):
    """The closed set of outcomes `evaluate_conflict()` can return."""

    NO_CONFLICT = "no_conflict"
    RESOLVED_BY_GOVERNED_AUTHORITY = "resolved_by_governed_authority"
    UNRESOLVED_CONFLICT = "unresolved_conflict"


class ConflictResolution(BaseModel):
    """One facet's conflict verdict: the outcome, the winning value when
    one was determined, and every `evidence_id` that took part in the
    underlying disagreement (empty when there was none)."""

    model_config = ConfigDict(extra="forbid")

    facet: str = Field(min_length=1)
    outcome: ConflictOutcome
    winning_value: str | None = Field(default=None, description="Populated only for NO_CONFLICT/RESOLVED_BY_GOVERNED_AUTHORITY.")
    conflicting_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)


def evaluate_conflict(facet: str, claims: Sequence[ConflictClaim], *, policy: ConflictPolicy) -> ConflictResolution:
    """Resolve one facet's claims deterministically. Never raises for an
    empty `claims` sequence (a facet nobody made a claim about is simply
    `NO_CONFLICT` with no winner) and never mutates `claims`."""
    if not claims:
        return ConflictResolution(facet=facet, outcome=ConflictOutcome.NO_CONFLICT, conflicting_evidence_ids=())

    distinct_values = {claim.value for claim in claims}
    if len(distinct_values) <= 1:
        return ConflictResolution(
            facet=facet,
            outcome=ConflictOutcome.NO_CONFLICT,
            winning_value=claims[0].value,
            conflicting_evidence_ids=(),
        )

    # A genuine disagreement -- every claiming item is "conflicting"
    # regardless of whether resolution below ultimately picks a winner.
    conflicting_ids = tuple(claim.evidence_id for claim in claims)

    if policy is ConflictPolicy.AUTHORITY_THEN_REJECT_TIE:
        max_authority = max(claim.authority for claim in claims)
        top_values = {claim.value for claim in claims if claim.authority == max_authority}
        if len(top_values) == 1:
            return ConflictResolution(
                facet=facet,
                outcome=ConflictOutcome.RESOLVED_BY_GOVERNED_AUTHORITY,
                winning_value=next(iter(top_values)),
                conflicting_evidence_ids=conflicting_ids,
            )
        # Tied at the top authority -- reject rather than guess between them.

    return ConflictResolution(
        facet=facet,
        outcome=ConflictOutcome.UNRESOLVED_CONFLICT,
        conflicting_evidence_ids=conflicting_ids,
    )


class ConflictReport(BaseModel):
    """The full package-level conflict result: one `ConflictResolution`
    per governed facet that had at least one claim among the valid items,
    plus the aggregate Gate-C (Task 9) actually needs to decide whether
    generation may proceed."""

    model_config = ConfigDict(extra="forbid")

    resolutions: tuple[ConflictResolution, ...] = Field(default_factory=tuple)
    unresolved_facets: tuple[str, ...] = Field(default_factory=tuple)
    has_unresolved_conflict: bool = False

    def resolution_for(self, facet: str) -> ConflictResolution:
        """Resolve one facet's result. Raises `KeyError` for a facet no
        valid item made any claim about at all -- never returns `None` or
        a synthesized default."""
        for resolution in self.resolutions:
            if resolution.facet == facet:
                return resolution
        raise KeyError(facet)


def validate_conflicts(
    package: EvidencePackage,
    *,
    valid_evidence_ids: Iterable[str],
    source_registry: SourceRegistry,
    conflict_policy: ConflictPolicy,
) -> ConflictReport:
    """Group every valid item's `claims` by facet and resolve each one.
    Only items named in `valid_evidence_ids` (Task 4/5/6's intersection,
    the same filter `completeness.py` uses) ever contribute a claim -- an
    invalid/stale item's assertion is never even considered, let alone
    trusted enough to conflict with anything. Authority is always read
    from the claiming item's own governed source (`source_registry.
    get_source(item.source_id).authority_level`), never from the item
    itself. Deterministic; never mutates `package`/`source_registry`;
    never calls the Model Gateway."""
    valid_ids = set(valid_evidence_ids)
    claims_by_facet: dict[str, list[ConflictClaim]] = {}

    for item in package.items:
        if item.evidence_id not in valid_ids:
            continue
        if not source_registry.has_source(item.source_id):
            continue
        authority = source_registry.get_source(item.source_id).authority_level
        for facet, value in item.claims.items():
            claims_by_facet.setdefault(facet, []).append(
                ConflictClaim(evidence_id=item.evidence_id, authority=authority, value=value)
            )

    resolutions = tuple(
        evaluate_conflict(facet, claims, policy=conflict_policy) for facet, claims in sorted(claims_by_facet.items())
    )
    unresolved_facets = tuple(r.facet for r in resolutions if r.outcome is ConflictOutcome.UNRESOLVED_CONFLICT)

    return ConflictReport(
        resolutions=resolutions,
        unresolved_facets=unresolved_facets,
        has_unresolved_conflict=bool(unresolved_facets),
    )
