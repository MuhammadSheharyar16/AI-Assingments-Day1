"""
Day 12 Task 5 -- final deterministic quality checks.

`final_response_rules.md` (`data/day12_pack/`): "The final live gate
checks deterministic properties ... This does not replace the Day 7
offline evaluation suite." Day 7 (`aico.evals.day07`) proves *product*
quality over a whole dataset, offline, permanently blocking CI regression
-- this module protects one *single live response*, deterministically,
right before it would otherwise leave the system. Neither replaces the
other; `check_final_quality()` never touches the Day 7 dataset/thresholds,
and Day 7 never runs against a live `FinalResponseCandidate`.

"Do not invent one combined opaque 'quality score'" (Task 5): every check
below is independent and named -- there is no aggregate numeric score
anywhere in this module, only a typed set of `QualityReasonCode`s, the
same "governed enum reason, never an opaque number" discipline every other
Day 9-12 decision in this codebase already follows.

Every Task 5 "At minimum include" bullet, traced to where it is enforced:

    - typed contract already passed     -> `policy.contract_must_pass` +
                                            `candidate.
                                            contract_validation_status`.
    - semantic validation already
      passed                           -> `policy.
                                            semantic_validation_must_pass`
                                            + `candidate.
                                            semantic_validation_status`.
    - status/answer consistency         -> `_status_answer_inconsistent()`,
                                            below -- see "Status/answer
                                            consistency" section.
    - required citation presence        -> already fully implemented by
                                            `gate_d.reconcile_final_
                                            citations()` (Task 3,
                                            `policy.answered_requires_
                                            citation` there -- a
                                            `CitationPolicy` field, not a
                                            `QualityPolicy` one). Not
                                            duplicated here (Day 10
                                            working rule, reused verbatim
                                            for Day 12: "Do not hardcode
                                            behavior in multiple unrelated
                                            files") -- Task 10's decision
                                            contract combines this
                                            module's report with Task 3's.
    - answer length/output-size
      ceiling                          -> `policy.max_answer_chars`,
                                            `QualityReasonCode.
                                            ANSWER_TOO_LONG`.
    - insufficient-evidence behavior    -> the status/answer consistency
                                            check above (an
                                            `insufficient_evidence`
                                            candidate whose text does not
                                            actually read as one) plus,
                                            again, Task 3's own citation
                                            reconciliation (an
                                            `insufficient_evidence`
                                            candidate carrying a
                                            fabricated citation) -- no
                                            separate check invented here;
                                            see `final_citation_cases.json`
                                            .../`gate_d.py`'s own
                                            "Provenance" section for the
                                            latter half.
    - no empty "answered" result        -> `policy.
                                            answered_must_be_nonempty`,
                                            `QualityReasonCode.
                                            EMPTY_ANSWERED_RESULT`.
    - no unsupported response status    -> `allowed_response_statuses`
                                            (the *policy's* governed
                                            subset, `GateDPolicyDocument.
                                            allowed_response_statuses` --
                                            Task 2 -- passed in
                                            explicitly, never re-derived
                                            from the closed `AnswerStatus`
                                            enum alone: `candidate_status`
                                            already cannot be anything
                                            outside that enum by the time
                                            it reaches this module, Task
                                            1's own envelope boundary
                                            already guarantees that; what
                                            *can* still happen is a
                                            governed `AnswerStatus` member
                                            this particular policy version
                                            has chosen not to permit).

## Status/answer consistency

Day 4's own `contracts/semantic.py` already enforces this exact rule
upstream (rule S5, the `INSUFFICIENT_EVIDENCE` marker-prefix convention)
-- `candidate.semantic_validation_status` records whether that check
already passed. `_status_answer_inconsistent()` below re-applies the
*identical* rule (reusing `INSUFFICIENT_EVIDENCE_PREFIX` directly from
`aico.contracts.semantic`, never a second, competing marker string) as
Gate-D's own defense-in-depth re-check at the release boundary, rather
than trusting the upstream flag alone -- the same posture every other
Gate-D check in this codebase takes toward its own upstream input
(working rule: "Gate-D does not repair ... with another model call";
more fundamentally, a flag reported *by* the candidate is not proof, it
is a claim Gate-D independently verifies wherever it cheaply can).

## Reject vs. safe_failure

Task 10's decision contract distinguishes `safe_failure` ("caller
receives controlled typed failure" -- a normal, policy-driven release
decline) from `reject` ("invalid internal candidate / programming-policy
error that should not be exposed as normal answer"). `final_quality_
cases.json` makes this distinction concrete: QUAL12-003/QUAL12-004 (an
already-failed typed contract/semantic validation) each expect `reject`,
while QUAL12-002/QUAL12-005/QUAL12-007 (empty answer, missing citation,
oversized answer -- ordinary release-policy violations on an otherwise
well-formed candidate) expect `safe_failure`. Only the quality layer
itself knows which specific rule a candidate violated, so
`QualityCheckReport.severity` is decided here, once, rather than
re-derived by Task 10 from a flat reason-code list: a candidate whose
typed contract or semantic validation already failed, or whose status is
not one this policy governs at all, never should have reached Gate-D as a
plausible candidate in the first place -- `reject`; everything else this
module checks is a normal release-policy decision on an otherwise valid
candidate -- `safe_failure`. `reject` always outranks `safe_failure` when
a candidate violates both kinds of rule at once (`_overall_severity()`).
"""
from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from aico.contracts.models import AnswerStatus
from aico.contracts.semantic import INSUFFICIENT_EVIDENCE_PREFIX
from aico.control.final_response import FinalResponseCandidate, ValidationStatus
from aico.control.policy_models import QualityPolicy


class QualityFailureSeverity(str, Enum):
    """Which of Task 10's two non-`allow` outcomes a quality failure maps
    to -- see module docstring's "Reject vs. safe_failure" section."""

    SAFE_FAILURE = "safe_failure"
    REJECT = "reject"


class QualityReasonCode(str, Enum):
    """The closed set of reasons `check_final_quality()` ever cites.
    Gate-D's full decision contract (Task 10) is expected to fold these
    into its own typed `reason_codes`, never invent a new, undocumented
    quality-failure string."""

    CONTRACT_VALIDATION_FAILED = "contract_validation_failed"
    SEMANTIC_VALIDATION_FAILED = "semantic_validation_failed"
    STATUS_ANSWER_INCONSISTENT = "status_answer_inconsistent"
    UNSUPPORTED_RESPONSE_STATUS = "unsupported_response_status"
    EMPTY_ANSWERED_RESULT = "empty_answered_result"
    ANSWER_TOO_LONG = "answer_too_long"


# The one place a `QualityReasonCode` is ever mapped to a
# `QualityFailureSeverity` -- see module docstring's "Reject vs.
# safe_failure" section for why each reason lands where it does.
_SEVERITY_BY_REASON: dict[QualityReasonCode, QualityFailureSeverity] = {
    QualityReasonCode.CONTRACT_VALIDATION_FAILED: QualityFailureSeverity.REJECT,
    QualityReasonCode.SEMANTIC_VALIDATION_FAILED: QualityFailureSeverity.REJECT,
    QualityReasonCode.STATUS_ANSWER_INCONSISTENT: QualityFailureSeverity.REJECT,
    QualityReasonCode.UNSUPPORTED_RESPONSE_STATUS: QualityFailureSeverity.REJECT,
    QualityReasonCode.EMPTY_ANSWERED_RESULT: QualityFailureSeverity.SAFE_FAILURE,
    QualityReasonCode.ANSWER_TOO_LONG: QualityFailureSeverity.SAFE_FAILURE,
}


class QualityCheckReport(BaseModel):
    """`check_final_quality()`'s one return value. `severity` is `None`
    exactly when `passed` is `True` -- there is no severity for a check
    that did not fail. Mirrors `gate_d.CitationReconciliationReport`'s own
    shape one layer over (`passed` + `reason_codes`), plus the
    `severity` this module alone can decide (see module docstring)."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    severity: QualityFailureSeverity | None = Field(default=None, description="None when passed is True.")
    reason_codes: tuple[QualityReasonCode, ...] = Field(default_factory=tuple)


def _status_answer_inconsistent(candidate: FinalResponseCandidate) -> bool:
    """Re-applies `contracts/semantic.py`'s own rule S5 -- see module
    docstring's "Status/answer consistency" section. Deliberately reuses
    `INSUFFICIENT_EVIDENCE_PREFIX` directly rather than a second,
    hand-copied marker string, the same "governed value imported once,
    never re-typed" discipline this codebase applies to every other
    shared constant (e.g. `redaction.py`'s single masking scheme)."""
    starts_with_marker = candidate.candidate_answer.startswith(INSUFFICIENT_EVIDENCE_PREFIX)
    if candidate.candidate_status is AnswerStatus.ANSWERED and starts_with_marker:
        return True
    if candidate.candidate_status is AnswerStatus.INSUFFICIENT_EVIDENCE and not starts_with_marker:
        return True
    return False


def _overall_severity(reason_codes: Sequence[QualityReasonCode]) -> QualityFailureSeverity:
    """`reject` outranks `safe_failure` whenever a candidate violates both
    kinds of rule at once -- an internal-candidate/programming-policy
    error is never downgraded to "merely declined by policy" just because
    it also happens to violate an ordinary release rule."""
    if any(_SEVERITY_BY_REASON[reason] is QualityFailureSeverity.REJECT for reason in reason_codes):
        return QualityFailureSeverity.REJECT
    return QualityFailureSeverity.SAFE_FAILURE


def check_final_quality(
    candidate: FinalResponseCandidate,
    *,
    policy: QualityPolicy,
    allowed_response_statuses: Sequence[AnswerStatus],
) -> QualityCheckReport:
    """Run every Task 5 deterministic quality check against `candidate`.
    Never raises for an ordinary input -- every outcome is a normal, typed
    `QualityCheckReport`, the identical "typed result, not an exception"
    convention every other Day 11/12 validator in this codebase already
    follows. Never mutates `candidate`/`policy`, and never calls the
    Model Gateway or any other external service -- purely a function of
    its typed inputs, the same purity `disclosure.py`/`redaction.py`
    already commit to.

    `allowed_response_statuses` is the governed subset this specific
    policy version permits (`GateDPolicyDocument.allowed_response_statuses`,
    Task 2) -- a caller resolves it once from a loaded `GateDPolicyRegistry`
    and passes it in, so this module never needs its own registry
    reference (kept a pure function of its typed inputs, matching
    `gate_d.reconcile_final_citations()`'s identical `policy: CitationPolicy`
    parameter shape one section over)."""
    reason_codes: list[QualityReasonCode] = []

    if policy.contract_must_pass and candidate.contract_validation_status is not ValidationStatus.PASSED:
        reason_codes.append(QualityReasonCode.CONTRACT_VALIDATION_FAILED)

    if policy.semantic_validation_must_pass and candidate.semantic_validation_status is not ValidationStatus.PASSED:
        reason_codes.append(QualityReasonCode.SEMANTIC_VALIDATION_FAILED)

    if candidate.candidate_status not in allowed_response_statuses:
        reason_codes.append(QualityReasonCode.UNSUPPORTED_RESPONSE_STATUS)

    if _status_answer_inconsistent(candidate):
        reason_codes.append(QualityReasonCode.STATUS_ANSWER_INCONSISTENT)

    if (
        policy.answered_must_be_nonempty
        and candidate.candidate_status is AnswerStatus.ANSWERED
        and not candidate.candidate_answer.strip()
    ):
        reason_codes.append(QualityReasonCode.EMPTY_ANSWERED_RESULT)

    if len(candidate.candidate_answer) > policy.max_answer_chars:
        reason_codes.append(QualityReasonCode.ANSWER_TOO_LONG)

    if not reason_codes:
        return QualityCheckReport(passed=True)

    return QualityCheckReport(
        passed=False,
        severity=_overall_severity(reason_codes),
        reason_codes=tuple(reason_codes),
    )
