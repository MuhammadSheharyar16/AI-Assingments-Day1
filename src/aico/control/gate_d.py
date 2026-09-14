"""
Day 12 Task 3 -- final citation reconciliation.
Day 12 Task 4 -- evidence/citation provenance preservation (folded into
Task 3's own check, not a separate function -- see "Provenance" below for
why the two are one algorithm, the same "filtering and the decision that
depends on it are one thing, not two" reasoning `gate_c.py`'s own
docstring gives for folding Day 11 Task 10 into Task 9).
Day 12 Task 6 -- final disclosure validation, the field/profile-driven
half only (see this module's own "Day 12 Task 6" section, appended near
the end of this file, for why the secret-pattern/hidden-prompt-marker
half is deliberately left to Task 7's own dedicated section instead of
folded in here).

Day 12's required structure names no separate module for citation
reconciliation or final disclosure validation (unlike Gate-C's own
evidence validators, each given a dedicated file under
`src/aico/evidence/` because Day 11's tree explicitly lists them) --
`gate_d.py` itself is where both live, built up one labeled section per
task the same way `policy_models.py` grew a "Day 12 Task 2" section
rather than a new file. Later Day 12 tasks (the secret/protected-value
detector, latency budget, safe failure, the full decision contract) add
their own labeled sections to this same file as they land; only Task
3/4/6 are implemented so far.

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

from collections.abc import Sequence
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from aico.contracts.models import AnswerStatus
from aico.control.disclosure import ProtectedField, apply_disclosure
from aico.control.final_response import FinalCitation, FinalResponseCandidate
from aico.control.models import GateBDecision
from aico.control.policy_models import CitationPolicy, DisclosureAction, DisclosureProfile, GateDDisclosurePolicy


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


class DisclosureReasonCode(str, Enum):
    """The closed set of reasons `check_final_disclosure()` ever cites.
    Gate-D's full decision contract (Task 10) is expected to fold these
    into its own typed `reason_codes`, never invent a new, undocumented
    disclosure-failure string."""

    DENIED_FIELD_VALUE_LEAKED = "denied_field_value_leaked"
    REDACTABLE_FIELD_VALUE_LEAKED = "redactable_field_value_leaked"


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
    must remain sanitized")."""
    if not policy.enforce_gate_b_profile:
        return DisclosureCheckReport(passed=True)

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
    reason_codes = tuple(dict.fromkeys(check.reason for check in field_checks if check.reason is not None))

    return DisclosureCheckReport(
        passed=not leaked_field_names,
        reason_codes=reason_codes,
        field_checks=tuple(field_checks),
        leaked_field_names=leaked_field_names,
    )
