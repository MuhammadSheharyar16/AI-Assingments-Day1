"""
Day 11 Task 9 -- Gate-C: the deterministic evidence-trust/quality boundary
that decides whether the evidence retrieval actually returned may reach
generation at all -- run *after* Gate-B/protected evidence retrieval and
*before* the Model Gateway (`Day 11 Task.pdf`'s own architecture diagram).
Day 11 Task 10 -- filtering behavior (folded in here, not a separate
module: Gate-C cannot decide `allow` without first knowing exactly which
evidence survived every other Day 11 check, so "which items are still
valid" and "what does that set of survivors justify" are one algorithm,
not two).

"Gate-A/lane selection decided what a request means and which governed
route it takes; Gate-B decided whether the trusted caller may proceed,
under which tenant/data scope. Gate-C decides whether the evidence
retrieval actually returned may be trusted enough to use at all" (Day 11
`Build outcome`). `GateC.evaluate()` never guesses: every non-`REJECT`
outcome is derived only from real, governed data -- the loaded
`SourceRegistry` (Task 2), the loaded `GateCPolicyRegistry` (Task 3), and
the per-item verdicts Tasks 4-8's own validators already computed -- never
repaired, widened, or second-guessed here.

## Inputs Gate-C consumes (Task 9's own list)

    - Gate-B allowed decision/effective scope  -> `gate_b_decision:
                                                    GateBDecision`.
    - governed request/intent metadata         -> `package.intent_id`
                                                    (Task 1's own field,
                                                    reused rather than
                                                    duplicated) plus
                                                    `request.request_kind`
                                                    (`GateCRequest`, new
                                                    here -- Task 1's
                                                    envelope has no
                                                    `request_kind` field;
                                                    it belongs to how the
                                                    request was
                                                    classified, not to the
                                                    evidence itself).
    - candidate evidence package               -> `package:
                                                    EvidencePackage`.
    - source registry                          -> `source_registry:
                                                    SourceRegistry`.
    - Gate-C policy                            -> `policy_registry:
                                                    GateCPolicyRegistry`.

`provenance_index` (Task 5) is additionally accepted, optional: this pack
ships no committed governed-provenance-index file (`provenance.py`'s own
docstring), so a caller with one populates it; without one, Task 5's
*independent* integrity check is simply not run for this evaluation --
Task 4's self-consistency check inside `validate_provenance()` still runs
unconditionally (it needs nothing external).

## Decision algorithm, fail-closed at every stage

  0. Gate-B must have actually granted `ALLOW` (working rule: "Gate-C must
     not widen Gate-B authorization scope") -- `reject` immediately
     otherwise; there is no scope for Gate-C to even evaluate evidence
     against if Gate-B itself never established one.
  1. `request.request_kind` must be resolved -- `None` means the caller
     does not yet know which governed evidence-quality rule applies at
     all, Task 9's own documented `clarify` case ("the request itself is
     safely ambiguous about which evidence requirement applies").
  2. `policy_registry.find_rule(package.intent_id, request.request_kind)`
     must resolve exactly one governed `IntentEvidenceRequirement` --
     `reject` (not `clarify`: the caller committed to a specific
     `request_kind`, and nothing governs it) when it does not.
  3. Every candidate item is checked independently, against *every* Day 11
     validator: Task 2/3's own registry+policy gating (source exists, is
     active, supports this intent, its `source_type` is one this rule's
     `allowed_source_types` governs), Task 4's `validate_provenance()`,
     Task 5's `validate_integrity()` (only when `provenance_index` was
     supplied), and Task 6's `validate_freshness()`. An item with *any*
     reason from *any* of these is rejected -- `validated_evidence_ids`/
     `rejected_evidence_ids` is this stage's own filtering result (Task
     10's own "Gate-C may reject individual evidence items").
  4. Task 8's `validate_conflicts()` runs only over the *validated* set
     (an already-rejected item's claim was never trustworthy enough to
     conflict with anything) -- an unresolved conflict on any facet
     `reject`s outright (working rule: "unresolved conflict cannot
     silently proceed to the model as if evidence were consistent");
     never a second chance, never resolved by asking the model which
     source "looks better."
  5. When at least one candidate item existed at all but none survived
     stage 3, `reject` (Task 9's own meaning: `insufficient_evidence`
     requires that *valid* evidence exists in the first place; zero
     survivors from a non-empty candidate set means every candidate was
     itself untrustworthy, not merely incomplete).
  6. Task 8's own "minimum evidence requirements" governed concept --
     fewer validated items than `rule.minimum_valid_items` ->
     `insufficient_evidence` (this also covers an empty candidate package
     from the start, since `minimum_valid_items` is always >= 1, Task 3's
     own field constraint).
  7. Task 7's `validate_completeness()`, evaluated against *this rule's*
     own governed `required_facets` (never `package.required_facets` as
     supplied by the caller -- Gate-C derives the authoritative
     requirement from the governed policy it just resolved, the same
     "the policy decides, not the request" posture every other governed
     boundary in this codebase already takes) -- missing coverage ->
     `insufficient_evidence`.
  8. Otherwise, `allow` -- Task 10's own filtering example exactly: "5
     retrieved items, 2 invalid, 3 valid, required facets still fully
     covered -> allow using only the 3 validated items." Rejected items
     are named in `rejected_evidence_ids`; nothing downstream (the prompt
     builder, Task 13) is ever handed anything but `validated_evidence_ids`.

`policy_version`/`source_registry_version` are populated on every path,
including every early-exit `reject`/`clarify` -- Task 9's own decision-
provenance field list, and both registries are already loaded regardless
of how far evaluation gets.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from aico.control.models import GateBDecision, GateBStatus
from aico.evidence.completeness import CompletenessStatus, validate_completeness
from aico.evidence.conflicts import validate_conflicts
from aico.evidence.freshness import FreshnessStatus, validate_freshness
from aico.evidence.models import EvidenceItem, EvidencePackage
from aico.evidence.policy import GateCPolicyRegistry, IntentEvidenceRequirement
from aico.evidence.provenance import (
    GovernedProvenanceIndex,
    ProvenanceFailureReason,
    validate_integrity,
    validate_provenance,
)
from aico.evidence.source_registry import SourceRegistry


class GateCStatus(str, Enum):
    """The four required Gate-C outcomes (Task 9). Deliberately not
    extensible at the type level -- a fifth status would need an
    assignment-level change, never an ad hoc string. See the module
    docstring's decision algorithm for exactly when each is reached."""

    ALLOW = "allow"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CLARIFY = "clarify"
    REJECT = "reject"


class GateCReasonCode(str, Enum):
    """Gate-C's own reason codes -- for reasons only Gate-C itself can
    determine (it is the first thing that combines a governed rule with
    the source registry, or that looks at Gate-B's own decision status).
    Per-item reasons Tasks 4/5/6 already compute
    (`ProvenanceFailureReason`/`IntegrityFailureReason`/
    `FreshnessFailureReason`) are folded into `GateCDecision.reason_codes`
    by their own `.value`, not duplicated as members here."""

    GATE_B_NOT_ALLOWED = "gate_b_not_allowed"
    REQUEST_KIND_NOT_RESOLVED = "request_kind_not_resolved"
    NO_GOVERNED_EVIDENCE_REQUIREMENT = "no_governed_evidence_requirement"
    DISABLED_SOURCE = "disabled_source"
    SOURCE_INTENT_NOT_ALLOWED = "source_intent_not_allowed"
    SOURCE_TYPE_NOT_ALLOWED = "source_type_not_allowed"
    NO_VALID_EVIDENCE = "no_valid_evidence"
    INSUFFICIENT_VALID_ITEMS = "insufficient_valid_items"
    MISSING_REQUIRED_FACETS = "missing_required_facets"
    UNRESOLVED_CONFLICT = "unresolved_conflict"


class GateCDecision(BaseModel):
    """Gate-C's typed decision (Task 9's required field list).
    `validated_evidence_ids` is the *only* evidence the prompt builder
    (Task 13) may ever be handed -- populated for `allow` only; every
    other status carries an empty tuple (least privilege: nothing is
    forwarded until a decision actually resolves to `allow`, the identical
    posture `GateBDecision`'s own docstring documents for
    `effective_tenant_scope`). `rejected_evidence_ids` is populated
    whenever at least one candidate item failed some check, regardless of
    the final decision -- Task 14's observability and Task 15's
    `gate_c_decisions.md` both want to see what was filtered out even on
    an otherwise-`allow` decision."""

    model_config = ConfigDict(extra="forbid")

    decision: GateCStatus
    validated_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    rejected_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    reason_codes: tuple[str, ...] = Field(default_factory=tuple)
    missing_facets: tuple[str, ...] = Field(default_factory=tuple)
    conflict_facets: tuple[str, ...] = Field(default_factory=tuple)
    freshness_summary: str = Field(default="", description="Sanitized aggregate, e.g. 'fresh=2;stale=1;other=0'.")
    policy_version: str = Field(min_length=1, description="The governed Gate-C policy version this decision was made against.")
    source_registry_version: str = Field(
        min_length=1, description="The governed source registry version this decision was made against."
    )


@dataclass(frozen=True)
class GateCRequest:
    """Governed request/intent metadata beyond what the candidate evidence
    package itself already carries (Task 9's "governed request/intent
    metadata" input) -- today, just `request_kind`, the narrower governed
    shape `GateCPolicyRegistry.find_rule()` matches a rule against
    alongside `package.intent_id` (Task 1's own field, reused rather than
    duplicated on this type). `None` when upstream classification could
    not determine which governed evidence-quality rule applies at all --
    Gate-C's own `clarify` outcome exists for exactly that case."""

    request_kind: str | None = None


def _freshness_summary(item_statuses: list[FreshnessStatus | None]) -> str:
    fresh = sum(1 for status in item_statuses if status is FreshnessStatus.FRESH)
    stale = sum(1 for status in item_statuses if status is FreshnessStatus.STALE)
    other = len(item_statuses) - fresh - stale
    return f"fresh={fresh};stale={stale};other={other}"


def _registry_policy_reasons(
    item: EvidenceItem,
    *,
    source_registry: SourceRegistry,
    rule: IntentEvidenceRequirement,
    intent_id: str,
) -> tuple[str, ...]:
    """Task 2/3's own registry+policy gating -- neither `SourceRegistry`
    (Task 2) nor `GateCPolicyDocument` (Task 3) alone can make these
    checks; only Gate-C, which has both plus the resolved `intent_id`,
    can. Short-circuits on an unknown source (nothing else is resolvable
    without one) -- `validate_provenance()` independently reaches the
    identical `unknown_source` conclusion for the same item; the two are
    deduplicated once combined, see `evaluate()`."""
    if not source_registry.has_source(item.source_id):
        return (ProvenanceFailureReason.UNKNOWN_SOURCE.value,)

    source = source_registry.get_source(item.source_id)
    reasons: list[str] = []
    if not source.is_active:
        reasons.append(GateCReasonCode.DISABLED_SOURCE.value)
    if not source.supports_intent(intent_id):
        reasons.append(GateCReasonCode.SOURCE_INTENT_NOT_ALLOWED.value)
    if source.source_type not in rule.allowed_source_types:
        reasons.append(GateCReasonCode.SOURCE_TYPE_NOT_ALLOWED.value)
    return tuple(reasons)


def _decision(
    status: GateCStatus,
    *,
    policy_version: str,
    source_registry_version: str,
    validated_evidence_ids: tuple[str, ...] = (),
    rejected_evidence_ids: tuple[str, ...] = (),
    reason_codes: tuple[str, ...] = (),
    missing_facets: tuple[str, ...] = (),
    conflict_facets: tuple[str, ...] = (),
    freshness_summary: str = "",
) -> GateCDecision:
    """Every path funnels through here so every field is deliberately set
    (or deliberately left at its empty default) in exactly one place --
    the same "no partially populated result by accident of which stage
    returned" discipline `gate_b.py`'s own `_deny()` helper follows."""
    return GateCDecision(
        decision=status,
        validated_evidence_ids=validated_evidence_ids if status is GateCStatus.ALLOW else (),
        rejected_evidence_ids=rejected_evidence_ids,
        reason_codes=reason_codes,
        missing_facets=missing_facets,
        conflict_facets=conflict_facets,
        freshness_summary=freshness_summary,
        policy_version=policy_version,
        source_registry_version=source_registry_version,
    )


@dataclass
class GateC:
    """Gate-C: built once against a loaded `SourceRegistry` (Task 2) and
    `GateCPolicyRegistry` (Task 3), reused for every request. `evaluate()`
    is the only public entry point -- see the module docstring for the
    fail-closed algorithm it runs."""

    source_registry: SourceRegistry
    policy_registry: GateCPolicyRegistry

    def evaluate(
        self,
        *,
        gate_b_decision: GateBDecision,
        package: EvidencePackage,
        request: GateCRequest | None = None,
        provenance_index: GovernedProvenanceIndex | None = None,
    ) -> GateCDecision:
        """Evaluate one candidate evidence package. Never raises for an
        ordinary input, well-formed or not -- every outcome, including
        every reject reason and the one documented `clarify` case, is a
        normal, typed `GateCDecision` result, never a fall-through."""
        request = request or GateCRequest()
        policy_version = self.policy_registry.policy_version
        registry_version = self.source_registry.registry_version

        # Stage 0 -- Gate-B must have actually granted ALLOW.
        if gate_b_decision.decision is not GateBStatus.ALLOW:
            return _decision(
                GateCStatus.REJECT,
                policy_version=policy_version,
                source_registry_version=registry_version,
                reason_codes=(GateCReasonCode.GATE_B_NOT_ALLOWED.value,),
            )

        # Stage 1 -- request_kind must be resolved.
        if request.request_kind is None:
            return _decision(
                GateCStatus.CLARIFY,
                policy_version=policy_version,
                source_registry_version=registry_version,
                reason_codes=(GateCReasonCode.REQUEST_KIND_NOT_RESOLVED.value,),
            )

        # Stage 2 -- a governed evidence-quality rule must exist.
        rule = self.policy_registry.find_rule(package.intent_id, request.request_kind)
        if rule is None:
            return _decision(
                GateCStatus.REJECT,
                policy_version=policy_version,
                source_registry_version=registry_version,
                reason_codes=(GateCReasonCode.NO_GOVERNED_EVIDENCE_REQUIREMENT.value,),
            )

        # Stage 3 -- every candidate item, checked independently against
        # every Day 11 validator.
        item_reasons: dict[str, list[str]] = {item.evidence_id: [] for item in package.items}

        for item in package.items:
            item_reasons[item.evidence_id].extend(
                _registry_policy_reasons(
                    item, source_registry=self.source_registry, rule=rule, intent_id=package.intent_id
                )
            )

        provenance_report = validate_provenance(package, source_registry=self.source_registry, gate_b_decision=gate_b_decision)
        for result in provenance_report.item_results:
            item_reasons[result.evidence_id].extend(reason.value for reason in result.reasons)

        if provenance_index is not None:
            integrity_report = validate_integrity(package, provenance_index=provenance_index)
            for result in integrity_report.item_results:
                item_reasons[result.evidence_id].extend(reason.value for reason in result.reasons)

        freshness_report = validate_freshness(package, source_registry=self.source_registry, policy_registry=self.policy_registry)
        freshness_by_id: dict[str, FreshnessStatus | None] = {}
        for result in freshness_report.item_results:
            item_reasons[result.evidence_id].extend(reason.value for reason in result.reasons)
            freshness_by_id[result.evidence_id] = result.status

        validated_evidence_ids = tuple(
            evidence_id for evidence_id, reasons in item_reasons.items() if not reasons
        )
        rejected_evidence_ids = tuple(
            evidence_id for evidence_id, reasons in item_reasons.items() if reasons
        )
        item_reason_codes = tuple(
            dict.fromkeys(reason for reasons in item_reasons.values() for reason in reasons)
        )
        freshness_summary = _freshness_summary(
            [freshness_by_id.get(item.evidence_id) for item in package.items]
        )

        # Stage 4 -- conflicts, over the validated set only.
        conflict_report = validate_conflicts(
            package,
            valid_evidence_ids=validated_evidence_ids,
            source_registry=self.source_registry,
            conflict_policy=rule.conflict_policy,
        )
        if conflict_report.has_unresolved_conflict:
            return _decision(
                GateCStatus.REJECT,
                policy_version=policy_version,
                source_registry_version=registry_version,
                validated_evidence_ids=validated_evidence_ids,
                rejected_evidence_ids=rejected_evidence_ids,
                reason_codes=(*item_reason_codes, GateCReasonCode.UNRESOLVED_CONFLICT.value),
                conflict_facets=conflict_report.unresolved_facets,
                freshness_summary=freshness_summary,
            )

        # Stage 5 -- a non-empty candidate set with zero survivors is
        # broken evidence, not merely incomplete.
        if package.items and not validated_evidence_ids:
            return _decision(
                GateCStatus.REJECT,
                policy_version=policy_version,
                source_registry_version=registry_version,
                rejected_evidence_ids=rejected_evidence_ids,
                reason_codes=(*item_reason_codes, GateCReasonCode.NO_VALID_EVIDENCE.value),
                freshness_summary=freshness_summary,
            )

        # Stage 6 -- minimum evidence requirement (Task 3's own field).
        if len(validated_evidence_ids) < rule.minimum_valid_items:
            return _decision(
                GateCStatus.INSUFFICIENT_EVIDENCE,
                policy_version=policy_version,
                source_registry_version=registry_version,
                validated_evidence_ids=(),
                rejected_evidence_ids=rejected_evidence_ids,
                reason_codes=(*item_reason_codes, GateCReasonCode.INSUFFICIENT_VALID_ITEMS.value),
                freshness_summary=freshness_summary,
            )

        # Stage 7 -- completeness, against the governed rule's own
        # required_facets (never `package.required_facets` as supplied by
        # the caller).
        effective_package = package.model_copy(update={"required_facets": tuple(rule.required_facets)})
        completeness_result = validate_completeness(
            effective_package, valid_evidence_ids=validated_evidence_ids, source_registry=self.source_registry
        )
        if completeness_result.status is CompletenessStatus.INSUFFICIENT_EVIDENCE:
            return _decision(
                GateCStatus.INSUFFICIENT_EVIDENCE,
                policy_version=policy_version,
                source_registry_version=registry_version,
                rejected_evidence_ids=rejected_evidence_ids,
                reason_codes=(*item_reason_codes, GateCReasonCode.MISSING_REQUIRED_FACETS.value),
                missing_facets=completeness_result.missing_facets,
                freshness_summary=freshness_summary,
            )

        # Stage 8 -- allow, using only the validated evidence.
        return _decision(
            GateCStatus.ALLOW,
            policy_version=policy_version,
            source_registry_version=registry_version,
            validated_evidence_ids=validated_evidence_ids,
            rejected_evidence_ids=rejected_evidence_ids,
            reason_codes=item_reason_codes,
            freshness_summary=freshness_summary,
        )
