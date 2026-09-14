"""
Day 12 Task 5 -- final deterministic quality checks
(`check_final_quality()`, `src/aico/control/quality.py`).

Structure: the first section replays every `final_quality_cases.json`
case end to end against the real committed policy -- combined with
`gate_d.reconcile_final_citations()` (Task 3), since one fixture case
(QUAL12-005, "answered without citation") is fundamentally a citation
concern quality.py deliberately does not duplicate (see `quality.py`'s
own docstring); the second section proves each Task 5 behavior directly,
including the `reject` vs. `safe_failure` severity split and the two
checks no fixture case exercises in isolation (status/answer
inconsistency the other direction, and a policy-narrowed
`allowed_response_statuses`).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aico.contracts.models import AnswerStatus
from aico.control.final_response import FinalResponseCandidate, ValidationStatus, parse_final_response_candidate
from aico.control.gate_d import GateCEvidenceRecord, reconcile_final_citations
from aico.control.policy_models import QualityPolicy
from aico.control.policy_registry import GateDPolicyRegistry
from aico.control.quality import (
    QualityCheckReport,
    QualityFailureSeverity,
    QualityReasonCode,
    check_final_quality,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DAY12_FIXTURES = REPO_ROOT / "data" / "day12_pack" / "fixtures"
FINAL_QUALITY_CASES = json.loads((DAY12_FIXTURES / "final_quality_cases.json").read_text(encoding="utf-8"))["cases"]

_STARTED_AT = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)

_REAL_POLICY_REGISTRY = GateDPolicyRegistry.load()
REAL_QUALITY_POLICY = _REAL_POLICY_REGISTRY.quality_policy
REAL_CITATION_POLICY = _REAL_POLICY_REGISTRY.citation_policy
REAL_ALLOWED_STATUSES = _REAL_POLICY_REGISTRY.allowed_response_statuses

_MATCHING_CITATION = {"evidence_id": "E-101", "chunk_id": "CH-101", "source_id": "SRC-POLICY-A", "source_version": "3"}
_MATCHING_EVIDENCE = [GateCEvidenceRecord.model_validate(_MATCHING_CITATION)]


def _base_envelope(**overrides: object) -> dict:
    payload: dict = {
        "request_id": "REQ-1",
        "correlation_id": "CORR-1",
        "candidate_status": "answered",
        "candidate_answer": "Synthetic Supplier Alpha uses net 30 payment terms.",
        "candidate_citations": [],
        "gate_c_validated_evidence_ids": [],
        "started_at": _STARTED_AT.isoformat(),
        "elapsed_ms": 1200,
        "model_latency_ms": 800,
        "contract_validation_status": "passed",
        "semantic_validation_status": "passed",
    }
    payload.update(overrides)
    return payload


def _candidate_from(**overrides: object) -> FinalResponseCandidate:
    return parse_final_response_candidate(_base_envelope(**overrides))


def _candidate_for_case(case: dict) -> FinalResponseCandidate:
    answer = case.get("candidate_answer")
    if answer is None:
        answer = "x" * case["candidate_answer_length"]
    citations = [_MATCHING_CITATION] if case["citation_count"] > 0 else []
    return _candidate_from(
        candidate_status=case["candidate_status"],
        candidate_answer=answer,
        candidate_citations=citations,
        gate_c_validated_evidence_ids=[c["evidence_id"] for c in citations],
        contract_validation_status="passed" if case["contract_valid"] else "failed",
        semantic_validation_status="passed" if case["semantic_valid"] else "failed",
    )


def _evidence_for_case(case: dict) -> list[GateCEvidenceRecord]:
    return _MATCHING_EVIDENCE if case["citation_count"] > 0 else []


# ══════════════════════════════════════════════════════════════════════
# Section 1 -- fixture replay (quality + Task 3 citation reconciliation
# combined -- see module docstring for why).
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("case", FINAL_QUALITY_CASES, ids=lambda case: case["id"])
def test_final_quality_cases_reproduce_expected_outcome(case: dict) -> None:
    candidate = _candidate_for_case(case)

    quality_report = check_final_quality(
        candidate, policy=REAL_QUALITY_POLICY, allowed_response_statuses=REAL_ALLOWED_STATUSES
    )
    citation_report = reconcile_final_citations(
        candidate, gate_c_validated_evidence=_evidence_for_case(case), policy=REAL_CITATION_POLICY
    )

    if not quality_report.passed:
        overall_passed = False
        overall_is_reject = quality_report.severity is QualityFailureSeverity.REJECT
    elif not citation_report.passed:
        overall_passed = False
        overall_is_reject = False  # Task 3 never produces reject, only safe_failure
    else:
        overall_passed = True
        overall_is_reject = False

    assert overall_passed is (case["expected"] == "allow"), (case["id"], quality_report, citation_report)
    if not overall_passed:
        assert overall_is_reject is (case["expected"] == "reject"), (case["id"], quality_report)


def test_valid_answer_case_passes_cleanly() -> None:
    case = next(c for c in FINAL_QUALITY_CASES if c["id"] == "QUAL12-001")
    report = check_final_quality(
        _candidate_for_case(case), policy=REAL_QUALITY_POLICY, allowed_response_statuses=REAL_ALLOWED_STATUSES
    )
    assert report == QualityCheckReport(passed=True)


def test_empty_answer_case_is_safe_failure_not_reject() -> None:
    case = next(c for c in FINAL_QUALITY_CASES if c["id"] == "QUAL12-002")
    report = check_final_quality(
        _candidate_for_case(case), policy=REAL_QUALITY_POLICY, allowed_response_statuses=REAL_ALLOWED_STATUSES
    )
    assert report.passed is False
    assert report.severity is QualityFailureSeverity.SAFE_FAILURE
    assert report.reason_codes == (QualityReasonCode.EMPTY_ANSWERED_RESULT,)


def test_contract_invalid_case_is_reject() -> None:
    case = next(c for c in FINAL_QUALITY_CASES if c["id"] == "QUAL12-003")
    report = check_final_quality(
        _candidate_for_case(case), policy=REAL_QUALITY_POLICY, allowed_response_statuses=REAL_ALLOWED_STATUSES
    )
    assert report.passed is False
    assert report.severity is QualityFailureSeverity.REJECT
    assert report.reason_codes == (QualityReasonCode.CONTRACT_VALIDATION_FAILED,)


def test_semantic_invalid_case_is_reject() -> None:
    case = next(c for c in FINAL_QUALITY_CASES if c["id"] == "QUAL12-004")
    report = check_final_quality(
        _candidate_for_case(case), policy=REAL_QUALITY_POLICY, allowed_response_statuses=REAL_ALLOWED_STATUSES
    )
    assert report.passed is False
    assert report.severity is QualityFailureSeverity.REJECT
    assert report.reason_codes == (QualityReasonCode.SEMANTIC_VALIDATION_FAILED,)


def test_answered_without_citation_case_passes_quality_alone() -> None:
    """QUAL12-005 is a citation concern, not a quality one -- `quality.py`
    alone reports `passed=True` for it (nothing in `QualityPolicy` governs
    citation presence); the fixture's own `safe_failure` expectation comes
    from `gate_d.reconcile_final_citations()` instead, proven in
    Section 1's combined replay above."""
    case = next(c for c in FINAL_QUALITY_CASES if c["id"] == "QUAL12-005")
    report = check_final_quality(
        _candidate_for_case(case), policy=REAL_QUALITY_POLICY, allowed_response_statuses=REAL_ALLOWED_STATUSES
    )
    assert report.passed is True

    citation_report = reconcile_final_citations(
        _candidate_for_case(case), gate_c_validated_evidence=_evidence_for_case(case), policy=REAL_CITATION_POLICY
    )
    assert citation_report.passed is False


def test_insufficient_evidence_clean_case_passes() -> None:
    case = next(c for c in FINAL_QUALITY_CASES if c["id"] == "QUAL12-006")
    report = check_final_quality(
        _candidate_for_case(case), policy=REAL_QUALITY_POLICY, allowed_response_statuses=REAL_ALLOWED_STATUSES
    )
    assert report == QualityCheckReport(passed=True)


def test_oversized_answer_case_is_safe_failure_not_reject() -> None:
    case = next(c for c in FINAL_QUALITY_CASES if c["id"] == "QUAL12-007")
    report = check_final_quality(
        _candidate_for_case(case), policy=REAL_QUALITY_POLICY, allowed_response_statuses=REAL_ALLOWED_STATUSES
    )
    assert report.passed is False
    assert report.severity is QualityFailureSeverity.SAFE_FAILURE
    assert report.reason_codes == (QualityReasonCode.ANSWER_TOO_LONG,)


# ══════════════════════════════════════════════════════════════════════
# Section 2 -- specific Task 5 behaviors.
# ══════════════════════════════════════════════════════════════════════


def test_answer_exactly_at_max_chars_passes() -> None:
    candidate = _candidate_from(candidate_answer="x" * REAL_QUALITY_POLICY.max_answer_chars)
    report = check_final_quality(candidate, policy=REAL_QUALITY_POLICY, allowed_response_statuses=REAL_ALLOWED_STATUSES)
    assert report.passed is True


def test_answer_one_over_max_chars_fails() -> None:
    candidate = _candidate_from(candidate_answer="x" * (REAL_QUALITY_POLICY.max_answer_chars + 1))
    report = check_final_quality(candidate, policy=REAL_QUALITY_POLICY, allowed_response_statuses=REAL_ALLOWED_STATUSES)
    assert report.passed is False
    assert report.reason_codes == (QualityReasonCode.ANSWER_TOO_LONG,)


def test_answered_answer_starting_with_insufficient_evidence_marker_is_inconsistent() -> None:
    """The direction no shipped fixture exercises: an `answered` candidate
    whose text itself reads as an insufficiency refusal -- Gate-D's own
    re-check of Day 4's rule S5 catches it even though nothing here
    re-runs Day 4's own validator."""
    candidate = _candidate_from(
        candidate_status="answered",
        candidate_answer="INSUFFICIENT_EVIDENCE: this should not be status=answered.",
    )
    report = check_final_quality(candidate, policy=REAL_QUALITY_POLICY, allowed_response_statuses=REAL_ALLOWED_STATUSES)
    assert report.passed is False
    assert report.severity is QualityFailureSeverity.REJECT
    assert QualityReasonCode.STATUS_ANSWER_INCONSISTENT in report.reason_codes


def test_insufficient_evidence_answer_missing_marker_is_inconsistent() -> None:
    candidate = _candidate_from(
        candidate_status="insufficient_evidence",
        candidate_answer="Here is a real answer pretending not to be one.",
    )
    report = check_final_quality(candidate, policy=REAL_QUALITY_POLICY, allowed_response_statuses=REAL_ALLOWED_STATUSES)
    assert report.passed is False
    assert report.severity is QualityFailureSeverity.REJECT
    assert QualityReasonCode.STATUS_ANSWER_INCONSISTENT in report.reason_codes


def test_policy_narrowed_allowed_statuses_rejects_unsupported_status() -> None:
    """No shipped fixture exercises a policy version narrower than the
    full `AnswerStatus` enum -- proven directly: a candidate whose status
    is a perfectly valid `AnswerStatus` member but not one *this policy
    version* governs is `UNSUPPORTED_RESPONSE_STATUS`."""
    candidate = _candidate_from(
        candidate_status="insufficient_evidence",
        candidate_answer="INSUFFICIENT_EVIDENCE: no evidence available.",
    )
    narrowed_statuses = (AnswerStatus.ANSWERED,)  # this policy version no longer permits insufficient_evidence
    report = check_final_quality(candidate, policy=REAL_QUALITY_POLICY, allowed_response_statuses=narrowed_statuses)
    assert report.passed is False
    assert report.severity is QualityFailureSeverity.REJECT
    assert QualityReasonCode.UNSUPPORTED_RESPONSE_STATUS in report.reason_codes


def test_contract_must_pass_false_ignores_a_failed_contract() -> None:
    """The other real behavioral switch this function reads: with
    `contract_must_pass=False`, a `failed` `contract_validation_status`
    is no longer, by itself, a reason to reject."""
    lenient_policy = REAL_QUALITY_POLICY.model_copy(update={"contract_must_pass": False})
    candidate = _candidate_from(contract_validation_status="failed")
    report = check_final_quality(candidate, policy=lenient_policy, allowed_response_statuses=REAL_ALLOWED_STATUSES)
    assert report.passed is True


def test_semantic_validation_must_pass_false_ignores_a_failed_semantic_check() -> None:
    lenient_policy = REAL_QUALITY_POLICY.model_copy(update={"semantic_validation_must_pass": False})
    candidate = _candidate_from(semantic_validation_status="failed")
    report = check_final_quality(candidate, policy=lenient_policy, allowed_response_statuses=REAL_ALLOWED_STATUSES)
    assert report.passed is True


def test_answered_must_be_nonempty_false_allows_an_empty_answer() -> None:
    lenient_policy = REAL_QUALITY_POLICY.model_copy(update={"answered_must_be_nonempty": False})
    candidate = _candidate_from(candidate_answer="")
    report = check_final_quality(candidate, policy=lenient_policy, allowed_response_statuses=REAL_ALLOWED_STATUSES)
    assert report.passed is True


def test_whitespace_only_answer_counts_as_empty() -> None:
    candidate = _candidate_from(candidate_answer="   ")
    report = check_final_quality(candidate, policy=REAL_QUALITY_POLICY, allowed_response_statuses=REAL_ALLOWED_STATUSES)
    assert report.passed is False
    assert QualityReasonCode.EMPTY_ANSWERED_RESULT in report.reason_codes


def test_reject_outranks_safe_failure_when_both_apply() -> None:
    """A candidate that is simultaneously contract-invalid (reject-level)
    and oversized (safe_failure-level) is reported as `reject` -- an
    internal-candidate error is never downgraded merely because it also
    violates an ordinary release rule."""
    candidate = _candidate_from(
        candidate_answer="x" * (REAL_QUALITY_POLICY.max_answer_chars + 1),
        contract_validation_status="failed",
    )
    report = check_final_quality(candidate, policy=REAL_QUALITY_POLICY, allowed_response_statuses=REAL_ALLOWED_STATUSES)
    assert report.passed is False
    assert report.severity is QualityFailureSeverity.REJECT
    assert QualityReasonCode.CONTRACT_VALIDATION_FAILED in report.reason_codes
    assert QualityReasonCode.ANSWER_TOO_LONG in report.reason_codes


def test_severity_is_none_exactly_when_passed() -> None:
    passing = check_final_quality(_candidate_from(), policy=REAL_QUALITY_POLICY, allowed_response_statuses=REAL_ALLOWED_STATUSES)
    assert passing.passed is True
    assert passing.severity is None

    failing = check_final_quality(
        _candidate_from(candidate_answer=""), policy=REAL_QUALITY_POLICY, allowed_response_statuses=REAL_ALLOWED_STATUSES
    )
    assert failing.passed is False
    assert failing.severity is not None


def test_no_opaque_combined_quality_score_field_exists() -> None:
    """Task 5: "Do not invent one combined opaque quality score." """
    assert set(QualityCheckReport.model_fields) == {"passed", "severity", "reason_codes"}


def test_report_never_raises_for_an_ordinary_input() -> None:
    candidate = _candidate_from(candidate_status="insufficient_evidence", candidate_answer="")
    report = check_final_quality(candidate, policy=REAL_QUALITY_POLICY, allowed_response_statuses=REAL_ALLOWED_STATUSES)
    assert isinstance(report, QualityCheckReport)


def test_quality_policy_type_reused_directly() -> None:
    assert isinstance(REAL_QUALITY_POLICY, QualityPolicy)


def test_contract_validation_status_type_reused_directly() -> None:
    """`check_final_quality()` compares against the real, governed
    `ValidationStatus` enum (Task 1) -- not a competing ad hoc shape."""
    candidate = _candidate_from()
    assert candidate.contract_validation_status is ValidationStatus.PASSED
