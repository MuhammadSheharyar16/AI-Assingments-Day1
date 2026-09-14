"""
Day 12 Task 8 -- latency-budget policy
(`check_latency_budget()`, `src/aico/control/gate_d.py`).

Structure: Section 1 replays every `latency_budget_cases.json` case --
LAT12-001 through LAT12-004 through `check_latency_budget()` itself
against the real committed `latency_budgets` policy; LAT12-005 (invalid
negative timing) through `parse_final_response_candidate()` instead,
proving the envelope rejects it before this function would ever run (see
`gate_d.py`'s own "Day 12 Task 8" section for why that split is correct,
not a gap -- `test_day12_gate_d.py` already proves the identical envelope
behavior for its own Task 1 purposes; this file re-proves it once more
here specifically because it is one of Task 8's own five named required
cases). Section 2 proves the remaining Task 8 behaviors directly: the
`threshold_is_inclusive=False` direction no fixture exercises, and that
the report never fabricates/adjusts the measured telemetry it reports.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from aico.control.errors import FinalResponseEnvelopeError
from aico.control.final_response import FinalResponseCandidate, parse_final_response_candidate
from aico.control.gate_d import LatencyCheckReport, LatencyReasonCode, check_latency_budget
from aico.control.policy_models import LatencyBudgets
from aico.control.policy_registry import GateDPolicyRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent
DAY12_FIXTURES = REPO_ROOT / "data" / "day12_pack" / "fixtures"
LATENCY_BUDGET_CASES = json.loads((DAY12_FIXTURES / "latency_budget_cases.json").read_text(encoding="utf-8"))["cases"]

_STARTED_AT = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)

REAL_LATENCY_BUDGETS = GateDPolicyRegistry.load().latency_budgets


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
    return _candidate_from(elapsed_ms=case["total_latency_ms"], model_latency_ms=case["model_latency_ms"])


# ══════════════════════════════════════════════════════════════════════
# Section 1 -- fixture replay.
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "case", [c for c in LATENCY_BUDGET_CASES if c["expected"] != "reject"], ids=lambda case: case["id"]
)
def test_latency_budget_cases_reproduce_expected_outcome(case: dict) -> None:
    candidate = _candidate_for_case(case)
    report = check_latency_budget(candidate, policy=REAL_LATENCY_BUDGETS)
    expected_passed = case["expected"] == "allow"
    assert report.passed is expected_passed, (case["id"], report.reason_codes)


def test_within_budget_case_names_no_failure_reason() -> None:
    case = next(c for c in LATENCY_BUDGET_CASES if c["id"] == "LAT12-001")
    report = check_latency_budget(_candidate_for_case(case), policy=REAL_LATENCY_BUDGETS)
    assert report == LatencyCheckReport(
        passed=True, total_latency_ms=2100, model_latency_ms=1200, max_total_latency_ms=2500, max_model_latency_ms=1400
    )


def test_exact_threshold_case_passes_inclusively() -> None:
    case = next(c for c in LATENCY_BUDGET_CASES if c["id"] == "LAT12-002")
    report = check_latency_budget(_candidate_for_case(case), policy=REAL_LATENCY_BUDGETS)
    assert report.passed is True
    assert report.reason_codes == ()


def test_model_budget_exceeded_case_reason() -> None:
    case = next(c for c in LATENCY_BUDGET_CASES if c["id"] == "LAT12-003")
    report = check_latency_budget(_candidate_for_case(case), policy=REAL_LATENCY_BUDGETS)
    assert report.passed is False
    assert report.reason_codes == (LatencyReasonCode.MODEL_LATENCY_BUDGET_EXCEEDED,)


def test_total_budget_exceeded_case_reason() -> None:
    case = next(c for c in LATENCY_BUDGET_CASES if c["id"] == "LAT12-004")
    report = check_latency_budget(_candidate_for_case(case), policy=REAL_LATENCY_BUDGETS)
    assert report.passed is False
    assert report.reason_codes == (LatencyReasonCode.TOTAL_LATENCY_BUDGET_EXCEEDED,)


def test_invalid_negative_timing_case_is_rejected_at_the_envelope_not_this_function() -> None:
    """LAT12-005 expects `reject`, Task 10's meaning for an invalid
    internal candidate -- exactly what a negative `elapsed_ms` already
    produces at Task 1's own envelope boundary, before
    `check_latency_budget()` would ever be called."""
    case = next(c for c in LATENCY_BUDGET_CASES if c["id"] == "LAT12-005")
    assert case["total_latency_ms"] < 0
    with pytest.raises(FinalResponseEnvelopeError) as excinfo:
        parse_final_response_candidate(
            _base_envelope(elapsed_ms=case["total_latency_ms"], model_latency_ms=case["model_latency_ms"])
        )
    assert excinfo.value.field_path == "elapsed_ms"


# ══════════════════════════════════════════════════════════════════════
# Section 2 -- specific Task 8 behaviors.
# ══════════════════════════════════════════════════════════════════════


def test_missing_required_timing_is_rejected_at_the_envelope() -> None:
    """Task 8's own "missing required timing" required case -- also a
    Task 1 envelope guarantee, not this function's job."""
    payload = _base_envelope()
    del payload["elapsed_ms"]
    with pytest.raises(FinalResponseEnvelopeError):
        parse_final_response_candidate(payload)


@pytest.mark.parametrize("field_name", ["total_latency_ms", "model_latency_ms"])
def test_threshold_is_inclusive_false_makes_exact_threshold_fail(field_name: str) -> None:
    """No shipped fixture exercises `threshold_is_inclusive=False` --
    proven directly: a candidate measured at exactly the budget now
    exceeds it (strictly under required, not at-or-under)."""
    strict_budgets = REAL_LATENCY_BUDGETS.model_copy(update={"threshold_is_inclusive": False})
    at_threshold = {
        "elapsed_ms": REAL_LATENCY_BUDGETS.max_total_latency_ms,
        "model_latency_ms": REAL_LATENCY_BUDGETS.max_model_latency_ms,
    }
    candidate = _candidate_from(**at_threshold)
    report = check_latency_budget(candidate, policy=strict_budgets)
    assert report.passed is False


def test_threshold_is_inclusive_false_still_passes_strictly_under_budget() -> None:
    strict_budgets = REAL_LATENCY_BUDGETS.model_copy(update={"threshold_is_inclusive": False})
    candidate = _candidate_from(
        elapsed_ms=REAL_LATENCY_BUDGETS.max_total_latency_ms - 1,
        model_latency_ms=REAL_LATENCY_BUDGETS.max_model_latency_ms - 1,
    )
    report = check_latency_budget(candidate, policy=strict_budgets)
    assert report.passed is True


def test_both_budgets_exceeded_at_once_names_both_reasons() -> None:
    candidate = _candidate_from(
        elapsed_ms=REAL_LATENCY_BUDGETS.max_total_latency_ms + 100,
        model_latency_ms=REAL_LATENCY_BUDGETS.max_model_latency_ms + 100,
    )
    report = check_latency_budget(candidate, policy=REAL_LATENCY_BUDGETS)
    assert report.passed is False
    assert set(report.reason_codes) == {
        LatencyReasonCode.TOTAL_LATENCY_BUDGET_EXCEEDED,
        LatencyReasonCode.MODEL_LATENCY_BUDGET_EXCEEDED,
    }


def test_report_never_fabricates_faster_telemetry() -> None:
    """"Do not fabricate faster telemetry" -- the report's own
    `total_latency_ms`/`model_latency_ms` are exactly what the candidate
    carried, never rounded down or otherwise adjusted toward passing."""
    measured_total = REAL_LATENCY_BUDGETS.max_total_latency_ms + 37
    measured_model = REAL_LATENCY_BUDGETS.max_model_latency_ms + 5
    candidate = _candidate_from(elapsed_ms=measured_total, model_latency_ms=measured_model)
    report = check_latency_budget(candidate, policy=REAL_LATENCY_BUDGETS)
    assert report.total_latency_ms == measured_total
    assert report.model_latency_ms == measured_model
    assert report.passed is False


def test_report_carries_the_budget_it_was_checked_against() -> None:
    candidate = _candidate_from()
    report = check_latency_budget(candidate, policy=REAL_LATENCY_BUDGETS)
    assert report.max_total_latency_ms == REAL_LATENCY_BUDGETS.max_total_latency_ms
    assert report.max_model_latency_ms == REAL_LATENCY_BUDGETS.max_model_latency_ms


def test_report_never_raises_for_an_ordinary_input() -> None:
    candidate = _candidate_from(elapsed_ms=0, model_latency_ms=0)
    report = check_latency_budget(candidate, policy=REAL_LATENCY_BUDGETS)
    assert isinstance(report, LatencyCheckReport)
    assert report.passed is True


def test_latency_budgets_type_reused_directly() -> None:
    assert isinstance(REAL_LATENCY_BUDGETS, LatencyBudgets)


def test_zero_or_negative_policy_budget_rejected_at_policy_load() -> None:
    """A hard budget of zero or less would be a nonsensical/unreachable
    policy -- already rejected at Task 2's own policy-document boundary,
    not something this function has to separately guard against."""
    with pytest.raises(ValidationError):
        LatencyBudgets(max_total_latency_ms=0, max_model_latency_ms=1400, threshold_is_inclusive=True)
    with pytest.raises(ValidationError):
        LatencyBudgets(max_total_latency_ms=2500, max_model_latency_ms=-1, threshold_is_inclusive=True)
