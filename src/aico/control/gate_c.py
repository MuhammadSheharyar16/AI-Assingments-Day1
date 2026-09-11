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
Day 11 Task 14 -- observability / provenance metadata. Unlike `gate_a.py`/
`gate_b.py`/`disclosure.py` (pure decision logic, "no I/O, no tracing
import of their own" -- their own module docstrings), `GateC.evaluate()`
traces itself directly, the same way `answer_service.py`'s own
`GroundedAnswerService.answer()` traces its own multi-stage pipeline
rather than leaving it to an outer orchestrator: Gate-C is itself a
multi-stage orchestrator over four other Day 11 validator modules, and
Task 14 explicitly names sub-stage spans ("gate_c" / "provenance_validation"
/ "freshness_validation" / "completeness_validation") no single outer
"gate_c" span alone could distinguish. `_decision()` -- the one funnel
every return path already goes through -- is where every sanitized
attribute Task 14 names gets set on whatever span is current: `policy_
version`/`source_registry_version`/`candidate_evidence_count`/`validated_
evidence_count`/`rejected_evidence_count`/`missing_facet_count`/`conflict_
count`/`freshness_result`(the sanitized summary string)/`decision`/
`reason_codes`/`latency_ms`. Never `request_id`/`correlation_id` (this
module does not import `aico.api`/`aico.observability`, the identical
boundary `answer_service.py` keeps -- those two IDs are carried the same
way every span in this codebase already carries them, as attributes on
whatever root span a caller has open around this call, Day 6 Task 9's own
mechanism, not reintroduced here); never `ontology_version`/`gate_b_
policy_version` either (`GateC` has no `OntologyRegistry`/`PolicyRegistry`
of its own to read them from -- both already appear on the sibling
`"gate_a"`/`"gate_b"` spans a real caller, `ControlPlaneAnswerService`,
opens in the same trace); and never raw evidence content, a raw protected
record, PII, a secret, a full authorization claim, or a generated answer
-- every attribute this module ever sets is a count, a version string, a
governed enum value, or a sanitized reason-code string, the identical
"safe to log" discipline every other span in this codebase already
follows.
Day 11 Task 12 -- preserve Gate-B scope (also folded in, not a separate
check: it is exactly `_check_gate_b_scope()` inside `validate_provenance()`
(Task 4), reused here as one input among several). Gate-C may further
*narrow* what Gate-B already granted (rejecting a tenant/classification
Gate-B never authorized) but has no code path that could ever *widen* it:
`evaluate()`'s own signature has no parameter for a role, an identity, or
a scope override, and the only scope value it ever reads is whatever
`gate_b_decision.effective_tenant_scope`/`effective_data_classes` already
grants -- "Gate-C is an evidence-quality boundary, not a second
authorization system." A scope-violating item's reason is *accumulated*
alongside every other check's (`item_reasons[...].extend(...)`, never
reset or overwritten), and only ever removed from consideration by being
*excluded* from `validated_evidence_ids` -- there is no path in this
module through which "the request needed that item's facet" or "every
other check on it passed" could reintroduce it.

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

import time
from dataclasses import dataclass
from enum import Enum

from opentelemetry import trace
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

# Task 14 -- same pattern `answer_service.py`/`control_plane_answer_service.py`
# already use and document: `opentelemetry.trace.get_tracer(__name__)`
# directly, never importing `aico.observability` here. Works against
# whatever provider `aico.observability.telemetry.configure_tracing()`
# installs, or a harmless no-op default when nothing has configured one
# yet (e.g. a test that imports this module directly).
_tracer = trace.get_tracer(__name__)


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
    started_at: float,
    candidate_evidence_count: int = 0,
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
    returned" discipline `gate_b.py`'s own `_deny()` helper follows -- and,
    Task 14, so every sanitized operational-metadata attribute is recorded
    on whatever span is current in exactly this one place too, rather than
    repeated at each of `evaluate()`'s own return points. `started_at`
    (`time.monotonic()`, taken once at the top of `evaluate()`) is what
    `latency_ms` is measured against."""
    result = GateCDecision(
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

    # Task 14 -- sanitized Gate-C operational metadata, on whatever span is
    # currently active (the "gate_c" span `evaluate()` itself opens -- see
    # below; a harmless no-op when nothing configured a real tracer, e.g. a
    # test calling `evaluate()` directly). Every value here is a count, a
    # version string, a governed enum value, or a sanitized reason-code
    # string -- never raw evidence content, a raw protected record, PII, a
    # secret, a full authorization claim, or a generated answer.
    span = trace.get_current_span()
    span.set_attribute("gate_c.policy_version", policy_version)
    span.set_attribute("gate_c.source_registry_version", source_registry_version)
    span.set_attribute("gate_c.candidate_evidence_count", candidate_evidence_count)
    span.set_attribute("gate_c.validated_evidence_count", len(result.validated_evidence_ids))
    span.set_attribute("gate_c.rejected_evidence_count", len(rejected_evidence_ids))
    span.set_attribute("gate_c.missing_facet_count", len(missing_facets))
    span.set_attribute("gate_c.conflict_count", len(conflict_facets))
    span.set_attribute("gate_c.freshness_result", freshness_summary)
    span.set_attribute("gate_c.decision", status.value)
    span.set_attribute("gate_c.reason_codes", ",".join(reason_codes))
    span.set_attribute("gate_c.latency_ms", (time.monotonic() - started_at) * 1000)

    return result


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
        normal, typed `GateCDecision` result, never a fall-through. Task 14
        -- runs entirely inside one `"gate_c"` span (every return point
        funnels through `_decision()`, which records this span's own
        sanitized attributes); `"provenance_validation"`/
        `"freshness_validation"`/`"completeness_validation"` are its own
        nested child spans, opened only for the stages actually reached."""
        with _tracer.start_as_current_span("gate_c"):
            started_at = time.monotonic()
            request = request or GateCRequest()
            policy_version = self.policy_registry.policy_version
            registry_version = self.source_registry.registry_version
            candidate_evidence_count = len(package.items)

            # Stage 0 -- Gate-B must have actually granted ALLOW.
            if gate_b_decision.decision is not GateBStatus.ALLOW:
                return _decision(
                    GateCStatus.REJECT,
                    policy_version=policy_version,
                    source_registry_version=registry_version,
                    started_at=started_at,
                    candidate_evidence_count=candidate_evidence_count,
                    reason_codes=(GateCReasonCode.GATE_B_NOT_ALLOWED.value,),
                )

            # Stage 1 -- request_kind must be resolved.
            if request.request_kind is None:
                return _decision(
                    GateCStatus.CLARIFY,
                    policy_version=policy_version,
                    source_registry_version=registry_version,
                    started_at=started_at,
                    candidate_evidence_count=candidate_evidence_count,
                    reason_codes=(GateCReasonCode.REQUEST_KIND_NOT_RESOLVED.value,),
                )

            # Stage 2 -- a governed evidence-quality rule must exist.
            rule = self.policy_registry.find_rule(package.intent_id, request.request_kind)
            if rule is None:
                return _decision(
                    GateCStatus.REJECT,
                    policy_version=policy_version,
                    source_registry_version=registry_version,
                    started_at=started_at,
                    candidate_evidence_count=candidate_evidence_count,
                    reason_codes=(GateCReasonCode.NO_GOVERNED_EVIDENCE_REQUIREMENT.value,),
                )

            # Stage 3 -- every candidate item, checked independently
            # against every Day 11 validator. Task 14 -- "provenance_
            # validation" covers Task 2/3's own registry+policy gating
            # (source trust) alongside Task 4/5's validators (the same
            # "trust/provenance" dimension, one span); "freshness_
            # validation" covers Task 6 separately.
            item_reasons: dict[str, list[str]] = {item.evidence_id: [] for item in package.items}

            with _tracer.start_as_current_span("provenance_validation") as span:
                for item in package.items:
                    item_reasons[item.evidence_id].extend(
                        _registry_policy_reasons(
                            item, source_registry=self.source_registry, rule=rule, intent_id=package.intent_id
                        )
                    )

                provenance_report = validate_provenance(
                    package, source_registry=self.source_registry, gate_b_decision=gate_b_decision
                )
                for result in provenance_report.item_results:
                    item_reasons[result.evidence_id].extend(reason.value for reason in result.reasons)

                integrity_checked = provenance_index is not None
                if provenance_index is not None:
                    integrity_report = validate_integrity(package, provenance_index=provenance_index)
                    for result in integrity_report.item_results:
                        item_reasons[result.evidence_id].extend(reason.value for reason in result.reasons)

                failed_so_far = sum(1 for reasons in item_reasons.values() if reasons)
                span.set_attribute("provenance_validation.candidate_count", candidate_evidence_count)
                span.set_attribute("provenance_validation.failed_count", failed_so_far)
                span.set_attribute("provenance_validation.integrity_checked", integrity_checked)

            with _tracer.start_as_current_span("freshness_validation") as span:
                freshness_report = validate_freshness(
                    package, source_registry=self.source_registry, policy_registry=self.policy_registry
                )
                freshness_by_id: dict[str, FreshnessStatus | None] = {}
                for result in freshness_report.item_results:
                    item_reasons[result.evidence_id].extend(reason.value for reason in result.reasons)
                    freshness_by_id[result.evidence_id] = result.status

                freshness_summary = _freshness_summary(
                    [freshness_by_id.get(item.evidence_id) for item in package.items]
                )
                span.set_attribute("freshness_validation.result", freshness_summary)

            validated_evidence_ids = tuple(
                evidence_id for evidence_id, reasons in item_reasons.items() if not reasons
            )
            rejected_evidence_ids = tuple(
                evidence_id for evidence_id, reasons in item_reasons.items() if reasons
            )
            item_reason_codes = tuple(
                dict.fromkeys(reason for reasons in item_reasons.values() for reason in reasons)
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
                    started_at=started_at,
                    candidate_evidence_count=candidate_evidence_count,
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
                    started_at=started_at,
                    candidate_evidence_count=candidate_evidence_count,
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
                    started_at=started_at,
                    candidate_evidence_count=candidate_evidence_count,
                    validated_evidence_ids=(),
                    rejected_evidence_ids=rejected_evidence_ids,
                    reason_codes=(*item_reason_codes, GateCReasonCode.INSUFFICIENT_VALID_ITEMS.value),
                    freshness_summary=freshness_summary,
                )

            # Stage 7 -- completeness, against the governed rule's own
            # required_facets (never `package.required_facets` as supplied
            # by the caller).
            with _tracer.start_as_current_span("completeness_validation") as span:
                effective_package = package.model_copy(update={"required_facets": tuple(rule.required_facets)})
                completeness_result = validate_completeness(
                    effective_package, valid_evidence_ids=validated_evidence_ids, source_registry=self.source_registry
                )
                span.set_attribute("completeness_validation.required_facet_count", len(rule.required_facets))
                span.set_attribute("completeness_validation.covered_facet_count", len(completeness_result.covered_facets))
                span.set_attribute("completeness_validation.missing_facet_count", len(completeness_result.missing_facets))

            if completeness_result.status is CompletenessStatus.INSUFFICIENT_EVIDENCE:
                return _decision(
                    GateCStatus.INSUFFICIENT_EVIDENCE,
                    policy_version=policy_version,
                    source_registry_version=registry_version,
                    started_at=started_at,
                    candidate_evidence_count=candidate_evidence_count,
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
                started_at=started_at,
                candidate_evidence_count=candidate_evidence_count,
                validated_evidence_ids=validated_evidence_ids,
                rejected_evidence_ids=rejected_evidence_ids,
                reason_codes=item_reason_codes,
                freshness_summary=freshness_summary,
            )
