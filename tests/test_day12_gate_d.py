"""
Day 12 Task 1 -- the typed final-response envelope
(`src/aico/control/final_response.py`).

Gate-D's own decision contract (Task 10) is a later task -- this file
today proves only the envelope every later Gate-D task builds on: a
well-formed candidate parses into a typed `FinalResponseCandidate`, and
every one of Task 1's own named "Required validation" cases (missing
response status, malformed citation structure, invalid/negative latency
values, missing control metadata, unknown final status) is rejected with a
sanitized `FinalResponseEnvelopeError` -- never a raw `pydantic.
ValidationError`, and never a silently-defaulted/partially-valid envelope.
Later Day 12 tasks (citation reconciliation, quality, disclosure, latency
budget, safe failure, the Gate-D decision contract itself) add their own
sections/tests to this same file as they land.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from aico.contracts.models import AnswerStatus
from aico.control.errors import FinalResponseEnvelopeError
from aico.control.final_response import (
    FinalCitation,
    FinalResponseCandidate,
    ValidationStatus,
    parse_final_response_candidate,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DAY12_FIXTURES = REPO_ROOT / "data" / "day12_pack" / "fixtures"
GATE_D_POLICY = json.loads((DAY12_FIXTURES / "gate_d_policy_v1.json").read_text(encoding="utf-8"))
FINAL_CITATION_CASES = json.loads((DAY12_FIXTURES / "final_citation_cases.json").read_text(encoding="utf-8"))["cases"]
FINAL_QUALITY_CASES = json.loads((DAY12_FIXTURES / "final_quality_cases.json").read_text(encoding="utf-8"))["cases"]

_STARTED_AT = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)


def _base_envelope(**overrides: object) -> dict:
    """A minimal, well-formed candidate payload -- every test below starts
    from this and mutates exactly the field(s) under test, so a failure
    is attributable to that one field, not to some other omitted one."""
    payload: dict = {
        "request_id": "REQ-1",
        "correlation_id": "CORR-1",
        "candidate_status": "answered",
        "candidate_answer": "Synthetic Supplier Alpha uses net 30 payment terms.",
        "candidate_citations": [
            {"evidence_id": "E-101", "chunk_id": "CH-101", "source_id": "SRC-POLICY-A", "source_version": "3"}
        ],
        "gate_c_validated_evidence_ids": ["E-101"],
        "gate_b_disclosure_profile": "policy_reader",
        "started_at": _STARTED_AT.isoformat(),
        "elapsed_ms": 1200,
        "model_latency_ms": 800,
        "contract_validation_status": "passed",
        "semantic_validation_status": "passed",
    }
    payload.update(overrides)
    return payload


# -- valid envelopes -----------------------------------------------------


def test_valid_envelope_parses() -> None:
    candidate = parse_final_response_candidate(_base_envelope())
    assert isinstance(candidate, FinalResponseCandidate)
    assert candidate.candidate_status is AnswerStatus.ANSWERED
    assert candidate.contract_validation_status is ValidationStatus.PASSED
    assert candidate.semantic_validation_status is ValidationStatus.PASSED
    assert candidate.candidate_citations == (
        FinalCitation(evidence_id="E-101", chunk_id="CH-101", source_id="SRC-POLICY-A", source_version="3"),
    )
    assert candidate.gate_c_validated_evidence_ids == ("E-101",)


def test_citation_id_is_optional() -> None:
    """`final_citation_cases.json`'s own citations never carry a
    `citation_id` -- a fixed fixture, not to be edited to add one."""
    candidate = parse_final_response_candidate(_base_envelope())
    assert candidate.candidate_citations[0].citation_id is None


def test_citation_id_carried_through_when_supplied() -> None:
    payload = _base_envelope(
        candidate_citations=[
            {
                "citation_id": "CIT-1",
                "evidence_id": "E-101",
                "chunk_id": "CH-101",
                "source_id": "SRC-POLICY-A",
                "source_version": "3",
            }
        ]
    )
    candidate = parse_final_response_candidate(payload)
    assert candidate.candidate_citations[0].citation_id == "CIT-1"


def test_insufficient_evidence_may_carry_zero_citations() -> None:
    payload = _base_envelope(
        candidate_status="insufficient_evidence",
        candidate_answer="INSUFFICIENT_EVIDENCE: available evidence does not support the requested fact.",
        candidate_citations=[],
        gate_c_validated_evidence_ids=[],
    )
    candidate = parse_final_response_candidate(payload)
    assert candidate.candidate_status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert candidate.candidate_citations == ()
    assert candidate.gate_c_validated_evidence_ids == ()


def test_empty_candidate_answer_is_a_well_formed_shape() -> None:
    """Task 1 does not reject an empty answer at the shape layer --
    `final_quality_cases.json` QUAL12-002 sends one explicitly and expects
    a later, semantic `safe_failure` (Task 5), not a shape rejection."""
    candidate = parse_final_response_candidate(_base_envelope(candidate_answer=""))
    assert candidate.candidate_answer == ""


def test_disclosure_profile_is_optional() -> None:
    """`disclosure_leak_cases.json` DISC12-005/DISC12-006 name no
    `disclosure_profile` at all -- a well-formed candidate may legitimately
    carry none."""
    payload = _base_envelope()
    del payload["gate_b_disclosure_profile"]
    candidate = parse_final_response_candidate(payload)
    assert candidate.gate_b_disclosure_profile is None


def test_exact_threshold_latency_is_valid() -> None:
    payload = _base_envelope(
        elapsed_ms=GATE_D_POLICY["latency_budgets"]["max_total_latency_ms"],
        model_latency_ms=GATE_D_POLICY["latency_budgets"]["max_model_latency_ms"],
    )
    candidate = parse_final_response_candidate(payload)
    assert candidate.elapsed_ms == 2500
    assert candidate.model_latency_ms == 1400


# -- Task 1's own required rejection cases --------------------------------


def test_missing_response_status_rejected() -> None:
    payload = _base_envelope()
    del payload["candidate_status"]
    with pytest.raises(FinalResponseEnvelopeError) as excinfo:
        parse_final_response_candidate(payload)
    assert excinfo.value.field_path == "candidate_status"


def test_unknown_final_status_rejected() -> None:
    payload = _base_envelope(candidate_status="fabricated_status")
    with pytest.raises(FinalResponseEnvelopeError) as excinfo:
        parse_final_response_candidate(payload)
    assert excinfo.value.field_path == "candidate_status"


@pytest.mark.parametrize(
    "malformed_citation",
    [
        {"chunk_id": "CH-101", "source_id": "SRC-POLICY-A", "source_version": "3"},  # missing evidence_id
        {"evidence_id": "E-101", "source_id": "SRC-POLICY-A", "source_version": "3"},  # missing chunk_id
        {"evidence_id": "E-101", "chunk_id": "CH-101", "source_version": "3"},  # missing source_id
        {"evidence_id": "E-101", "chunk_id": "CH-101", "source_id": "SRC-POLICY-A"},  # missing source_version
        {"evidence_id": "", "chunk_id": "CH-101", "source_id": "SRC-POLICY-A", "source_version": "3"},  # blank
        {"evidence_id": "E-101", "chunk_id": "CH-101", "source_id": "SRC-POLICY-A", "source_version": "3", "unexpected": "x"},
    ],
)
def test_malformed_citation_structure_rejected(malformed_citation: dict) -> None:
    payload = _base_envelope(candidate_citations=[malformed_citation])
    with pytest.raises(FinalResponseEnvelopeError):
        parse_final_response_candidate(payload)


@pytest.mark.parametrize("field_name", ["elapsed_ms", "model_latency_ms"])
def test_invalid_negative_timing_rejected(field_name: str) -> None:
    payload = _base_envelope(**{field_name: -1})
    with pytest.raises(FinalResponseEnvelopeError) as excinfo:
        parse_final_response_candidate(payload)
    assert excinfo.value.field_path == field_name


def test_missing_started_at_rejected() -> None:
    payload = _base_envelope()
    del payload["started_at"]
    with pytest.raises(FinalResponseEnvelopeError):
        parse_final_response_candidate(payload)


@pytest.mark.parametrize(
    "missing_field",
    [
        "request_id",
        "correlation_id",
        "contract_validation_status",
        "semantic_validation_status",
        "elapsed_ms",
        "model_latency_ms",
    ],
)
def test_missing_control_metadata_rejected(missing_field: str) -> None:
    payload = _base_envelope()
    del payload[missing_field]
    with pytest.raises(FinalResponseEnvelopeError) as excinfo:
        parse_final_response_candidate(payload)
    assert excinfo.value.field_path == missing_field


@pytest.mark.parametrize("field_name", ["contract_validation_status", "semantic_validation_status"])
def test_unknown_validation_status_rejected(field_name: str) -> None:
    payload = _base_envelope(**{field_name: "maybe"})
    with pytest.raises(FinalResponseEnvelopeError) as excinfo:
        parse_final_response_candidate(payload)
    assert excinfo.value.field_path == field_name


@pytest.mark.parametrize("blank_id_field", ["request_id", "correlation_id"])
def test_blank_identifier_rejected(blank_id_field: str) -> None:
    payload = _base_envelope(**{blank_id_field: "   "})
    with pytest.raises(FinalResponseEnvelopeError):
        parse_final_response_candidate(payload)


def test_unknown_top_level_field_rejected() -> None:
    """Never a raw unchecked dict passed into Gate-D -- an unexpected key
    is a malformed envelope, not something silently dropped."""
    payload = _base_envelope(unexpected_field="surprise")
    with pytest.raises(FinalResponseEnvelopeError):
        parse_final_response_candidate(payload)


def test_parse_error_never_leaks_raw_pydantic_validation_error() -> None:
    with pytest.raises(FinalResponseEnvelopeError):
        try:
            parse_final_response_candidate(_base_envelope(candidate_status="bogus"))
        except ValidationError:  # pragma: no cover - proves it never escapes
            pytest.fail("raw pydantic.ValidationError leaked past the boundary parser")


def test_direct_model_construction_still_validates() -> None:
    """The boundary parser is the intended entry point, but constructing
    `FinalResponseCandidate` directly still enforces every rule -- there is
    no way to hand Gate-D a partially-valid instance either way."""
    with pytest.raises(ValidationError):
        FinalResponseCandidate.model_validate(_base_envelope(elapsed_ms=-5))


# -- fixture-driven shape proof -------------------------------------------


@pytest.mark.parametrize("case", FINAL_CITATION_CASES, ids=lambda case: case["id"])
def test_final_citation_cases_fixture_shape(case: dict) -> None:
    """Every `final_citation_cases.json` case's own `candidate_citations`
    is, at minimum, a well-formed set of `FinalCitation`s regardless of
    whether Gate-D will ultimately allow or reject the candidate -- Task 1
    proves shape only; Task 3 proves the actual reconciliation outcome."""
    payload = _base_envelope(
        candidate_status=case["candidate_status"],
        candidate_answer="Synthetic Supplier Alpha uses net 30 payment terms.",
        candidate_citations=case["candidate_citations"],
        gate_c_validated_evidence_ids=[item["evidence_id"] for item in case["gate_c_validated_evidence"]],
    )
    candidate = parse_final_response_candidate(payload)
    assert len(candidate.candidate_citations) == len(case["candidate_citations"])


@pytest.mark.parametrize("case", FINAL_QUALITY_CASES, ids=lambda case: case["id"])
def test_final_quality_cases_fixture_shape(case: dict) -> None:
    """Every `final_quality_cases.json` case parses as a well-formed
    envelope at the shape layer -- including the ones later, deterministic
    quality checks (Task 5) are expected to reject (empty answer, missing
    citation, oversized answer): those are semantic failures, not shape
    failures (identical split to `contracts/models.py`'s own D04-09 case)."""
    answer = case.get("candidate_answer")
    if answer is None:
        answer = "x" * case["candidate_answer_length"]
    citations = (
        [{"evidence_id": "E-101", "chunk_id": "CH-101", "source_id": "SRC-POLICY-A", "source_version": "3"}]
        if case["citation_count"] > 0
        else []
    )
    payload = _base_envelope(
        candidate_status=case["candidate_status"],
        candidate_answer=answer,
        candidate_citations=citations,
        gate_c_validated_evidence_ids=[c["evidence_id"] for c in citations],
        contract_validation_status="passed" if case["contract_valid"] else "failed",
        semantic_validation_status="passed" if case["semantic_valid"] else "failed",
    )
    candidate = parse_final_response_candidate(payload)
    assert candidate.candidate_answer == answer
