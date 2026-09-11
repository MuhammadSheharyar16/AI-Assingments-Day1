"""
Day 11 Task 6 -- freshness policy.

`provenance_freshness_rules.md`'s freshness rule is deliberately small and
purely arithmetic:

    age = as_of - source_updated_at
    fresh when age <= configured max_age

This module has two layers built on exactly that rule:

- `evaluate_freshness()` -- the pure, deterministic core `gate_c_cases.
  json`'s own `freshness_cases` fixture is shaped for: three primitive
  inputs in (`as_of`, `source_updated_at`, `max_age_hours`), one
  `FreshnessStatus` out. No `EvidenceItem`, no registry, no lookup -- just
  the arithmetic, so it is trivially unit-testable against the fixture's
  own values and reusable anywhere a freshness decision is needed from raw
  timestamps, not only from a fully-validated envelope.
- `validate_freshness()` -- the package-level entry point Gate-C (Task 9)
  is expected to call: resolves each `EvidenceItem`'s own governed
  threshold (Task 2's `SourceRecord.freshness_policy_id` -> Task 3's
  `FreshnessPolicy.max_age_hours`) and evaluates it against `package.
  as_of` -- Task 1's own "deterministic/injectable reference time
  freshness (Task 6) is evaluated against" field, so there is nothing
  else in this module that could reach for wall-clock `datetime.now()` in
  the first place; `as_of` always comes from the caller (a fixed value in
  tests, never live time).

## Required cases, each traced to where it is proven

    - fresh evidence                  -> `evaluate_freshness()`:
                                          `age <= max_age`.
    - exactly-at-threshold evidence    -> `evaluate_freshness()`: `age ==
                                          max_age` is still `<=`, so this
                                          is `FRESH` too (`gate_c_cases.
                                          json` FRESH-002's own "fresh_at_
                                          threshold" label names the
                                          *scenario*, not a third status --
                                          the rule's own "<=" wording
                                          settles which side of the
                                          boundary it falls on).
    - stale evidence                   -> `evaluate_freshness()`: `age >
                                          max_age`.
    - missing freshness timestamp      -> `evaluate_freshness(...,
                                          source_updated_at=None, ...)` ->
                                          `MISSING_TIMESTAMP`. Task 1's
                                          envelope already makes
                                          `EvidenceItem.source_updated_at`
                                          required and non-blank, so this
                                          branch is unreachable through
                                          `validate_freshness()` for a
                                          properly validated item -- kept
                                          on `evaluate_freshness()`'s own
                                          signature (`datetime | None`)
                                          because the pure function is a
                                          general-purpose primitive, not
                                          coupled to `EvidenceItem`'s own
                                          guarantees, the same reasoning
                                          `provenance.py`'s
                                          `GovernedProvenanceIndex.get()`
                                          returning `None` is handled
                                          explicitly rather than assumed
                                          impossible.
    - future/invalid source timestamp  -> `evaluate_freshness()`:
                                          `source_updated_at > as_of` ->
                                          `INVALID_TIMESTAMP` -- a source
                                          claiming to have been updated
                                          after the request's own
                                          reference time is not a valid
                                          claim to age at all, fail closed
                                          rather than compute a negative
                                          age.
    - source whose policy has a
      different max age                -> `validate_freshness()` resolves
                                          each item's *own* source's *own*
                                          governed threshold independently
                                          (Task 2 -> Task 3 lookup, per
                                          item) -- two items from
                                          different sources are never
                                          evaluated against a shared or
                                          hardcoded threshold.

## Rule: retrieval score does not override staleness

Nothing in this module's signatures accepts a rank, score, or position at
all -- `evaluate_freshness()`/`validate_freshness()` have no parameter
retrieval ordering could even be threaded through. A stale top-ranked
result is evaluated by the identical arithmetic as a stale last-ranked
one; there is no code path here that could special-case either.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from aico.evidence.models import EvidenceItem, EvidencePackage
from aico.evidence.policy import GateCPolicyRegistry
from aico.evidence.source_registry import SourceRegistry


class FreshnessStatus(str, Enum):
    """The closed set of outcomes `evaluate_freshness()` (the pure,
    low-level function) can return."""

    FRESH = "fresh"
    STALE = "stale"
    MISSING_TIMESTAMP = "missing_timestamp"
    INVALID_TIMESTAMP = "invalid_timestamp"


def evaluate_freshness(
    *,
    as_of: datetime,
    source_updated_at: datetime | None,
    max_age_hours: int,
) -> FreshnessStatus:
    """The one place this module computes `age = as_of - source_updated_at`
    and compares it to `max_age_hours` -- deterministic, no I/O, no
    wall-clock access; `as_of` is always supplied by the caller (working
    rule: "Do not use wall-clock datetime.now() directly ... without an
    injectable/reference clock"). Never raises for `source_updated_at`
    being `None` or in the future relative to `as_of` -- both are normal,
    typed outcomes (`MISSING_TIMESTAMP`/`INVALID_TIMESTAMP`), not
    exceptions."""
    if source_updated_at is None:
        return FreshnessStatus.MISSING_TIMESTAMP
    if source_updated_at > as_of:
        return FreshnessStatus.INVALID_TIMESTAMP

    age = as_of - source_updated_at
    max_age = timedelta(hours=max_age_hours)
    return FreshnessStatus.FRESH if age <= max_age else FreshnessStatus.STALE


class FreshnessFailureReason(str, Enum):
    """The closed set of reasons `validate_freshness()` ever cites for
    marking one evidence item invalid -- the three `FreshnessStatus`
    failure outcomes, plus two lookup-level reasons only the package-level
    check (not the pure function) can produce."""

    UNKNOWN_SOURCE = "unknown_source"
    UNKNOWN_FRESHNESS_POLICY = "unknown_freshness_policy"
    STALE = "stale"
    MISSING_TIMESTAMP = "missing_timestamp"
    INVALID_TIMESTAMP = "invalid_timestamp"


class FreshnessItemResult(BaseModel):
    """Freshness's per-item verdict: the resolved `FreshnessStatus` (when
    a governed threshold could be resolved at all), the computed age and
    threshold in hours (for observability, Task 14), and every reason a
    rejected item failed."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    valid: bool
    status: FreshnessStatus | None = Field(
        default=None, description="None only when no governed threshold could be resolved for this item at all."
    )
    age_hours: float | None = Field(default=None, description="(as_of - source_updated_at) in hours, when computable.")
    max_age_hours: int | None = Field(default=None, description="The governed threshold this item was evaluated against.")
    reasons: tuple[FreshnessFailureReason, ...] = Field(default_factory=tuple)


class FreshnessReport(BaseModel):
    """The full package-level freshness result -- the `FreshnessItemResult`
    analog of `provenance.py`'s `ProvenanceReport`."""

    model_config = ConfigDict(extra="forbid")

    validated_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    rejected_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    item_results: tuple[FreshnessItemResult, ...] = Field(default_factory=tuple)

    def result_for(self, evidence_id: str) -> FreshnessItemResult:
        """Resolve one item's result by `evidence_id`. Raises `KeyError`
        for an id this report has no result for, matching
        `ProvenanceReport.result_for()`'s identical contract."""
        for result in self.item_results:
            if result.evidence_id == evidence_id:
                return result
        raise KeyError(evidence_id)


def _evaluate_item_freshness(
    item: EvidenceItem,
    *,
    as_of: datetime,
    source_registry: SourceRegistry,
    policy_registry: GateCPolicyRegistry,
) -> FreshnessItemResult:
    if not source_registry.has_source(item.source_id):
        return FreshnessItemResult(
            evidence_id=item.evidence_id,
            valid=False,
            reasons=(FreshnessFailureReason.UNKNOWN_SOURCE,),
        )

    source = source_registry.get_source(item.source_id)
    if not policy_registry.has_freshness_policy(source.freshness_policy_id):
        return FreshnessItemResult(
            evidence_id=item.evidence_id,
            valid=False,
            reasons=(FreshnessFailureReason.UNKNOWN_FRESHNESS_POLICY,),
        )

    freshness_policy = policy_registry.get_freshness_policy(source.freshness_policy_id)
    max_age_hours = freshness_policy.max_age_hours
    status = evaluate_freshness(as_of=as_of, source_updated_at=item.source_updated_at, max_age_hours=max_age_hours)

    age_hours: float | None = None
    if status in (FreshnessStatus.FRESH, FreshnessStatus.STALE):
        age_hours = (as_of - item.source_updated_at).total_seconds() / 3600

    reason_by_status = {
        FreshnessStatus.STALE: FreshnessFailureReason.STALE,
        FreshnessStatus.MISSING_TIMESTAMP: FreshnessFailureReason.MISSING_TIMESTAMP,
        FreshnessStatus.INVALID_TIMESTAMP: FreshnessFailureReason.INVALID_TIMESTAMP,
    }
    reason = reason_by_status.get(status)
    reasons = (reason,) if reason is not None else ()

    return FreshnessItemResult(
        evidence_id=item.evidence_id,
        valid=status is FreshnessStatus.FRESH,
        status=status,
        age_hours=age_hours,
        max_age_hours=max_age_hours,
        reasons=reasons,
    )


def validate_freshness(
    package: EvidencePackage,
    *,
    source_registry: SourceRegistry,
    policy_registry: GateCPolicyRegistry,
) -> FreshnessReport:
    """Validate every item `package` carries against Task 6's freshness
    policy, using `package.as_of` as the sole reference time -- never
    wall-clock, and never influenced by retrieval rank/score (see module
    docstring's "Rule" section). Each item's own governed threshold is
    resolved independently through `source_registry`/`policy_registry`;
    an item whose source or freshness policy cannot be resolved fails
    closed rather than being silently treated as fresh. Returns one
    `FreshnessReport`; never raises for an individual item's failure,
    never mutates `package`/`source_registry`/`policy_registry`."""
    item_results = tuple(
        _evaluate_item_freshness(item, as_of=package.as_of, source_registry=source_registry, policy_registry=policy_registry)
        for item in package.items
    )
    return FreshnessReport(
        validated_evidence_ids=tuple(r.evidence_id for r in item_results if r.valid),
        rejected_evidence_ids=tuple(r.evidence_id for r in item_results if not r.valid),
        item_results=item_results,
    )
