"""
Day 12 Task 1 -- the typed final-response envelope
(`src/aico/control/final_response.py`).
Day 12 Task 10 -- the Gate-D decision contract itself (`GateD`/
`GateDDecision`, `src/aico/control/gate_d.py`) -- its own section near
the end of this file.

The first part of this file proves only the envelope every other Gate-D
task builds on: a well-formed candidate parses into a typed
`FinalResponseCandidate`, and every one of Task 1's own named "Required
validation" cases (missing response status, malformed citation structure,
invalid/negative latency values, missing control metadata, unknown final
status) is rejected with a sanitized `FinalResponseEnvelopeError` -- never
a raw `pydantic.ValidationError`, and never a silently-defaulted/
partially-valid envelope. The "Day 12 Task 10" section proves the full
orchestration: every one of Tasks 3-9's own checks, combined by
`GateD.evaluate()` into one `GateDDecision` -- `allow`/`safe_failure`/
`reject` from single-check and multi-check failures alike, the "reject
outranks safe_failure" combination rule, and the least-privilege
`validated_citation_ids`/`safe_failure_code` population.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from aico.contracts.models import AnswerStatus
from aico.control.disclosure import ProtectedField
from aico.control.errors import FinalResponseEnvelopeError
from aico.control.final_response import (
    FinalCitation,
    FinalResponseCandidate,
    ValidationStatus,
    parse_final_response_candidate,
)
from aico.control.gate_d import (
    CitationReasonCode,
    GateCEvidenceRecord,
    GateD,
    GateDDecision,
    GateDStatus,
)
from aico.control.models import GateBDecision, GateBStatus
from aico.control.ontology import LaneId
from aico.control.policy_models import DataClassification, PiiCategory
from aico.control.policy_registry import GateDPolicyRegistry, PolicyRegistry
from aico.control.quality import QualityReasonCode

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


# ══════════════════════════════════════════════════════════════════════
# Day 12 Task 10 -- the Gate-D decision contract (`GateD`/`GateDDecision`).
# ══════════════════════════════════════════════════════════════════════

_GATE_B_POLICY = PolicyRegistry.load()
_GATE_D_POLICY_REGISTRY = GateDPolicyRegistry.load(gate_b_policy=_GATE_B_POLICY)
_GATE_D = GateD(policy_registry=_GATE_D_POLICY_REGISTRY)

_VALID_CITATION = {"evidence_id": "E-101", "chunk_id": "CH-101", "source_id": "SRC-POLICY-A", "source_version": "3"}
_VALID_EVIDENCE = (GateCEvidenceRecord.model_validate(_VALID_CITATION),)


def _gate_b_decision(profile_id: str = "policy_reader") -> GateBDecision:
    return GateBDecision(
        decision=GateBStatus.ALLOW,
        effective_tenant_scope=("TENANT-A",),
        effective_data_classes=(DataClassification.PUBLIC, DataClassification.INTERNAL),
        effective_pii_policy=(PiiCategory.NONE, PiiCategory.CONTACT),
        disclosure_profile=profile_id,
        lane=LaneId.RAG,
        reason_code="test_fixture",
        policy_version=_GATE_B_POLICY.policy_version,
    )


def _evaluate(candidate: FinalResponseCandidate, **overrides: object) -> GateDDecision:
    kwargs: dict = {
        "candidate": candidate,
        "gate_c_validated_evidence": _VALID_EVIDENCE,
        "gate_b_decision": _gate_b_decision(),
        "disclosure_profile": _GATE_B_POLICY.get_disclosure_profile("policy_reader"),
        "protected_fields": (),
    }
    kwargs.update(overrides)
    return _GATE_D.evaluate(**kwargs)


def _valid_candidate(**overrides: object) -> FinalResponseCandidate:
    defaults: dict = {
        "candidate_citations": [_VALID_CITATION],
        "gate_c_validated_evidence_ids": ["E-101"],
        "gate_b_disclosure_profile": "policy_reader",
    }
    defaults.update(overrides)
    return parse_final_response_candidate(_base_envelope(**defaults))


def test_fully_valid_candidate_allows() -> None:
    decision = _evaluate(_valid_candidate())
    assert decision.decision is GateDStatus.ALLOW
    assert decision.reason_codes == ()
    assert decision.validated_citation_ids == ("E-101",)
    assert decision.safe_failure_code is None
    assert decision.quality_checks.passed is True
    assert decision.citation_checks.passed is True
    assert decision.disclosure_checks.passed is True
    assert decision.latency_checks.passed is True


def test_forged_citation_alone_is_safe_failure() -> None:
    candidate = _valid_candidate(
        candidate_citations=[{"evidence_id": "E-999", "chunk_id": "CH-999", "source_id": "SRC-FAKE", "source_version": "1"}],
        gate_c_validated_evidence_ids=[],
    )
    decision = _evaluate(candidate, gate_c_validated_evidence=())
    assert decision.decision is GateDStatus.SAFE_FAILURE
    assert decision.safe_failure_code == _GATE_D_POLICY_REGISTRY.safe_failure.code
    assert CitationReasonCode.CITATION_NOT_GATE_C_VALIDATED.value in decision.reason_codes
    assert decision.validated_citation_ids == ()


def test_empty_answer_alone_is_safe_failure() -> None:
    decision = _evaluate(_valid_candidate(candidate_answer=""))
    assert decision.decision is GateDStatus.SAFE_FAILURE
    assert decision.quality_checks.passed is False


def test_disclosure_leak_alone_is_safe_failure() -> None:
    candidate = _valid_candidate(candidate_answer="Tax identifier is SYN-ID-123456.")
    decision = _evaluate(
        candidate,
        protected_fields=(
            ProtectedField(
                name="tax_identifier",
                value="SYN-ID-123456",
                data_class=DataClassification.CONFIDENTIAL,
                pii_category=PiiCategory.PERSONAL_IDENTIFIER,
            ),
        ),
    )
    assert decision.decision is GateDStatus.SAFE_FAILURE
    assert decision.disclosure_checks.passed is False


def test_secret_pattern_alone_is_safe_failure() -> None:
    candidate = _valid_candidate(candidate_answer="Authorization value: Bearer SYNTHETIC_SECRET_TOKEN")
    decision = _evaluate(candidate)
    assert decision.decision is GateDStatus.SAFE_FAILURE
    assert decision.disclosure_checks.secret_checks.passed is False


def test_latency_budget_exceeded_alone_is_safe_failure() -> None:
    over_budget = _GATE_D_POLICY_REGISTRY.latency_budgets.max_total_latency_ms + 1
    decision = _evaluate(_valid_candidate(elapsed_ms=over_budget))
    assert decision.decision is GateDStatus.SAFE_FAILURE
    assert decision.latency_checks.passed is False


def test_contract_invalid_alone_is_reject() -> None:
    decision = _evaluate(_valid_candidate(contract_validation_status="failed"))
    assert decision.decision is GateDStatus.REJECT
    assert decision.safe_failure_code is None
    assert QualityReasonCode.CONTRACT_VALIDATION_FAILED.value in decision.reason_codes


def test_semantic_invalid_alone_is_reject() -> None:
    decision = _evaluate(_valid_candidate(semantic_validation_status="failed"))
    assert decision.decision is GateDStatus.REJECT


def test_reject_outranks_safe_failure_across_categories() -> None:
    """A candidate failing both a reject-level check (contract) and a
    safe_failure-level one (a forged citation, an entirely different
    category) is still reported as `reject` overall."""
    candidate = _valid_candidate(
        contract_validation_status="failed",
        candidate_citations=[{"evidence_id": "E-999", "chunk_id": "CH-999", "source_id": "SRC-FAKE", "source_version": "1"}],
        gate_c_validated_evidence_ids=[],
    )
    decision = _evaluate(candidate, gate_c_validated_evidence=())
    assert decision.decision is GateDStatus.REJECT
    assert decision.safe_failure_code is None
    assert decision.citation_checks.passed is False  # still ran, still reported
    assert QualityReasonCode.CONTRACT_VALIDATION_FAILED.value in decision.reason_codes
    assert CitationReasonCode.CITATION_NOT_GATE_C_VALIDATED.value in decision.reason_codes


def test_multiple_safe_failure_reasons_all_accumulate() -> None:
    over_budget = _GATE_D_POLICY_REGISTRY.latency_budgets.max_total_latency_ms + 1
    candidate = _valid_candidate(candidate_answer="", elapsed_ms=over_budget)
    decision = _evaluate(candidate)
    assert decision.decision is GateDStatus.SAFE_FAILURE
    assert QualityReasonCode.EMPTY_ANSWERED_RESULT.value in decision.reason_codes
    from aico.control.gate_d import LatencyReasonCode

    assert LatencyReasonCode.TOTAL_LATENCY_BUDGET_EXCEEDED.value in decision.reason_codes


def test_validated_citation_ids_empty_unless_allow() -> None:
    """Least privilege, the identical convention `GateCDecision.
    validated_evidence_ids` already gives: even a citation that was
    individually valid is not surfaced once the overall decision is not
    `allow`."""
    decision = _evaluate(_valid_candidate(candidate_answer=""))  # citation itself is fine; quality fails
    assert decision.decision is GateDStatus.SAFE_FAILURE
    assert decision.citation_checks.passed is True  # the sub-check itself did pass
    assert decision.validated_citation_ids == ()  # but not surfaced


def test_sub_reports_always_populated_regardless_of_decision() -> None:
    decision = _evaluate(_valid_candidate(contract_validation_status="failed"))
    assert decision.decision is GateDStatus.REJECT
    assert decision.citation_checks is not None
    assert decision.quality_checks is not None
    assert decision.disclosure_checks is not None
    assert decision.latency_checks is not None


def test_policy_version_matches_the_real_loaded_policy() -> None:
    decision = _evaluate(_valid_candidate())
    assert decision.policy_version == _GATE_D_POLICY_REGISTRY.policy_version == "1.0"


def test_insufficient_evidence_clean_candidate_allows() -> None:
    candidate = _valid_candidate(
        candidate_status="insufficient_evidence",
        candidate_answer="INSUFFICIENT_EVIDENCE: available evidence does not support the requested fact.",
        candidate_citations=[],
        gate_c_validated_evidence_ids=[],
    )
    decision = _evaluate(candidate, gate_c_validated_evidence=())
    assert decision.decision is GateDStatus.ALLOW


def test_never_raises_for_an_ordinary_input() -> None:
    decision = _evaluate(_valid_candidate(candidate_answer=""))
    assert isinstance(decision, GateDDecision)


def test_gate_d_is_reusable_across_multiple_evaluations() -> None:
    """Built once, reused for every request -- the identical pattern
    `GateC`'s own docstring describes."""
    first = _evaluate(_valid_candidate())
    second = _evaluate(_valid_candidate(candidate_answer=""))
    assert first.decision is GateDStatus.ALLOW
    assert second.decision is GateDStatus.SAFE_FAILURE


def test_gate_d_policy_registry_type_reused_directly() -> None:
    assert isinstance(_GATE_D.policy_registry, GateDPolicyRegistry)
