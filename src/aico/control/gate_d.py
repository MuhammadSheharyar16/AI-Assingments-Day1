"""
Day 12 Task 3 -- final citation reconciliation.
Day 12 Task 4 -- evidence/citation provenance preservation (folded into
Task 3's own check, not a separate function -- see "Provenance" below for
why the two are one algorithm, the same "filtering and the decision that
depends on it are one thing, not two" reasoning `gate_c.py`'s own
docstring gives for folding Day 11 Task 10 into Task 9).
Day 12 Task 6 -- final disclosure validation, the field/profile-driven
half only (see this module's own "Day 12 Task 6" section for why the
secret-pattern/hidden-prompt-marker half is a separate function, Task 7's
own).
Day 12 Task 7 -- deterministic secret / protected-value detection, the
profile-independent half of final disclosure validation (see this
module's own "Day 12 Task 7" section).
Day 12 Task 8 -- latency-budget policy.
Day 12 Task 9 -- safe failure behavior.
Day 12 Task 10 -- the Gate-D decision contract itself (see this module's
own "Day 12 Task 10" section, appended near the end of this file): the
`GateD` class that orchestrates every one of Tasks 3-9's own checks into
one typed `GateDDecision`, the same role `GateC.evaluate()` plays for
Day 11 Tasks 4-8.
Day 12 Task 13 -- observability / audit metadata, folded into `GateD.
evaluate()` itself rather than a separate function (see this module's own
"Task 13" docstring subsection, immediately preceding `GateDStatus`) --
the identical "the orchestrator traces itself" reasoning `gate_c.py`'s
own module docstring gives for folding its own Task 14 the same way.

Day 12's required structure names no separate module for citation
reconciliation, final disclosure validation, latency-budget checking, or
safe failure behavior (unlike Gate-C's own evidence validators, each
given a dedicated file under `src/aico/evidence/` because Day 11's tree
explicitly lists them) -- `gate_d.py` itself is where all of it lives,
built up one labeled section per task the same way `policy_models.py`
grew a "Day 12 Task 2" section rather than a new file. Tasks 3/4/6/7/8/9/
10/13 are implemented so far.

## What Gate-D's final citation check proves

`final_response_rules.md` (`data/day12_pack/`): "`final_citations ⊆
Gate-C validated evidence`. A single forged or rejected-evidence citation
fails the candidate." Concretely: `reconcile_final_citations()` takes one
typed `FinalResponseCandidate` (Task 1) and the actual `GateCEvidenceRecord`s
Gate-C validated for this request (Task 10's own "Gate-C validated evidence
metadata" input -- a *separate* parameter from the candidate, never folded
into `FinalResponseCandidate` itself: Task 1's envelope carries only the
bare `gate_c_validated_evidence_ids`, cheap enough for observability/audit
use; the full provenance records this function needs to actually
reconcile against are supplied directly, the identical split Gate-C itself
draws between "the request/intent metadata" and "the candidate evidence
package" as two separate `evaluate()` parameters), plus the governed
`CitationPolicy` (Task 2).

Every Task 3 "Required behavior" bullet, traced to where it is enforced:

    - valid final citations pass        -> a citation whose evidence_id
                                            resolves to a
                                            `GateCEvidenceRecord` AND
                                            agrees with it on chunk_id/
                                            source_id/source_version is
                                            `valid=True` in its own
                                            `CitationCheckResult`.
    - citation to Gate-C-rejected item
      fails                            -> that item's evidence_id is
                                            simply absent from
                                            `gate_c_validated_evidence`
                                            (Gate-C never reports a
                                            rejected id as validated) --
                                            indistinguishable, by design,
                                            from a wholly forged id; both
                                            resolve to the identical
                                            `CITATION_NOT_GATE_C_VALIDATED`
                                            reason. `final_citation_ids ⊆
                                            gate_c_validated_evidence_ids`
                                            (the assignment's own
                                            "Conceptually" line) is a
                                            single membership test -- it
                                            does not, and structurally
                                            cannot, distinguish *why* an
                                            id is outside the validated
                                            set, only that it is.
    - forged citation fails             -> same check, same reason code --
                                            see immediately above.
    - mixed valid + invalid citation
      fails                            -> ANY invalid citation fails the
                                            whole candidate,
                                            unconditionally (see
                                            "Never silently drop" below);
                                            `reject_mixed_valid_invalid`
                                            additionally appends the
                                            overarching
                                            `MIXED_VALID_INVALID_CITATIONS`
                                            reason when the policy says to
                                            name that specific shape of
                                            failure.
    - answered status requiring
      citations cannot pass with zero
      citations                        -> `policy.answered_requires_
                                            citation` + `candidate_status
                                            is ANSWERED` + empty
                                            `candidate_citations` ->
                                            `MISSING_REQUIRED_CITATION`.
    - insufficient-evidence status must
      not contain fabricated factual
      citations                        -> no status-based exemption
                                            anywhere in this module: every
                                            citation a candidate carries,
                                            `insufficient_evidence`
                                            included, goes through the
                                            identical Gate-C-membership/
                                            provenance check above. A
                                            "clean" `insufficient_evidence`
                                            candidate (`final_quality_
                                            cases.json` QUAL12-006) simply
                                            carries zero citations to begin
                                            with -- there is no separate
                                            "zero citations required" rule
                                            for this status the way
                                            `answered_requires_citation`
                                            is for `answered`.

## Never silently drop an invalid citation

Working rule: "Do not silently delete an invalid citation and return the
remaining answer as trusted." This is read as an absolute system
invariant, not something `reject_mixed_valid_invalid=False` could ever
switch off -- `reconcile_final_citations()` has no code path that removes
an invalid citation from consideration and reports `passed=True` using
only the survivors. `reject_mixed_valid_invalid` governs only whether the
*additional*, overarching `MIXED_VALID_INVALID_CITATIONS` reason is named
alongside the per-citation reasons a mix already carries -- never whether
the candidate fails. (Gate-C's own analogous flag, `conflict_policy`,
governs *how* a conflict is resolved; nothing in this codebase's Gate-C or
Gate-D ever exposes a policy switch for "resolve invalid evidence by
quietly using only what's left.")

## `citations_must_be_gate_c_approved`

The one real behavioral switch this function reads: `False` skips the
Gate-C-membership/provenance check entirely (every citation is trivially
`valid=True` from this function's own perspective) -- a policy version
that has decided this lane does not require Gate-C reconciliation at all.
`gate_d_policy_v1.json`'s committed value is `true`; no shipped fixture
exercises `false`, so this module's own test file adds a dedicated case
proving the switch is genuinely honored, not merely declared.

## Provenance (Task 4)

`final_citation_ids ⊆ gate_c_validated_evidence_ids` alone (evidence_id
membership only) would let `final_citation_cases.json` CIT12-005
("source_version_mismatch") through: its citation's `evidence_id` really
is in the validated set, it merely disagrees with that set's own record
about which `source_version` it was retrieved at. `GateCEvidenceRecord`
(below) is what makes the *full* provenance identity available for this
comparison -- `chunk_id`/`source_id`/`source_version`, not just
`evidence_id` -- reusing the same four provenance-identity fields
`final_response.py`'s own `FinalCitation` already validates the shape of,
kept as a distinct type (not a second `FinalCitation` reference) because
it represents a different thing: what Gate-C actually validated, never
what a candidate merely *claims*. "If source/version provenance
conflicts, Gate-D rejects the candidate" (Task 4) is exactly
`CITATION_PROVENANCE_MISMATCH` below -- checked only once evidence_id
membership itself already holds (a citation whose evidence_id is not even
in the validated set has nothing to compare provenance against; it is
already `CITATION_NOT_GATE_C_VALIDATED`).

Task 4's remaining named field, `citation_id`, plays no role in this
comparison (Gate-C's own record carries none to compare it against -- it
is the candidate's own public-facing id, not a piece of Gate-C provenance)
but is still "retained enough ... to resolve back to the evidence
supplied to generation" (Task 4's own framing): `CitationCheckResult`
below carries it through unchanged from the candidate's own
`FinalCitation.citation_id`, so a report reader can resolve one verdict
back to its originating public citation without re-walking
`candidate.candidate_citations` by `evidence_id`."""
from __future__ import annotations

import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, Field, field_validator

from aico.contracts.models import AnswerStatus
from aico.control.disclosure import ProtectedField, apply_disclosure
from aico.control.final_response import FinalCitation, FinalResponseCandidate
from aico.control.models import GateBDecision
from aico.control.policy_models import (
    CitationPolicy,
    DisclosureAction,
    DisclosureProfile,
    GateDDisclosurePolicy,
    LatencyBudgets,
    SafeFailureSpec,
)
from aico.control.policy_registry import GateDPolicyRegistry
from aico.control.quality import QualityCheckReport, QualityFailureSeverity, QualityReasonCode, check_final_quality


class GateCEvidenceRecord(BaseModel):
    """The provenance identity of one Gate-C-validated evidence item, as
    supplied directly to Gate-D -- Task 10's own "Gate-C validated
    evidence metadata" input, kept separate from
    `FinalResponseCandidate.gate_c_validated_evidence_ids` (see module
    docstring). Deliberately just the four provenance-identity fields
    citation reconciliation needs (Task 3/4), not the full
    `aico.evidence.models.EvidenceItem` -- whose remaining fields
    (content, tenant scope, freshness timestamps, ...) Gate-C has already
    checked and this reconciliation has no further use for. A real
    deployment builds one of these per `GateCDecision.validated_evidence_ids`
    entry (Task 11's wiring, from the `EvidenceItem` retrieval actually
    returned); `final_citation_cases.json`'s own `gate_c_validated_evidence`
    fixture entries already ship exactly this shape."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_version: str = Field(min_length=1)


class CitationReasonCode(str, Enum):
    """The closed set of reasons `reconcile_final_citations()` ever cites.
    Gate-D's full decision contract (Task 10) is expected to fold these
    into its own typed `reason_codes`, never invent a new, undocumented
    citation-reconciliation failure string."""

    MISSING_REQUIRED_CITATION = "missing_required_citation"
    CITATION_NOT_GATE_C_VALIDATED = "citation_not_gate_c_validated"
    CITATION_PROVENANCE_MISMATCH = "citation_provenance_mismatch"
    MIXED_VALID_INVALID_CITATIONS = "mixed_valid_invalid_citations"


class CitationCheckResult(BaseModel):
    """Citation reconciliation's per-citation verdict -- `evidence_id` this
    result is for, whether it passed both Task 3 checks, and -- when it
    did not -- every reason it failed (mirrors `evidence/provenance.py`'s
    own `ProvenanceItemResult` shape one layer over).

    `citation_id` is carried through unchanged from the candidate's own
    `FinalCitation.citation_id` -- Task 4's own named field, absent from
    every shipped fixture citation (`final_citation_cases.json`'s own
    citations never carry one, `final_response.py`'s own docstring already
    notes why) but threaded through here whenever a real caller does
    supply one, so a report reader can resolve a verdict back to the
    candidate's own public citation id without re-walking
    `candidate.candidate_citations` by `evidence_id`. Plays no role in the
    reconciliation decision itself -- `evidence_id`/`chunk_id`/`source_id`/
    `source_version` are what Gate-C's own record is keyed and compared
    against (see `_check_one_citation`)."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    citation_id: str | None = Field(default=None)
    valid: bool
    reasons: tuple[CitationReasonCode, ...] = Field(default_factory=tuple)


class CitationReconciliationReport(BaseModel):
    """The full candidate-level reconciliation result:
    `reconcile_final_citations()`'s one return value, the shape Gate-D's
    decision contract (Task 10) is expected to consume rather than
    re-running the per-citation checks itself. `valid_citation_evidence_ids`/
    `invalid_citation_evidence_ids` are populated from the per-citation
    verdicts regardless of the overall `passed` outcome -- Task 13's
    observability and Task 14's `final_citation_report.md` both want to
    see which citations individually reconciled even on an otherwise
    failing candidate, the identical "populated regardless of the final
    decision" convention `GateCDecision.rejected_evidence_ids` already
    follows."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    reason_codes: tuple[CitationReasonCode, ...] = Field(default_factory=tuple)
    citation_checks: tuple[CitationCheckResult, ...] = Field(default_factory=tuple)
    valid_citation_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    invalid_citation_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)


def _check_one_citation(
    citation: FinalCitation,
    *,
    validated_by_id: dict[str, GateCEvidenceRecord],
    citations_must_be_gate_c_approved: bool,
) -> CitationCheckResult:
    if not citations_must_be_gate_c_approved:
        return CitationCheckResult(evidence_id=citation.evidence_id, citation_id=citation.citation_id, valid=True)

    validated = validated_by_id.get(citation.evidence_id)
    if validated is None:
        # Covers both Task 3 bullets identically -- a wholly forged
        # evidence_id and one Gate-C actually saw and rejected are the
        # same "not in the validated set" outcome; see module docstring.
        return CitationCheckResult(
            evidence_id=citation.evidence_id,
            citation_id=citation.citation_id,
            valid=False,
            reasons=(CitationReasonCode.CITATION_NOT_GATE_C_VALIDATED,),
        )

    # Task 4 -- full provenance identity, not evidence_id membership
    # alone: `citation_id` (checked for presence/shape by Task 1's
    # envelope already, never compared here -- it is the candidate's own
    # public-facing id, Gate-C's record carries none to compare it
    # against) plays no part in this comparison; `chunk_id`/`source_id`/
    # `source_version` are.
    provenance_matches = (
        citation.chunk_id == validated.chunk_id
        and citation.source_id == validated.source_id
        and citation.source_version == validated.source_version
    )
    if not provenance_matches:
        return CitationCheckResult(
            evidence_id=citation.evidence_id,
            citation_id=citation.citation_id,
            valid=False,
            reasons=(CitationReasonCode.CITATION_PROVENANCE_MISMATCH,),
        )

    return CitationCheckResult(evidence_id=citation.evidence_id, citation_id=citation.citation_id, valid=True)


def reconcile_final_citations(
    candidate: FinalResponseCandidate,
    *,
    gate_c_validated_evidence: Sequence[GateCEvidenceRecord],
    policy: CitationPolicy,
) -> CitationReconciliationReport:
    """Reconcile `candidate.candidate_citations` against the evidence
    Gate-C actually validated for this request. Never raises for an
    ordinary input, well-formed or not -- every outcome is a normal, typed
    `CitationReconciliationReport`, the identical "typed result, not an
    exception, for an expected pass/fail outcome" convention every other
    Day 11/12 validator in this codebase already follows. Never mutates
    `candidate`/`gate_c_validated_evidence`/`policy`."""
    validated_by_id = {record.evidence_id: record for record in gate_c_validated_evidence}

    reason_codes: list[CitationReasonCode] = []

    if (
        policy.answered_requires_citation
        and candidate.candidate_status is AnswerStatus.ANSWERED
        and not candidate.candidate_citations
    ):
        reason_codes.append(CitationReasonCode.MISSING_REQUIRED_CITATION)

    citation_checks = tuple(
        _check_one_citation(
            citation,
            validated_by_id=validated_by_id,
            citations_must_be_gate_c_approved=policy.citations_must_be_gate_c_approved,
        )
        for citation in candidate.candidate_citations
    )

    valid_ids = tuple(check.evidence_id for check in citation_checks if check.valid)
    invalid_ids = tuple(check.evidence_id for check in citation_checks if not check.valid)

    if invalid_ids:
        if valid_ids and policy.reject_mixed_valid_invalid:
            reason_codes.append(CitationReasonCode.MIXED_VALID_INVALID_CITATIONS)
        # Every per-citation failure reason also belongs at the
        # candidate level, deduplicated and in first-seen order, so a
        # caller reading `CitationReconciliationReport.reason_codes` alone
        # never has to re-walk `citation_checks` to learn *why* --
        # deliberately preserved even though `invalid_ids` alone already
        # decides `passed` below.
        for check in citation_checks:
            for reason in check.reasons:
                if reason not in reason_codes:
                    reason_codes.append(reason)

    passed = not reason_codes

    return CitationReconciliationReport(
        passed=passed,
        reason_codes=tuple(reason_codes),
        citation_checks=citation_checks,
        valid_citation_evidence_ids=valid_ids,
        invalid_citation_evidence_ids=invalid_ids,
    )


# ══════════════════════════════════════════════════════════════════════
# Day 12 Task 6 -- final disclosure validation (field/profile-driven half).
# ══════════════════════════════════════════════════════════════════════
#
# "A model may accidentally reproduce content that the Day 10 disclosure
# policy says must not leave the system. Gate-D must validate the actual
# final candidate. Use deterministic disclosure rules ... Do not use an
# LLM as the final disclosure authority" (Task 6). `check_final_disclosure()`
# is that validation: it never asks a model whether `candidate.
# candidate_answer` is safe -- it resolves, deterministically, what *should*
# have been disclosed for this request (reusing Day 10's own machinery
# wholesale, never a second, competing implementation -- structure rule:
# "Gate-D may reuse Day 5 citation validation and Day 10 disclosure/
# redaction functions rather than reimplementing them inconsistently"),
# then checks whether the final generated text still contains a raw
# protected value that resolution says should never have appeared
# unredacted.
#
# Two of Task 6's four "Required checks" bullets, traced to where they are
# enforced:
#
#     - denied field values do not
#       appear                          -> `DisclosureReasonCode.
#                                            DENIED_FIELD_VALUE_LEAKED`,
#                                            below: a protected field whose
#                                            resolved `DisclosureAction` is
#                                            `DENY`, whose raw
#                                            `ProtectedField.value` still
#                                            appears verbatim in
#                                            `candidate.candidate_answer`.
#     - fields requiring redaction are
#       not returned unredacted        -> `DisclosureReasonCode.
#                                            REDACTABLE_FIELD_VALUE_LEAKED`,
#                                            below: the identical check,
#                                            for a resolved action of
#                                            `REDACT` instead of `DENY` --
#                                            `disclosure_leak_cases.json`
#                                            DISC12-002's own distinction
#                                            from DISC12-003/DISC12-004
#                                            (`policy_reader` resolves
#                                            `contact_email` to `redact`,
#                                            `tax_identifier` to `deny`).
#
# The remaining two bullets -- "raw PII known to the protected candidate
# data cannot reappear in output" and "authorization claims, access
# tokens, secrets and internal policy data cannot appear" /
# "hidden/system prompt content is not released" -- are deliberately split
# across two different mechanisms, not both implemented here:
#
#     - "raw PII ... cannot reappear"   -> this is the *general principle*
#                                            the two reason codes above
#                                            already implement concretely,
#                                            not a third, separate check:
#                                            between `DENY`/`REDACT`, every
#                                            protected field this policy
#                                            version has not explicitly
#                                            authorized (`ALLOW`) for this
#                                            profile is covered. A field
#                                            genuinely resolved to `ALLOW`
#                                            (`disclosure_leak_cases.json`
#                                            DISC12-001's own
#                                            `supplier_name`/
#                                            `payment_terms`, both `allow`
#                                            under `policy_reader`) is, by
#                                            construction, authorized to
#                                            appear -- not a leak.
#     - "authorization claims, access
#       tokens, secrets ..." /
#       "hidden/system prompt content"  -> Task 7's own dedicated
#                                            deterministic detector
#                                            (`policy.block_secret_patterns`
#                                            / `policy.
#                                            block_hidden_prompt_markers`,
#                                            already typed on
#                                            `GateDDisclosurePolicy`, Task
#                                            2) -- these are not
#                                            per-request *protected field
#                                            values* Gate-B's disclosure
#                                            profile has any opinion about
#                                            at all (`disclosure_leak_
#                                            cases.json` DISC12-005/
#                                            DISC12-006 both omit
#                                            `disclosure_profile`
#                                            entirely), they are fixed
#                                            synthetic patterns Task 7
#                                            scans for unconditionally.
#                                            `check_final_disclosure()`
#                                            below correctly reports
#                                            `passed=True` for both of
#                                            those two cases in isolation
#                                            (nothing in scope here to
#                                            flag) -- Task 7's own function
#                                            is what actually fails them;
#                                            Task 10's decision contract
#                                            combines both reports.
#
# `policy.enforce_gate_b_profile=False` skips this check entirely
# (`passed=True` unconditionally) -- the policy has decided this lane does
# not re-validate against Gate-B's own profile at the release boundary,
# the identical "named boolean switch, honored literally" posture
# `policy.citations_must_be_gate_c_approved` already gets in Task 3.
# `disclosure_profile=None` (Gate-B resolved none for this request) is
# likewise not a failure -- `apply_disclosure()` itself already returns an
# empty view for that case (its own "no fall-through" guarantee, Day 10
# Task 9), so there is nothing here to check either.
#
# ## Unknown disclosure profile reference (Task 2's own cross-check)
#
# `gate_d_policy_requirements.md`'s "unknown disclosure profile/rule
# reference rejected" is a *candidate*-level check, not a static-document
# one (Task 2's own `GateDPolicyDocument` names no specific profile id
# itself to validate at load time -- see `policy_models.py`'s "Day 12 Task
# 2" section). `check_final_disclosure()` below is where it is actually
# enforced: `candidate.gate_b_disclosure_profile` (Task 1's own envelope
# field) is cross-checked against `known_disclosure_profile_ids` -- the
# same governed universe `GateDPolicyRegistry.has_disclosure_profile()`
# already exposes, forwarded in by `GateD.evaluate()` (Task 10) from its
# own loaded registry. A candidate naming a profile id that registry does
# not recognize is `UNKNOWN_DISCLOSURE_PROFILE_REFERENCE` -- a malformed/
# incoherent envelope Gate-D must never trust implicitly, exactly the same
# "a claim the candidate makes is not proof" posture this module's own
# citation reconciliation (Task 3) already takes toward a forged
# `evidence_id`. `known_disclosure_profile_ids=None` (the default) means
# "no context supplied, this cross-check simply does not run" -- the
# identical allowance `has_disclosure_profile()`'s own docstring already
# gives, so every existing direct caller of this function (this module's
# own test file included) keeps working unchanged.


class DisclosureReasonCode(str, Enum):
    """The closed set of reasons `check_final_disclosure()` ever cites.
    Gate-D's full decision contract (Task 10) is expected to fold these
    into its own typed `reason_codes`, never invent a new, undocumented
    disclosure-failure string."""

    DENIED_FIELD_VALUE_LEAKED = "denied_field_value_leaked"
    REDACTABLE_FIELD_VALUE_LEAKED = "redactable_field_value_leaked"
    UNKNOWN_DISCLOSURE_PROFILE_REFERENCE = "unknown_disclosure_profile_reference"


class DisclosureFieldCheckResult(BaseModel):
    """Disclosure's per-field verdict: `field_name` this result is for,
    the `DisclosureAction` Day 10's own `apply_disclosure()` resolved for
    it, whether that field's raw value still leaked into the final text
    despite that resolution, and -- when it did -- why (mirrors
    `gate_d.CitationCheckResult`'s shape one section over)."""

    model_config = ConfigDict(extra="forbid")

    field_name: str = Field(min_length=1)
    action: DisclosureAction
    leaked: bool
    reason: DisclosureReasonCode | None = Field(default=None)


class DisclosureCheckReport(BaseModel):
    """The full candidate-level disclosure result:
    `check_final_disclosure()`'s one return value. `leaked_field_names`
    is populated regardless of the overall `passed` outcome -- Task 13's
    observability and Task 14's `disclosure_latency_report.md` both want
    to see exactly which fields leaked (by name only, never by value --
    see `check_final_disclosure()`'s own docstring), the identical
    "populated regardless of the final decision" convention
    `CitationReconciliationReport`'s own `invalid_citation_evidence_ids`
    already follows."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    reason_codes: tuple[DisclosureReasonCode, ...] = Field(default_factory=tuple)
    field_checks: tuple[DisclosureFieldCheckResult, ...] = Field(default_factory=tuple)
    leaked_field_names: tuple[str, ...] = Field(default_factory=tuple)


def check_final_disclosure(
    candidate: FinalResponseCandidate,
    *,
    gate_b_decision: GateBDecision,
    disclosure_profile: DisclosureProfile | None,
    protected_fields: Sequence[ProtectedField],
    policy: GateDDisclosurePolicy,
    known_disclosure_profile_ids: frozenset[str] | None = None,
) -> DisclosureCheckReport:
    """Validate `candidate.candidate_answer` against what Day 10's own
    `apply_disclosure()` resolves *should* have been disclosed for this
    request -- never a second, hand-rolled resolution of
    `gate_b_decision`/`disclosure_profile`/`protected_fields` (structure
    rule: reuse Day 10's disclosure functions, do not reimplement them).
    Never raises for an ordinary input -- every outcome is a normal, typed
    `DisclosureCheckReport`. Never mutates `candidate`/`gate_b_decision`/
    `disclosure_profile`/`protected_fields`/`policy`, and never logs or
    returns a raw protected value -- only field names and the governed
    `DisclosureAction` each resolved to (working rule: "Gate-D telemetry
    must remain sanitized").

    `known_disclosure_profile_ids` is Task 2's own governed universe
    (`GateDPolicyRegistry.known_disclosure_profile_ids`) -- when supplied,
    `candidate.gate_b_disclosure_profile` naming anything outside it is
    `UNKNOWN_DISCLOSURE_PROFILE_REFERENCE` (see module docstring's "Unknown
    disclosure profile reference" section). `None` (the default) means no
    registry context was supplied -- this one cross-check simply does not
    run, the identical allowance `has_disclosure_profile()` already gives,
    so a caller exercising the field-leak checks in isolation (this
    module's own test file included) is unaffected."""
    if not policy.enforce_gate_b_profile:
        return DisclosureCheckReport(passed=True)

    unknown_profile_reasons: tuple[DisclosureReasonCode, ...] = ()
    if (
        candidate.gate_b_disclosure_profile is not None
        and known_disclosure_profile_ids is not None
        and candidate.gate_b_disclosure_profile not in known_disclosure_profile_ids
    ):
        unknown_profile_reasons = (DisclosureReasonCode.UNKNOWN_DISCLOSURE_PROFILE_REFERENCE,)

    disclosed_view = apply_disclosure(gate_b_decision, disclosure_profile, protected_fields)
    original_values_by_name = {field.name: field.value for field in protected_fields}

    field_checks: list[DisclosureFieldCheckResult] = []
    for disclosed_field in disclosed_view.fields:
        original_value = original_values_by_name[disclosed_field.name]
        leaked = (
            disclosed_field.action is not DisclosureAction.ALLOW
            and bool(original_value)
            and original_value in candidate.candidate_answer
        )
        reason = None
        if leaked:
            reason = (
                DisclosureReasonCode.DENIED_FIELD_VALUE_LEAKED
                if disclosed_field.action is DisclosureAction.DENY
                else DisclosureReasonCode.REDACTABLE_FIELD_VALUE_LEAKED
            )
        field_checks.append(
            DisclosureFieldCheckResult(
                field_name=disclosed_field.name, action=disclosed_field.action, leaked=leaked, reason=reason
            )
        )

    leaked_field_names = tuple(check.field_name for check in field_checks if check.leaked)
    reason_codes = tuple(
        dict.fromkeys((*unknown_profile_reasons, *(check.reason for check in field_checks if check.reason is not None)))
    )

    return DisclosureCheckReport(
        passed=not leaked_field_names and not unknown_profile_reasons,
        reason_codes=reason_codes,
        field_checks=tuple(field_checks),
        leaked_field_names=leaked_field_names,
    )


# ══════════════════════════════════════════════════════════════════════
# Day 12 Task 7 -- deterministic secret / protected-value detection.
# ══════════════════════════════════════════════════════════════════════
#
# "Use controlled synthetic protected values from the resource pack. The
# validator must be able to detect explicit leak cases ... This is not a
# request to build a universal DLP platform. Implement the defined Day 12
# deterministic policy scope" (Task 7). `detect_protected_value_leak()` is
# that validator: unlike Task 6's `check_final_disclosure()` (governed by
# a per-request Gate-B disclosure profile and a caller-supplied set of
# protected field values), this one is profile-independent and
# self-contained -- it needs nothing beyond the candidate's own text and
# `policy.block_secret_patterns`/`policy.block_hidden_prompt_markers`
# (`GateDDisclosurePolicy`, Task 2). That independence is exactly what
# `disclosure_leak_cases.json` DISC12-005/DISC12-006 need: both omit
# `disclosure_profile` entirely (there is no per-field policy to consult
# at all), yet both must still fail -- only a check with no profile
# dependency can catch them.
#
# Task 7's three named examples (`SYN-BANK-00001234` / `SYN-ID-123456` /
# `Bearer SYNTHETIC_SECRET_TOKEN`) are read as *pattern classes*, not
# three literal strings to `==`-compare against -- `_SECRET_PATTERNS`
# below generalizes each to its structural shape (a `SYN-BANK-`/`SYN-ID-`
# prefix followed by digits; a `Bearer` token carrying this lab's own
# `SYNTHETIC_` naming convention) while staying deliberately narrow to
# that one governed convention -- never a generic "looks like a secret"
# heuristic (Task 7's own "not a universal DLP platform" instruction,
# taken literally: this scans for exactly the controlled synthetic
# families the resource pack defines, nothing broader). The hidden-prompt
# marker (`block_hidden_prompt_markers`) is a fourth, independent pattern,
# tracked separately because it is a *different* policy switch (Task 2)
# from `block_secret_patterns` -- a policy version could enable one
# without the other.
#
# For `SYN-BANK-`/`SYN-ID-` specifically, this check is intentionally
# *redundant* with Task 6's own field-based `DENIED_FIELD_VALUE_LEAKED`
# (`disclosure_leak_cases.json` DISC12-003/DISC12-004 trip both checks
# independently) -- defense in depth, not a bug: two independently-reasoned
# checks agreeing a value must not appear is strictly safer than one,
# and Task 10's decision contract treats either failing as sufficient
# reason to withhold release.
#
# "Normal telemetry must not print the matched protected value" -- see
# `SecretDetectionReport`'s own docstring: `matched_pattern_names` is a
# fixed, closed set of *pattern identifiers* this module itself names
# (`"synthetic_bank_account"`, not the digits that actually matched); the
# raw matched substring is discarded the instant `re.search()` returns,
# never stored on the report, logged, or returned to a caller.

# The one place each governed synthetic secret pattern is defined --
# Task 10's decision contract and this module's own tests reuse these by
# name, never a second, hand-copied regex.
_SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "synthetic_bank_account": re.compile(r"SYN-BANK-\d+"),
    "synthetic_tax_identifier": re.compile(r"SYN-ID-\d+"),
    "synthetic_bearer_token": re.compile(r"Bearer\s+SYNTHETIC_\S+"),
}
_HIDDEN_PROMPT_MARKER_NAME = "hidden_prompt_marker"
_HIDDEN_PROMPT_MARKER_PATTERN = re.compile(r"SYSTEM_INTERNAL_RULE_DO_NOT_EXPOSE")


class SecretReasonCode(str, Enum):
    """The closed set of reasons `detect_protected_value_leak()` ever
    cites. Gate-D's full decision contract (Task 10) is expected to fold
    these into its own typed `reason_codes`, never invent a new,
    undocumented secret-detection failure string."""

    SYNTHETIC_SECRET_PATTERN_DETECTED = "synthetic_secret_pattern_detected"
    HIDDEN_PROMPT_MARKER_DETECTED = "hidden_prompt_marker_detected"


class SecretDetectionReport(BaseModel):
    """`detect_protected_value_leak()`'s one return value.
    `matched_pattern_names` names *which governed pattern* matched
    (`_SECRET_PATTERNS`' own keys, plus `"hidden_prompt_marker"`) --
    never the matched text itself, never even the field/location it
    matched at (there is none -- this check has no field concept, unlike
    `DisclosureCheckReport.leaked_field_names`). Populated regardless of
    the overall `passed` outcome, the identical "populated regardless of
    the final decision" convention every other Day 12 report in this
    module already follows."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    reason_codes: tuple[SecretReasonCode, ...] = Field(default_factory=tuple)
    matched_pattern_names: tuple[str, ...] = Field(default_factory=tuple)


def detect_protected_value_leak(
    candidate: FinalResponseCandidate,
    *,
    policy: GateDDisclosurePolicy,
) -> SecretDetectionReport:
    """Scan `candidate.candidate_answer` for the governed synthetic
    secret/hidden-prompt-marker patterns this Day 12 policy scope defines
    -- see module docstring for exactly which patterns and why. Never
    raises for an ordinary input -- every outcome is a normal, typed
    `SecretDetectionReport`. Never mutates `candidate`/`policy`, calls the
    Model Gateway, or consults any external service -- a deterministic,
    local regex scan only (working rule: "Gate-D does not ask an LLM
    whether the response is safe to release"). Never logs, stores, or
    returns the matched substring itself -- see `SecretDetectionReport`'s
    own docstring."""
    matched_pattern_names: list[str] = []
    reason_codes: list[SecretReasonCode] = []

    if policy.block_secret_patterns:
        secret_matches = [name for name, pattern in _SECRET_PATTERNS.items() if pattern.search(candidate.candidate_answer)]
        if secret_matches:
            matched_pattern_names.extend(secret_matches)
            reason_codes.append(SecretReasonCode.SYNTHETIC_SECRET_PATTERN_DETECTED)

    if policy.block_hidden_prompt_markers and _HIDDEN_PROMPT_MARKER_PATTERN.search(candidate.candidate_answer):
        matched_pattern_names.append(_HIDDEN_PROMPT_MARKER_NAME)
        reason_codes.append(SecretReasonCode.HIDDEN_PROMPT_MARKER_DETECTED)

    return SecretDetectionReport(
        passed=not reason_codes,
        reason_codes=tuple(reason_codes),
        matched_pattern_names=tuple(matched_pattern_names),
    )


# ══════════════════════════════════════════════════════════════════════
# Day 12 Task 8 -- latency-budget policy.
# ══════════════════════════════════════════════════════════════════════
#
# Task 8's own two required timing checks -- "invalid negative timing" /
# "missing required timing" -- are *not* implemented here: Task 1's
# envelope already rejects both by construction (`FinalResponseCandidate.
# elapsed_ms`/`model_latency_ms` are `Field(ge=0)`, required, no
# default), and `latency_budget_cases.json` LAT12-005
# ("invalid_negative_timing") itself expects `reject` -- Task 10's
# documented meaning for "invalid internal candidate ... that should not
# be exposed as normal answer" -- exactly the outcome a malformed
# envelope (one that can never even become a `FinalResponseCandidate`)
# already produces upstream of this function ever running. `check_latency
# _budget()` below is therefore only ever called with an already-valid,
# non-negative `elapsed_ms`/`model_latency_ms` -- the identical division
# of labor `check_final_quality()`'s own docstring draws for "unknown
# final status" (a Task 1 shape guarantee, not re-checked at Task 5).
#
# The remaining four required cases -- within budget, model budget
# exceeded, total budget exceeded, exactly at threshold -- are all
# `check_latency_budget()`'s own job, governed entirely by
# `LatencyBudgets` (Task 2): `threshold_is_inclusive` decides whether a
# candidate measured at *exactly* `max_total_latency_ms`/
# `max_model_latency_ms` passes (`true`, the committed v1 value --
# `latency_budget_cases.json` LAT12-002) or must be strictly under
# (`false` -- no shipped fixture exercises this direction, so this
# module's own test file adds a dedicated case).
#
# ## "if policy says the budget is hard"
#
# The working rule ("A successful model call does not override an
# exceeded hard latency budget") and Task 8's own "Rule" both qualify
# this with "if policy says the budget is hard" -- read literally against
# what `LatencyBudgets` (Task 2) actually governs: this policy version
# declares no separate soft/hard distinction at all, only the two budget
# values themselves. A policy that declares a budget at all, with no
# accompanying "this one is merely advisory" field, is declaring a hard
# one -- there is no code path in `check_latency_budget()` that could
# ever treat an exceeded budget as anything but a release-blocking
# failure; a future policy version wanting genuinely *soft* (advisory,
# non-blocking) budgets would need its own new, explicit field, not a
# silent default here.
#
# "Do not fabricate faster telemetry": `check_latency_budget()` reads
# `candidate.elapsed_ms`/`candidate.model_latency_ms` exactly as the
# envelope carries them -- no rounding, no recomputation, no adjustment
# in either direction. "Optionally support stage budgets if documented":
# `gate_d_policy_v1.json` documents none beyond the total/model pair, so
# none are implemented -- a future policy version that adds one extends
# `LatencyBudgets` (Task 2) and this function together, the same way any
# other governed field addition in this codebase is made.


class LatencyReasonCode(str, Enum):
    """The closed set of reasons `check_latency_budget()` ever cites.
    Gate-D's full decision contract (Task 10) is expected to fold these
    into its own typed `reason_codes`, never invent a new, undocumented
    latency-failure string."""

    TOTAL_LATENCY_BUDGET_EXCEEDED = "total_latency_budget_exceeded"
    MODEL_LATENCY_BUDGET_EXCEEDED = "model_latency_budget_exceeded"


class LatencyCheckReport(BaseModel):
    """`check_latency_budget()`'s one return value. Carries the measured
    values and the budget they were checked against alongside the
    verdict -- Task 13's observability wants `total_latency_ms`/
    `model_latency_ms` on every decision regardless of outcome, and
    repeating them here means a caller never has to re-read them off the
    original `candidate` to build that telemetry."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    reason_codes: tuple[LatencyReasonCode, ...] = Field(default_factory=tuple)
    total_latency_ms: int = Field(ge=0)
    model_latency_ms: int = Field(ge=0)
    max_total_latency_ms: int = Field(gt=0)
    max_model_latency_ms: int = Field(gt=0)


def check_latency_budget(candidate: FinalResponseCandidate, *, policy: LatencyBudgets) -> LatencyCheckReport:
    """Check `candidate.elapsed_ms`/`candidate.model_latency_ms` against
    `policy`'s governed budgets -- see module docstring for why negative/
    missing timing is out of scope here (a Task 1 envelope guarantee) and
    for the "hard budget" / "no fabricated telemetry" reading. Never
    raises for an ordinary input -- every outcome is a normal, typed
    `LatencyCheckReport`. Never mutates `candidate`/`policy`, and performs
    no I/O -- a pure function of its typed inputs, exactly like this
    module's other Day 12 checks."""
    if policy.threshold_is_inclusive:
        total_exceeded = candidate.elapsed_ms > policy.max_total_latency_ms
        model_exceeded = candidate.model_latency_ms > policy.max_model_latency_ms
    else:
        total_exceeded = candidate.elapsed_ms >= policy.max_total_latency_ms
        model_exceeded = candidate.model_latency_ms >= policy.max_model_latency_ms

    reason_codes: list[LatencyReasonCode] = []
    if total_exceeded:
        reason_codes.append(LatencyReasonCode.TOTAL_LATENCY_BUDGET_EXCEEDED)
    if model_exceeded:
        reason_codes.append(LatencyReasonCode.MODEL_LATENCY_BUDGET_EXCEEDED)

    return LatencyCheckReport(
        passed=not reason_codes,
        reason_codes=tuple(reason_codes),
        total_latency_ms=candidate.elapsed_ms,
        model_latency_ms=candidate.model_latency_ms,
        max_total_latency_ms=policy.max_total_latency_ms,
        max_model_latency_ms=policy.max_model_latency_ms,
    )


# ══════════════════════════════════════════════════════════════════════
# Day 12 Task 9 -- safe failure behavior.
# ══════════════════════════════════════════════════════════════════════
#
# "When Gate-D cannot release the candidate, return a controlled typed
# failure" (Task 9). `SafeFailureResponse` is that controlled shape;
# `build_safe_failure_response()` is the only thing that ever constructs
# one. Task 9's own "Safe failure must not include" list -- unsafe
# candidate answer / leaked protected value / raw model response / raw
# evidence / policy internals / stack trace -- is enforced primarily at
# the *type* level, not by remembering to scrub each of those at every
# call site:
#
#     - `SafeFailureResponse` has no field capable of carrying any of
#       them in the first place -- `extra="forbid"` closes off an
#       accidental sixth field, and the five fields it does declare
#       (`status`/`error_code`/`message`/`request_id`/`correlation_id`/
#       `reason_codes`) are each individually safe by construction (see
#       below). There is structurally no way to hand this type a
#       candidate answer, an evidence record, or a traceback and have it
#       accept the assignment.
#     - `build_safe_failure_response()` does not even *accept* a
#       `FinalResponseCandidate` -- only the two bare identifier strings
#       (`request_id`/`correlation_id`) a caller extracts from one. This
#       is deliberately a stronger guarantee than "the function body
#       happens not to touch `candidate.candidate_answer`": the answer
#       text, citations, and evidence ids are never in scope at all, so
#       no future edit to this function could accidentally start
#       forwarding them.
#     - `error_code`/`message` come only from the governed `SafeFailureSpec`
#       (Task 2, `policy/gate_d_policy.v1.json`'s own `safe_failure`
#       object) -- fixed, policy-authored text, never anything derived
#       from the candidate or an exception (working rule: "Safe failure
#       text itself must be fixed/controlled and must not echo unsafe
#       generated content"). `status` is independently pinned to the
#       literal `"safe_failure"` a second time here (mirroring
#       `SafeFailureSpec.status`'s own pin) so this type alone, without
#       even consulting the policy object, can never represent anything
#       but a safe failure.
#     - `reason_codes` accepts only this module's own governed enum
#       values (`_KNOWN_REASON_CODE_VALUES`, below -- the union of every
#       `CitationReasonCode`/`QualityReasonCode`/`DisclosureReasonCode`/
#       `SecretReasonCode`/`LatencyReasonCode` member) -- "Reason codes
#       may be safe high-level enums" (Task 9) enforced as a hard
#       rejection, not a naming convention a caller could still violate
#       by passing free text (e.g. a raw exception message) instead.
#
# `build_safe_failure_response()` never raises for an ordinary input, and
# performs no I/O, no logging, and no Model Gateway call -- it is a pure
# assembly of already-governed values (working rule: "Gate-D does not
# repair authorization, citations, disclosure or latency violations with
# another model call").

# Every reason code this module's own checks (Tasks 3/5/6/7/8) can ever
# produce -- the one closed universe `SafeFailureResponse.reason_codes`
# validates against. Built once, from the enums themselves, so this set
# can never drift out of sync with what those checks actually emit.
_KNOWN_REASON_CODE_VALUES: frozenset[str] = frozenset(
    member.value
    for enum_cls in (CitationReasonCode, QualityReasonCode, DisclosureReasonCode, SecretReasonCode, LatencyReasonCode)
    for member in enum_cls
)


class SafeFailureResponse(BaseModel):
    """The controlled typed failure Gate-D returns when a candidate
    cannot be released -- Task 9's own "Conceptual result" shape
    (`status`/`error_code`/`request_id`/`correlation_id`), plus
    `message` (the governed `SafeFailureSpec.message`) and `reason_codes`
    (safe, high-level enum values only). See module docstring for why
    each field is safe by construction. `request_id`/`correlation_id` are
    still ordinary caller-supplied identifiers, not secret -- carrying
    them is what lets a caller correlate a safe failure back to its own
    request (Task 13's observability), the identical non-secret status
    every other decision/report in this module already gives them."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["safe_failure"] = Field(description="Pinned -- this type can never represent anything else.")
    error_code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    reason_codes: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes_are_governed(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = [code for code in value if code not in _KNOWN_REASON_CODE_VALUES]
        if unknown:
            raise ValueError(f"reason_codes must be governed Gate-D reason-code values, got: {unknown}")
        return value


def build_safe_failure_response(
    *,
    request_id: str,
    correlation_id: str,
    policy: SafeFailureSpec,
    reason_codes: Sequence[str] = (),
) -> SafeFailureResponse:
    """Build the one typed safe-failure result Gate-D ever returns.
    Deliberately takes only bare identifier strings, never a
    `FinalResponseCandidate` -- see module docstring. `reason_codes` is
    typically the concatenation of whichever of this module's own checks
    (Task 3/5/6/7/8) actually failed, each already reduced to its
    enum's `.value`; rejected outright (via `SafeFailureResponse`'s own
    validator) if any entry is not one of this module's governed reason
    codes."""
    return SafeFailureResponse(
        status=policy.status,
        error_code=policy.code,
        message=policy.message,
        request_id=request_id,
        correlation_id=correlation_id,
        reason_codes=tuple(reason_codes),
    )


# Day 12 Task 13 -- same pattern `gate_c.py` already uses and documents:
# `opentelemetry.trace.get_tracer(__name__)` directly, never importing
# `aico.observability` here. Declared here, not at module top, since
# `GateD.evaluate()` (below) is the only thing in this whole module that
# is ever traced -- every Task 3/5/6/7/8/9 check above stays pure/
# span-free, the same "no I/O, no tracing import of their own" discipline
# `disclosure.py`/`redaction.py` already commit to; `GateD.evaluate()`
# itself is a multi-stage orchestrator over them, the identical reason
# `GateC.evaluate()` traces itself directly instead of leaving it to an
# outer caller.
_tracer = trace.get_tracer(__name__)


# ══════════════════════════════════════════════════════════════════════
# Day 12 Task 10 -- the Gate-D decision contract.
# Day 12 Task 13 -- observability / audit metadata (folded into
# `GateD.evaluate()` itself, not a separate function -- see immediately
# above for why, the identical reasoning `gate_c.py`'s own module
# docstring gives for folding its Task 14 in the same way).
# ══════════════════════════════════════════════════════════════════════
#
# `GateD.evaluate()` is the one place every Task 3-9 check actually runs
# together, the same role `GateC.evaluate()` (Day 11 Task 9) plays over
# Day 11 Tasks 4-8's own validators -- this module's "Provenance"/"Reject
# vs. safe_failure"/etc. sections above are each already proven
# independently by their own task's function; this section is only the
# combination.
#
# ## Inputs Gate-D consumes (Task 10's own list)
#
#     - Gate-B disclosure context     -> `gate_b_decision: GateBDecision`
#                                         + `disclosure_profile:
#                                         DisclosureProfile | None` (the
#                                         resolved profile
#                                         `gate_b_decision.
#                                         disclosure_profile` names, when
#                                         one does) + `protected_fields:
#                                         Sequence[ProtectedField]` (the
#                                         protected candidate data
#                                         underlying this request -- Task
#                                         6's own inputs, unchanged).
#     - Gate-C validated evidence
#       metadata                     -> `gate_c_validated_evidence:
#                                         Sequence[GateCEvidenceRecord]`
#                                         (Task 3's own input, unchanged).
#     - typed candidate response      -> `candidate: FinalResponseCandidate`
#                                         (Task 1). Always already
#                                         well-formed by the time it
#                                         reaches here -- a malformed
#                                         payload never becomes one in the
#                                         first place
#                                         (`parse_final_response_candidate()`
#                                         raises `FinalResponseEnvelopeError`
#                                         upstream of Gate-D entirely, Task
#                                         11's own integration boundary);
#                                         `GateD.evaluate()` therefore has
#                                         no separate "malformed candidate"
#                                         branch of its own to reject --
#                                         Task 10's `reject` outcome is
#                                         reached only through a
#                                         *well-formed* candidate that
#                                         still fails quality's own
#                                         reject-level checks (an
#                                         already-failed typed contract/
#                                         semantic validation, an
#                                         unsupported status, or a
#                                         status/answer inconsistency).
#     - timing metadata                -> already part of `candidate`
#                                         (`elapsed_ms`/`model_latency_ms`,
#                                         Task 1) -- not a separate
#                                         parameter.
#     - Gate-D policy                  -> `self.policy_registry:
#                                         GateDPolicyRegistry` (Task 2),
#                                         resolved once at construction,
#                                         reused for every `evaluate()`
#                                         call -- the identical "built
#                                         once against loaded registries,
#                                         reused per request" pattern
#                                         `GateC`'s own docstring
#                                         describes.
#
# ## Decision algorithm
#
# Every check runs unconditionally, always, regardless of any other
# check's outcome -- there is no structural reason to short-circuit (each
# check's own inputs are already fully resolved before `evaluate()` is
# even called, unlike Gate-C's own Gate-B-ALLOW precondition), and running
# all of them gives Task 13's observability and Task 14's artifacts the
# complete picture of every violation a candidate carries at once, not
# only the first one found:
#
#   1. `check_final_quality()` (Task 5) -- its own `severity` already
#      distinguishes `reject` from `safe_failure` per-reason; see that
#      module's own docstring.
#   2. `reconcile_final_citations()` (Task 3/4) -- never produces
#      `reject`, only pass/fail (`safe_failure`-level whenever it fails).
#   3. `check_final_disclosure()` (Task 6) + `detect_protected_value_leak()`
#      (Task 7), combined into one `FinalDisclosureReport` -- Task 10
#      names a single `disclosure_checks` field; neither sub-check ever
#      produces `reject` either.
#   4. `check_latency_budget()` (Task 8) -- never produces `reject`
#      either; "a hard latency budget is exceeded" is, by this module's
#      own reading (Task 8's "Rule" section), always a `safe_failure`,
#      never grounds for `reject` (a slow-but-otherwise-valid candidate is
#      not an internal-candidate/programming-policy error).
#
# Combining: `quality_checks.severity is REJECT` -> overall `reject`,
# unconditionally (the identical "reject outranks safe_failure" rule
# `quality.py`'s own `_overall_severity()` already applies within Task 5
# alone, extended here across all four checks -- only quality's own check
# can ever produce a `reject`-level reason at all, so this is really the
# same rule, not a new one). Otherwise: any check failing at all ->
# overall `safe_failure`. Every check passing -> `allow`.
#
# `reason_codes` accumulates every reason every check actually produced
# (each already a `str` via `.value`), deduplicated in first-seen order --
# never only the first failure found, matching this module's own
# per-check reports' identical "every reason, not just one" convention.
# `validated_citation_ids` and `safe_failure_code` are populated only for
# their own respective outcome (`allow` / `safe_failure`) -- the identical
# least-privilege "empty/`None` unless the decision actually resolves to
# the state that grants it" posture `GateBDecision`/`GateCDecision` already
# establish for their own analogous fields.
#
# ## Task 13 -- observability / audit metadata
#
# `evaluate()` runs entirely inside one `"gate_d"` span (opened once, at
# the top of the method); `"final_citation_validation"`/
# `"final_disclosure_validation"`/`"latency_budget_validation"` are its
# own nested child spans, wrapping the Task 3/6-7/8 calls respectively --
# Task 13's own four named spans, no more, no fewer (there is
# deliberately no fifth "final_quality_validation" span: Task 13 names
# only these four, the identical "only the sub-stages the assignment
# actually names get their own span" restraint `gate_c.py`'s own Task 14
# section already applies -- Task 5's own quality check still runs, just
# without a dedicated child span of its own). The pure check functions
# above (`check_final_quality`/`reconcile_final_citations`/
# `check_final_disclosure`/`detect_protected_value_leak`/
# `check_latency_budget`) stay entirely span-free themselves -- `GateD.
# evaluate()` is the one orchestration layer that adds tracing around them,
# the identical split `gate_c.py`'s own Task 14 section documents for its
# four Day 11 validator modules.
#
# Every attribute the `"gate_d"` span itself carries, traced to Task 13's
# own "Useful fields" list:
#
#     - request_id / correlation_id   -> deliberately NOT set here, the
#                                         identical omission `gate_c.py`'s
#                                         own Task 14 section documents
#                                         for the identical reason: Day 6
#                                         Task 9's ambient-current-span
#                                         mechanism already carries both on
#                                         whatever root span a caller
#                                         (`api/control_plane.py`'s
#                                         `"api.ask_governed"`) has open
#                                         around this call -- every span
#                                         opened here, including `"gate_d"`
#                                         itself, becomes that span's own
#                                         child and shares its `trace_id`
#                                         automatically, without needing
#                                         either id passed in as a
#                                         parameter or repeated as an
#                                         attribute.
#     - ontology_version /
#       gate_b_policy_version /
#       gate_c_policy_version         -> also deliberately NOT set here --
#                                         `GateD` has no `OntologyRegistry`/
#                                         Gate-B `PolicyRegistry`/Gate-C
#                                         `GateCPolicyRegistry` of its own
#                                         to read them from (the identical
#                                         reason `gate_c.py` never sets
#                                         `ontology_version`/`gate_b_
#                                         policy_version` on its own span
#                                         either, despite also receiving a
#                                         real `GateBDecision`) -- all
#                                         three already appear on the
#                                         sibling `"gate_a"`/`"gate_b"`/
#                                         `"gate_c"` spans a real caller
#                                         opens in the same trace.
#     - gate_d_policy_version         -> `gate_d.policy_version`,
#                                         `self.policy_registry.
#                                         policy_version` -- the one policy
#                                         version this class genuinely
#                                         owns.
#     - candidate_status              -> `gate_d.candidate_status`,
#                                         `candidate.candidate_status.value`
#                                         (a governed enum value, never
#                                         the answer text itself).
#     - citation_count /
#       validated_citation_count      -> `gate_d.citation_count` (every
#                                         citation the candidate carried,
#                                         valid or not) / `gate_d.
#                                         validated_citation_count` (how
#                                         many of those actually
#                                         reconciled) -- counts only, never
#                                         the citations themselves.
#     - quality_result /
#       disclosure_result /
#       latency_result                -> `gate_d.quality_result` (`
#                                         "passed"`, or the failing
#                                         `QualityFailureSeverity` value --
#                                         `"safe_failure"`/`"reject"`) /
#                                         `gate_d.disclosure_result` /
#                                         `gate_d.latency_result` (`
#                                         "passed"`/`"failed"` for the
#                                         latter two, which never carry a
#                                         reject-level severity of their
#                                         own) -- a sanitized summary
#                                         label per sub-check, never the
#                                         sub-check's own detailed report.
#     - total_latency_ms /
#       model_latency_ms              -> `gate_d.total_latency_ms`/
#                                         `gate_d.model_latency_ms`,
#                                         `candidate.elapsed_ms`/
#                                         `candidate.model_latency_ms`
#                                         unchanged -- real measured
#                                         numbers, never recomputed here
#                                         (Task 8's "do not fabricate
#                                         faster telemetry", extended to
#                                         telemetry itself).
#     - decision / reason_code        -> `gate_d.decision` (the governed
#                                         `GateDStatus` value) /
#                                         `gate_d.reason_codes` (comma-
#                                         joined, the identical
#                                         "`reason_codes`, plural, one
#                                         joined attribute" naming
#                                         `gate_c.py`'s own
#                                         `"gate_c.reason_codes"` attribute
#                                         already uses for the same
#                                         shape of value).
#
# Task 13's own "Do not log" list -- raw candidate response / raw PII /
# access token / secret match value / full evidence / hidden prompt -- is
# structurally unreachable from the attributes above: every one of them is
# a count, a governed enum value, a policy version string, a measured
# latency number, or a sanitized reason-code string. Nothing here ever
# reads `candidate.candidate_answer`, a `ProtectedField.value`, a matched
# secret substring (`SecretDetectionReport.matched_pattern_names` already
# carries only pattern *names*, never the matched text -- Task 7's own
# guarantee, reused here rather than re-earned), or `gate_c_validated_
# evidence`'s own content (only counts derived from it).


class GateDStatus(str, Enum):
    """The three required Gate-D outcomes (Task 10). Deliberately not
    extensible at the type level -- a fourth status would need an
    assignment-level change, never an ad hoc string. See this section's
    own "Suggested meaning" mapping (Task 10's own words):
    `ALLOW` -- final response may leave the system.
    `SAFE_FAILURE` -- caller receives controlled typed failure.
    `REJECT` -- invalid internal candidate / programming-policy error
    that should not be exposed as normal answer."""

    ALLOW = "allow"
    SAFE_FAILURE = "safe_failure"
    REJECT = "reject"


class FinalDisclosureReport(BaseModel):
    """Task 6's field-based `DisclosureCheckReport` and Task 7's
    pattern-based `SecretDetectionReport`, combined into the single
    `disclosure_checks` field Task 10's own field list names -- "equivalent
    documented enums/shapes are acceptable" (Task 10): this is that
    documented equivalent, not a loss of either sub-check's own detail
    (`field_checks`/`secret_checks` below carry each in full)."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    reason_codes: tuple[str, ...] = Field(default_factory=tuple)
    field_checks: DisclosureCheckReport
    secret_checks: SecretDetectionReport


class GateDDecision(BaseModel):
    """Gate-D's typed decision (Task 10's field list: `decision` /
    `reason_codes` / `validated_citation_ids` / `quality_checks` /
    `disclosure_checks` / `latency_checks` / `policy_version` /
    `safe_failure_code`, plus `citation_checks` -- an extension beyond
    the minimum list, the same way `GateADecision`/`GateBDecision` each
    add their own extension fields beyond *their* minimum lists: full
    per-citation decision provenance -- which specific citation was
    valid/forged/mismatched, not only the flat `validated_citation_ids`
    summary -- is exactly what Task 14's `final_citation_report.md`
    needs, and re-deriving it by calling `reconcile_final_citations()` a
    second time would duplicate work this decision already did once).

    `validated_citation_ids` mirrors `GateCDecision.validated_evidence_ids`'s
    own least-privilege convention: populated for `ALLOW` only. `citation_
    checks`/`quality_checks`/`disclosure_checks`/`latency_checks` are
    always populated regardless of `decision` -- Task 13's observability
    and Task 14's artifacts both want to see every check's own outcome
    even on an otherwise-passing or otherwise-failing candidate, the
    identical "populated regardless of the final decision" convention
    every sub-report in this module already follows individually."""

    model_config = ConfigDict(extra="forbid")

    decision: GateDStatus
    reason_codes: tuple[str, ...] = Field(default_factory=tuple)
    validated_citation_ids: tuple[str, ...] = Field(default_factory=tuple)
    citation_checks: CitationReconciliationReport
    quality_checks: QualityCheckReport
    disclosure_checks: FinalDisclosureReport
    latency_checks: LatencyCheckReport
    policy_version: str = Field(min_length=1)
    safe_failure_code: str | None = Field(default=None, description="Populated only when decision is SAFE_FAILURE.")


@dataclass
class GateD:
    """Gate-D: built once against a loaded `GateDPolicyRegistry` (Task
    2), reused for every request. `evaluate()` is the only public entry
    point -- see this section's own module-level comment for the
    combination algorithm it runs."""

    policy_registry: GateDPolicyRegistry

    def evaluate(
        self,
        *,
        candidate: FinalResponseCandidate,
        gate_c_validated_evidence: Sequence[GateCEvidenceRecord],
        gate_b_decision: GateBDecision,
        disclosure_profile: DisclosureProfile | None,
        protected_fields: Sequence[ProtectedField],
    ) -> GateDDecision:
        """Evaluate one already-typed final candidate. Never raises for an
        ordinary input -- every outcome, `allow`/`safe_failure`/`reject`
        alike, is a normal, typed `GateDDecision` result. Never mutates
        any of its inputs, calls the Model Gateway, or consults an LLM
        for any part of the decision (working rule: "Gate-D does not ask
        an LLM whether the response is safe to release"). Task 13 --
        runs entirely inside one `"gate_d"` span (this method's own
        module-level "Task 13" docstring subsection has the full
        attribute-by-attribute mapping); `"final_citation_validation"`/
        `"final_disclosure_validation"`/`"latency_budget_validation"` are
        its own nested child spans, opened only around the calls they
        each name."""
        with _tracer.start_as_current_span("gate_d") as span:
            started_at = time.monotonic()
            policy = self.policy_registry

            quality_report = check_final_quality(
                candidate,
                policy=policy.quality_policy,
                allowed_response_statuses=policy.allowed_response_statuses,
            )

            with _tracer.start_as_current_span("final_citation_validation"):
                citation_report = reconcile_final_citations(
                    candidate,
                    gate_c_validated_evidence=gate_c_validated_evidence,
                    policy=policy.citation_policy,
                )

            with _tracer.start_as_current_span("final_disclosure_validation"):
                field_disclosure_report = check_final_disclosure(
                    candidate,
                    gate_b_decision=gate_b_decision,
                    disclosure_profile=disclosure_profile,
                    protected_fields=protected_fields,
                    policy=policy.disclosure_policy,
                    known_disclosure_profile_ids=policy.known_disclosure_profile_ids,
                )
                secret_report = detect_protected_value_leak(candidate, policy=policy.disclosure_policy)
                disclosure_report = FinalDisclosureReport(
                    passed=field_disclosure_report.passed and secret_report.passed,
                    reason_codes=tuple(
                        dict.fromkeys((*field_disclosure_report.reason_codes, *secret_report.reason_codes))
                    ),
                    field_checks=field_disclosure_report,
                    secret_checks=secret_report,
                )

            with _tracer.start_as_current_span("latency_budget_validation"):
                latency_report = check_latency_budget(candidate, policy=policy.latency_budgets)

            if quality_report.severity is QualityFailureSeverity.REJECT:
                decision = GateDStatus.REJECT
            elif not (
                quality_report.passed and citation_report.passed and disclosure_report.passed and latency_report.passed
            ):
                decision = GateDStatus.SAFE_FAILURE
            else:
                decision = GateDStatus.ALLOW

            reason_codes = tuple(
                dict.fromkeys(
                    (
                        *(code.value for code in quality_report.reason_codes),
                        *(code.value for code in citation_report.reason_codes),
                        *disclosure_report.reason_codes,
                        *(code.value for code in latency_report.reason_codes),
                    )
                )
            )

            def _result(passed: bool, severity: QualityFailureSeverity | None = None) -> str:
                if passed:
                    return "passed"
                return severity.value if severity is not None else "failed"

            # Task 13 -- sanitized Gate-D operational metadata, on this
            # span alone (never `request_id`/`correlation_id`/`ontology_
            # version`/`gate_b_policy_version`/`gate_c_policy_version` --
            # see this module's own "Task 13" docstring subsection for
            # why). Every value here is a count, a governed enum value, a
            # policy version string, a measured latency number, or a
            # sanitized reason-code string -- never the candidate answer,
            # a raw protected value, a secret match, or raw evidence.
            span.set_attribute("gate_d.policy_version", policy.policy_version)
            span.set_attribute("gate_d.candidate_status", candidate.candidate_status.value)
            span.set_attribute("gate_d.citation_count", len(candidate.candidate_citations))
            span.set_attribute("gate_d.validated_citation_count", len(citation_report.valid_citation_evidence_ids))
            span.set_attribute("gate_d.quality_result", _result(quality_report.passed, quality_report.severity))
            span.set_attribute("gate_d.disclosure_result", _result(disclosure_report.passed))
            span.set_attribute("gate_d.latency_result", _result(latency_report.passed))
            span.set_attribute("gate_d.total_latency_ms", candidate.elapsed_ms)
            span.set_attribute("gate_d.model_latency_ms", candidate.model_latency_ms)
            span.set_attribute("gate_d.decision", decision.value)
            span.set_attribute("gate_d.reason_codes", ",".join(reason_codes))
            span.set_attribute("gate_d.latency_ms", (time.monotonic() - started_at) * 1000)

            return GateDDecision(
                decision=decision,
                reason_codes=reason_codes,
                validated_citation_ids=citation_report.valid_citation_evidence_ids
                if decision is GateDStatus.ALLOW
                else (),
                citation_checks=citation_report,
                quality_checks=quality_report,
                disclosure_checks=disclosure_report,
                latency_checks=latency_report,
                policy_version=policy.policy_version,
                safe_failure_code=policy.safe_failure.code if decision is GateDStatus.SAFE_FAILURE else None,
            )
