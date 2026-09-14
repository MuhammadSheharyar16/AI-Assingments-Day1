"""
Day 12 Task 3 -- final citation reconciliation.
Day 12 Task 4 -- evidence/citation provenance preservation.

(`reconcile_final_citations()`, `src/aico/control/gate_d.py`.)

Structure: the first section replays every `final_citation_cases.json`
case end to end, reproducing each one's own `expected` outcome against the
real, committed `citation_policy` (`policy/gate_d_policy.v1.json`); the
second section proves the specific reason codes/behaviors Task 3/4 name
directly (missing required citation, forged vs. rejected-evidence
citation, mixed valid+invalid, provenance mismatch, the
`insufficient_evidence` "clean" vs. "fabricated citation" split, and the
`citations_must_be_gate_c_approved`/`reject_mixed_valid_invalid` policy
switches -- including the one case no shipped fixture exercises,
`citations_must_be_gate_c_approved=False`); the third proves the "never
silently drop an invalid citation" invariant directly against the report
shape.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aico.control.final_response import FinalResponseCandidate, parse_final_response_candidate
from aico.control.gate_d import (
    CitationReasonCode,
    GateCEvidenceRecord,
    reconcile_final_citations,
)
from aico.control.policy_models import CitationPolicy
from aico.control.policy_registry import GateDPolicyRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent
DAY12_FIXTURES = REPO_ROOT / "data" / "day12_pack" / "fixtures"
FINAL_CITATION_CASES = json.loads((DAY12_FIXTURES / "final_citation_cases.json").read_text(encoding="utf-8"))["cases"]

_STARTED_AT = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)

REAL_CITATION_POLICY = GateDPolicyRegistry.load().citation_policy


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


def _evidence_records(entries: list[dict]) -> list[GateCEvidenceRecord]:
    return [GateCEvidenceRecord.model_validate(entry) for entry in entries]


# ══════════════════════════════════════════════════════════════════════
# Section 1 -- fixture replay against the real committed citation policy.
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("case", FINAL_CITATION_CASES, ids=lambda case: case["id"])
def test_final_citation_cases_reproduce_expected_outcome(case: dict) -> None:
    candidate = _candidate_from(
        candidate_status=case["candidate_status"],
        candidate_citations=case["candidate_citations"],
        gate_c_validated_evidence_ids=[item["evidence_id"] for item in case["gate_c_validated_evidence"]],
    )
    gate_c_validated_evidence = _evidence_records(case["gate_c_validated_evidence"])

    report = reconcile_final_citations(
        candidate, gate_c_validated_evidence=gate_c_validated_evidence, policy=REAL_CITATION_POLICY
    )

    expected_passed = case["expected"] == "allow"
    assert report.passed is expected_passed, (case["id"], report.reason_codes)


def test_valid_single_citation_case_names_no_failure_reason() -> None:
    case = next(c for c in FINAL_CITATION_CASES if c["id"] == "CIT12-001")
    candidate = _candidate_from(
        candidate_status=case["candidate_status"],
        candidate_citations=case["candidate_citations"],
        gate_c_validated_evidence_ids=[item["evidence_id"] for item in case["gate_c_validated_evidence"]],
    )
    report = reconcile_final_citations(
        candidate, gate_c_validated_evidence=_evidence_records(case["gate_c_validated_evidence"]), policy=REAL_CITATION_POLICY
    )
    assert report.passed is True
    assert report.reason_codes == ()
    assert report.valid_citation_evidence_ids == ("E-101",)
    assert report.invalid_citation_evidence_ids == ()


def test_forged_citation_case_reason_is_not_gate_c_validated() -> None:
    case = next(c for c in FINAL_CITATION_CASES if c["id"] == "CIT12-002")
    candidate = _candidate_from(
        candidate_status=case["candidate_status"],
        candidate_citations=case["candidate_citations"],
        gate_c_validated_evidence_ids=[item["evidence_id"] for item in case["gate_c_validated_evidence"]],
    )
    report = reconcile_final_citations(
        candidate, gate_c_validated_evidence=_evidence_records(case["gate_c_validated_evidence"]), policy=REAL_CITATION_POLICY
    )
    assert report.passed is False
    assert CitationReasonCode.CITATION_NOT_GATE_C_VALIDATED in report.reason_codes
    assert report.invalid_citation_evidence_ids == ("E-999",)


def test_gate_c_rejected_item_reintroduced_case_same_reason_as_forged() -> None:
    """CIT12-003's citation names an evidence_id Gate-C actually saw and
    rejected (`gate_c_rejected_evidence_ids: ["E-202"]` in the fixture) --
    this module carries no separate rejected-ids parameter (Task 1's
    envelope deliberately doesn't either), so it is, by design,
    indistinguishable from a wholly forged id: both resolve to
    `CITATION_NOT_GATE_C_VALIDATED`. See module docstring."""
    case = next(c for c in FINAL_CITATION_CASES if c["id"] == "CIT12-003")
    candidate = _candidate_from(
        candidate_status=case["candidate_status"],
        candidate_citations=case["candidate_citations"],
        gate_c_validated_evidence_ids=[item["evidence_id"] for item in case["gate_c_validated_evidence"]],
    )
    report = reconcile_final_citations(
        candidate, gate_c_validated_evidence=_evidence_records(case["gate_c_validated_evidence"]), policy=REAL_CITATION_POLICY
    )
    assert report.passed is False
    assert report.reason_codes == (CitationReasonCode.CITATION_NOT_GATE_C_VALIDATED,)


def test_mixed_valid_invalid_case_names_both_reasons() -> None:
    case = next(c for c in FINAL_CITATION_CASES if c["id"] == "CIT12-004")
    candidate = _candidate_from(
        candidate_status=case["candidate_status"],
        candidate_citations=case["candidate_citations"],
        gate_c_validated_evidence_ids=[item["evidence_id"] for item in case["gate_c_validated_evidence"]],
    )
    report = reconcile_final_citations(
        candidate, gate_c_validated_evidence=_evidence_records(case["gate_c_validated_evidence"]), policy=REAL_CITATION_POLICY
    )
    assert report.passed is False
    assert CitationReasonCode.MIXED_VALID_INVALID_CITATIONS in report.reason_codes
    assert CitationReasonCode.CITATION_NOT_GATE_C_VALIDATED in report.reason_codes
    assert report.valid_citation_evidence_ids == ("E-101",)
    assert report.invalid_citation_evidence_ids == ("E-999",)


def test_source_version_mismatch_case_reason_is_provenance_mismatch() -> None:
    case = next(c for c in FINAL_CITATION_CASES if c["id"] == "CIT12-005")
    candidate = _candidate_from(
        candidate_status=case["candidate_status"],
        candidate_citations=case["candidate_citations"],
        gate_c_validated_evidence_ids=[item["evidence_id"] for item in case["gate_c_validated_evidence"]],
    )
    report = reconcile_final_citations(
        candidate, gate_c_validated_evidence=_evidence_records(case["gate_c_validated_evidence"]), policy=REAL_CITATION_POLICY
    )
    assert report.passed is False
    assert report.reason_codes == (CitationReasonCode.CITATION_PROVENANCE_MISMATCH,)


def test_fixture_citations_have_no_citation_id_and_report_carries_none() -> None:
    """`final_citation_cases.json`'s own citations never carry a
    `citation_id` -- `CitationCheckResult.citation_id` is `None` for
    every one of them, never a synthesized placeholder."""
    case = next(c for c in FINAL_CITATION_CASES if c["id"] == "CIT12-001")
    candidate = _candidate_from(
        candidate_status=case["candidate_status"],
        candidate_citations=case["candidate_citations"],
        gate_c_validated_evidence_ids=[item["evidence_id"] for item in case["gate_c_validated_evidence"]],
    )
    report = reconcile_final_citations(
        candidate, gate_c_validated_evidence=_evidence_records(case["gate_c_validated_evidence"]), policy=REAL_CITATION_POLICY
    )
    assert report.citation_checks[0].citation_id is None


def test_supplied_citation_id_is_retained_in_the_report() -> None:
    """Task 4: "A final citation should retain enough metadata to resolve
    back to the evidence supplied to generation" -- proven for the one
    field a fixture never exercises: when a real candidate does supply a
    `citation_id`, `CitationCheckResult` carries it through unchanged, for
    a passing citation and a failing one alike."""
    evidence = [GateCEvidenceRecord(evidence_id="E-101", chunk_id="CH-101", source_id="SRC-POLICY-A", source_version="3")]
    candidate = _candidate_from(
        candidate_citations=[
            {
                "citation_id": "CIT-PUBLIC-1",
                "evidence_id": "E-101",
                "chunk_id": "CH-101",
                "source_id": "SRC-POLICY-A",
                "source_version": "3",
            }
        ],
        gate_c_validated_evidence_ids=["E-101"],
    )
    report = reconcile_final_citations(candidate, gate_c_validated_evidence=evidence, policy=REAL_CITATION_POLICY)
    assert report.passed is True
    assert report.citation_checks[0].citation_id == "CIT-PUBLIC-1"

    forged_candidate = _candidate_from(
        candidate_citations=[
            {"citation_id": "CIT-PUBLIC-2", "evidence_id": "E-999", "chunk_id": "CH-999", "source_id": "SRC-FAKE", "source_version": "1"}
        ],
        gate_c_validated_evidence_ids=[],
    )
    forged_report = reconcile_final_citations(forged_candidate, gate_c_validated_evidence=[], policy=REAL_CITATION_POLICY)
    assert forged_report.passed is False
    assert forged_report.citation_checks[0].citation_id == "CIT-PUBLIC-2"


def test_citation_id_plays_no_role_in_the_reconciliation_decision() -> None:
    """`citation_id` is the candidate's own public-facing id -- Gate-C's
    `GateCEvidenceRecord` carries none to compare it against, so two
    citations differing only in `citation_id` (identical evidence_id/
    chunk_id/source_id/source_version) reach the identical verdict."""
    evidence = [GateCEvidenceRecord(evidence_id="E-101", chunk_id="CH-101", source_id="SRC-POLICY-A", source_version="3")]
    base_citation = {"evidence_id": "E-101", "chunk_id": "CH-101", "source_id": "SRC-POLICY-A", "source_version": "3"}

    without_id = _candidate_from(candidate_citations=[base_citation], gate_c_validated_evidence_ids=["E-101"])
    with_id = _candidate_from(
        candidate_citations=[{**base_citation, "citation_id": "CIT-ANYTHING"}],
        gate_c_validated_evidence_ids=["E-101"],
    )

    report_without = reconcile_final_citations(without_id, gate_c_validated_evidence=evidence, policy=REAL_CITATION_POLICY)
    report_with = reconcile_final_citations(with_id, gate_c_validated_evidence=evidence, policy=REAL_CITATION_POLICY)

    assert report_without.passed == report_with.passed is True
    assert report_without.reason_codes == report_with.reason_codes == ()


# ══════════════════════════════════════════════════════════════════════
# Section 2 -- specific Task 3/4 behaviors.
# ══════════════════════════════════════════════════════════════════════


def test_answered_with_zero_citations_fails_when_required() -> None:
    candidate = _candidate_from(candidate_status="answered", candidate_citations=[])
    report = reconcile_final_citations(candidate, gate_c_validated_evidence=[], policy=REAL_CITATION_POLICY)
    assert report.passed is False
    assert report.reason_codes == (CitationReasonCode.MISSING_REQUIRED_CITATION,)


def test_answered_with_zero_citations_passes_when_not_required() -> None:
    lenient_policy = REAL_CITATION_POLICY.model_copy(update={"answered_requires_citation": False})
    candidate = _candidate_from(candidate_status="answered", candidate_citations=[])
    report = reconcile_final_citations(candidate, gate_c_validated_evidence=[], policy=lenient_policy)
    assert report.passed is True


def test_insufficient_evidence_clean_passes_with_zero_citations() -> None:
    """`final_quality_cases.json` QUAL12-006's own shape: no citation
    requirement applies to `insufficient_evidence` the way it does to
    `answered` -- zero citations is simply the clean case, not a
    violation."""
    candidate = _candidate_from(
        candidate_status="insufficient_evidence",
        candidate_answer="INSUFFICIENT_EVIDENCE: available evidence does not support the requested fact.",
        candidate_citations=[],
    )
    report = reconcile_final_citations(candidate, gate_c_validated_evidence=[], policy=REAL_CITATION_POLICY)
    assert report.passed is True
    assert report.reason_codes == ()


def test_insufficient_evidence_with_fabricated_citation_still_fails() -> None:
    """Task 3's own "insufficient-evidence status must not contain
    fabricated factual citations" -- there is no status-based exemption:
    a fabricated citation on an `insufficient_evidence` candidate fails
    the identical Gate-C-membership check an `answered` candidate's
    would."""
    candidate = _candidate_from(
        candidate_status="insufficient_evidence",
        candidate_answer="INSUFFICIENT_EVIDENCE: available evidence does not support the requested fact.",
        candidate_citations=[
            {"evidence_id": "E-FAKE", "chunk_id": "CH-FAKE", "source_id": "SRC-FAKE", "source_version": "1"}
        ],
    )
    report = reconcile_final_citations(candidate, gate_c_validated_evidence=[], policy=REAL_CITATION_POLICY)
    assert report.passed is False
    assert report.reason_codes == (CitationReasonCode.CITATION_NOT_GATE_C_VALIDATED,)


def test_insufficient_evidence_with_genuinely_valid_citation_passes() -> None:
    """The converse of the above: a real, Gate-C-validated citation on an
    `insufficient_evidence` candidate is not itself a violation -- only a
    *fabricated* one is."""
    evidence = [GateCEvidenceRecord(evidence_id="E-101", chunk_id="CH-101", source_id="SRC-POLICY-A", source_version="3")]
    candidate = _candidate_from(
        candidate_status="insufficient_evidence",
        candidate_answer="INSUFFICIENT_EVIDENCE: available evidence does not support the requested fact.",
        candidate_citations=[{"evidence_id": "E-101", "chunk_id": "CH-101", "source_id": "SRC-POLICY-A", "source_version": "3"}],
        gate_c_validated_evidence_ids=["E-101"],
    )
    report = reconcile_final_citations(candidate, gate_c_validated_evidence=evidence, policy=REAL_CITATION_POLICY)
    assert report.passed is True


@pytest.mark.parametrize("mismatched_field", ["chunk_id", "source_id", "source_version"])
def test_provenance_mismatch_on_any_single_field_fails(mismatched_field: str) -> None:
    """Task 4's own field list (`citation_id` / `evidence_id`/`chunk_id` /
    `source_id` / `source_version`) -- proven one field at a time: a
    disagreement on any single provenance field, not only
    `source_version`, is a `CITATION_PROVENANCE_MISMATCH`."""
    valid_citation = {"evidence_id": "E-101", "chunk_id": "CH-101", "source_id": "SRC-POLICY-A", "source_version": "3"}
    mismatched_citation = dict(valid_citation)
    mismatched_citation[mismatched_field] = "SOMETHING-ELSE"

    evidence = [GateCEvidenceRecord.model_validate(valid_citation)]
    candidate = _candidate_from(
        candidate_citations=[mismatched_citation],
        gate_c_validated_evidence_ids=["E-101"],
    )
    report = reconcile_final_citations(candidate, gate_c_validated_evidence=evidence, policy=REAL_CITATION_POLICY)
    assert report.passed is False
    assert report.reason_codes == (CitationReasonCode.CITATION_PROVENANCE_MISMATCH,)


def test_citations_must_be_gate_c_approved_false_skips_reconciliation() -> None:
    """No shipped fixture exercises this flag as `False` -- proven
    directly: with it off, even a wholly forged citation is treated as
    valid (the policy has decided this lane does not require Gate-C
    reconciliation at all)."""
    lenient_policy = REAL_CITATION_POLICY.model_copy(update={"citations_must_be_gate_c_approved": False})
    candidate = _candidate_from(
        candidate_citations=[{"evidence_id": "E-999", "chunk_id": "CH-999", "source_id": "SRC-FAKE", "source_version": "1"}],
        gate_c_validated_evidence_ids=[],
    )
    report = reconcile_final_citations(candidate, gate_c_validated_evidence=[], policy=lenient_policy)
    assert report.passed is True
    assert report.valid_citation_evidence_ids == ("E-999",)


def test_reject_mixed_valid_invalid_false_still_fails_but_omits_overarching_reason() -> None:
    """The working rule against ever silently dropping an invalid citation
    is absolute (module docstring's "Never silently drop" section) --
    `reject_mixed_valid_invalid=False` only suppresses the extra
    `MIXED_VALID_INVALID_CITATIONS` reason code, it never flips `passed`
    to `True`."""
    narrow_policy = REAL_CITATION_POLICY.model_copy(update={"reject_mixed_valid_invalid": False})
    evidence = [GateCEvidenceRecord(evidence_id="E-101", chunk_id="CH-101", source_id="SRC-POLICY-A", source_version="3")]
    candidate = _candidate_from(
        candidate_citations=[
            {"evidence_id": "E-101", "chunk_id": "CH-101", "source_id": "SRC-POLICY-A", "source_version": "3"},
            {"evidence_id": "E-999", "chunk_id": "CH-999", "source_id": "SRC-FAKE", "source_version": "1"},
        ],
        gate_c_validated_evidence_ids=["E-101"],
    )
    report = reconcile_final_citations(candidate, gate_c_validated_evidence=evidence, policy=narrow_policy)
    assert report.passed is False
    assert CitationReasonCode.MIXED_VALID_INVALID_CITATIONS not in report.reason_codes
    assert CitationReasonCode.CITATION_NOT_GATE_C_VALIDATED in report.reason_codes


# ══════════════════════════════════════════════════════════════════════
# Section 3 -- "never silently drop an invalid citation" invariant.
# ══════════════════════════════════════════════════════════════════════


def test_passed_report_never_carries_any_invalid_citation() -> None:
    """A structural proof of the working rule, over every fixture case at
    once: whenever `passed` is `True`, `invalid_citation_evidence_ids` is
    always empty -- there is no report shape in which an invalid citation
    coexists with an overall `passed=True` outcome."""
    for case in FINAL_CITATION_CASES:
        candidate = _candidate_from(
            candidate_status=case["candidate_status"],
            candidate_citations=case["candidate_citations"],
            gate_c_validated_evidence_ids=[item["evidence_id"] for item in case["gate_c_validated_evidence"]],
        )
        report = reconcile_final_citations(
            candidate,
            gate_c_validated_evidence=_evidence_records(case["gate_c_validated_evidence"]),
            policy=REAL_CITATION_POLICY,
        )
        if report.passed:
            assert report.invalid_citation_evidence_ids == (), case["id"]


def test_report_never_raises_for_malformed_relationship_between_inputs() -> None:
    """`reconcile_final_citations()` is a pure function over typed inputs
    -- it never raises for an ordinary (even self-contradictory) input
    combination; every outcome is a normal, typed report."""
    candidate = _candidate_from(candidate_citations=[], gate_c_validated_evidence_ids=["E-101"])
    report = reconcile_final_citations(
        candidate,
        gate_c_validated_evidence=[GateCEvidenceRecord(evidence_id="E-999", chunk_id="C", source_id="S", source_version="1")],
        policy=REAL_CITATION_POLICY,
    )
    assert isinstance(report.passed, bool)


def test_citation_policy_type_reused_directly() -> None:
    """`reconcile_final_citations()`'s `policy` parameter is the real,
    governed `CitationPolicy` type (Task 2) -- not a competing ad hoc
    shape."""
    assert isinstance(REAL_CITATION_POLICY, CitationPolicy)
