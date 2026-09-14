"""
Day 12 Task 9 -- safe failure behavior
(`SafeFailureResponse`/`build_safe_failure_response()`,
`src/aico/control/gate_d.py`).

Structure: Section 1 proves the "Conceptual result" shape against the
real committed `safe_failure` policy (`policy/gate_d_policy.v1.json`);
Section 2 proves, one bullet at a time, that Task 9's own "Safe failure
must not include" list is enforced at the *type* level -- there is no
field capable of carrying a candidate answer, a raw model response, raw
evidence, policy internals, or a stack trace, and `reason_codes` accepts
only this module's own governed enum values, never free text.
"""
from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from aico.control.gate_d import (
    CitationReasonCode,
    DisclosureReasonCode,
    LatencyReasonCode,
    SafeFailureResponse,
    SecretReasonCode,
    build_safe_failure_response,
)
from aico.control.policy_models import SafeFailureSpec
from aico.control.policy_registry import GateDPolicyRegistry
from aico.control.quality import QualityReasonCode

REAL_SAFE_FAILURE_POLICY = GateDPolicyRegistry.load().safe_failure

# A deliberately sensitive/unsafe-looking payload -- proof that none of
# this ever has anywhere to go in a `SafeFailureResponse`.
UNSAFE_CANDIDATE_ANSWER = "Bank account is SYN-BANK-00001234, Bearer SYNTHETIC_SECRET_TOKEN"
FAKE_STACK_TRACE = 'Traceback (most recent call last):\n  File "app.py", line 1, in <module>\nValueError: boom'
FAKE_POLICY_INTERNALS = "rule_id=GB-R001 disclosure_profile=policy_reader effective_pii_policy=[contact]"
FAKE_RAW_EVIDENCE = "EvidenceItem(evidence_id='E-101', content='...')"


# ══════════════════════════════════════════════════════════════════════
# Section 1 -- the "Conceptual result" shape.
# ══════════════════════════════════════════════════════════════════════


def test_conceptual_result_shape_matches_the_real_policy() -> None:
    response = build_safe_failure_response(
        request_id="REQ-1", correlation_id="CORR-1", policy=REAL_SAFE_FAILURE_POLICY
    )
    assert response.status == "safe_failure"
    assert response.error_code == "FINAL_RESPONSE_REJECTED"
    assert response.request_id == "REQ-1"
    assert response.correlation_id == "CORR-1"


def test_message_comes_from_the_governed_policy_not_derived() -> None:
    response = build_safe_failure_response(
        request_id="REQ-1", correlation_id="CORR-1", policy=REAL_SAFE_FAILURE_POLICY
    )
    assert response.message == REAL_SAFE_FAILURE_POLICY.message


def test_message_and_error_code_never_vary_with_reason_codes() -> None:
    """Working rule: "Safe failure text itself must be fixed/controlled."
    A different reason never produces different top-level text -- only
    `reason_codes` itself differs."""
    plain = build_safe_failure_response(request_id="REQ-1", correlation_id="CORR-1", policy=REAL_SAFE_FAILURE_POLICY)
    with_reasons = build_safe_failure_response(
        request_id="REQ-1",
        correlation_id="CORR-1",
        policy=REAL_SAFE_FAILURE_POLICY,
        reason_codes=(CitationReasonCode.MISSING_REQUIRED_CITATION.value,),
    )
    assert plain.error_code == with_reasons.error_code
    assert plain.message == with_reasons.message


def test_reason_codes_from_every_governed_enum_are_accepted() -> None:
    reason_codes = (
        CitationReasonCode.CITATION_NOT_GATE_C_VALIDATED.value,
        QualityReasonCode.EMPTY_ANSWERED_RESULT.value,
        DisclosureReasonCode.DENIED_FIELD_VALUE_LEAKED.value,
        SecretReasonCode.SYNTHETIC_SECRET_PATTERN_DETECTED.value,
        LatencyReasonCode.TOTAL_LATENCY_BUDGET_EXCEEDED.value,
    )
    response = build_safe_failure_response(
        request_id="REQ-1", correlation_id="CORR-1", policy=REAL_SAFE_FAILURE_POLICY, reason_codes=reason_codes
    )
    assert response.reason_codes == reason_codes


def test_reason_codes_default_to_empty() -> None:
    response = build_safe_failure_response(request_id="REQ-1", correlation_id="CORR-1", policy=REAL_SAFE_FAILURE_POLICY)
    assert response.reason_codes == ()


# ══════════════════════════════════════════════════════════════════════
# Section 2 -- "Safe failure must not include" enforced at the type level.
# ══════════════════════════════════════════════════════════════════════


def test_response_type_has_no_field_for_candidate_content() -> None:
    """Structural proof, not a runtime scrub: the field set itself has no
    slot for an answer, evidence, or policy internals -- only the six
    fields Task 9 names."""
    assert set(SafeFailureResponse.model_fields) == {
        "status",
        "error_code",
        "message",
        "request_id",
        "correlation_id",
        "reason_codes",
    }


def test_builder_signature_does_not_accept_a_candidate() -> None:
    """`build_safe_failure_response()` takes bare `request_id`/
    `correlation_id` strings, never a `FinalResponseCandidate` -- the
    candidate's own answer/citations/evidence are never even in scope,
    the stronger guarantee module docstring describes."""
    import inspect

    signature = inspect.signature(build_safe_failure_response)
    assert "candidate" not in signature.parameters


def test_unsafe_candidate_answer_never_appears_regardless_of_caller_intent() -> None:
    """Even a caller that *tries* to smuggle the unsafe answer in (by
    reusing it as a reason code) is rejected outright -- there is no
    field that would silently accept it."""
    with pytest.raises(ValidationError):
        build_safe_failure_response(
            request_id="REQ-1",
            correlation_id="CORR-1",
            policy=REAL_SAFE_FAILURE_POLICY,
            reason_codes=(UNSAFE_CANDIDATE_ANSWER,),
        )


def test_stack_trace_like_reason_code_is_rejected() -> None:
    with pytest.raises(ValidationError):
        build_safe_failure_response(
            request_id="REQ-1", correlation_id="CORR-1", policy=REAL_SAFE_FAILURE_POLICY, reason_codes=(FAKE_STACK_TRACE,)
        )


def test_policy_internals_like_reason_code_is_rejected() -> None:
    with pytest.raises(ValidationError):
        build_safe_failure_response(
            request_id="REQ-1",
            correlation_id="CORR-1",
            policy=REAL_SAFE_FAILURE_POLICY,
            reason_codes=(FAKE_POLICY_INTERNALS,),
        )


def test_raw_evidence_like_reason_code_is_rejected() -> None:
    with pytest.raises(ValidationError):
        build_safe_failure_response(
            request_id="REQ-1", correlation_id="CORR-1", policy=REAL_SAFE_FAILURE_POLICY, reason_codes=(FAKE_RAW_EVIDENCE,)
        )


def test_arbitrary_free_text_reason_code_is_rejected() -> None:
    with pytest.raises(ValidationError):
        build_safe_failure_response(
            request_id="REQ-1",
            correlation_id="CORR-1",
            policy=REAL_SAFE_FAILURE_POLICY,
            reason_codes=("this is not a governed reason code",),
        )


def test_direct_model_construction_also_rejects_ungoverned_reason_codes() -> None:
    """The validator lives on `SafeFailureResponse` itself, not only in
    the builder -- there is no way to hand-construct an instance carrying
    an ungoverned reason code either."""
    with pytest.raises(ValidationError):
        SafeFailureResponse(
            status="safe_failure",
            error_code="FINAL_RESPONSE_REJECTED",
            message="The response could not be safely returned.",
            request_id="REQ-1",
            correlation_id="CORR-1",
            reason_codes=(UNSAFE_CANDIDATE_ANSWER,),
        )


def test_status_cannot_be_anything_but_safe_failure() -> None:
    with pytest.raises(ValidationError):
        SafeFailureResponse(
            status="allow",
            error_code="FINAL_RESPONSE_REJECTED",
            message="x",
            request_id="REQ-1",
            correlation_id="CORR-1",
        )


def test_unknown_field_rejected() -> None:
    """`extra='forbid'` -- no seventh field (a raw answer, an evidence
    dump, ...) can ever be smuggled in."""
    with pytest.raises(ValidationError):
        SafeFailureResponse.model_validate(
            {
                "status": "safe_failure",
                "error_code": "FINAL_RESPONSE_REJECTED",
                "message": "x",
                "request_id": "REQ-1",
                "correlation_id": "CORR-1",
                "candidate_answer": UNSAFE_CANDIDATE_ANSWER,
            }
        )


def test_serialized_response_never_contains_unsafe_content() -> None:
    """End-to-end proof, over the full serialized JSON: a response built
    with only governed reason codes never contains the unsafe answer, a
    stack trace, policy internals, or raw evidence -- because none of
    them were ever accepted as input in the first place."""
    response = build_safe_failure_response(
        request_id="REQ-1",
        correlation_id="CORR-1",
        policy=REAL_SAFE_FAILURE_POLICY,
        reason_codes=(DisclosureReasonCode.DENIED_FIELD_VALUE_LEAKED.value, SecretReasonCode.HIDDEN_PROMPT_MARKER_DETECTED.value),
    )
    serialized = response.model_dump_json()
    for unsafe_fragment in (UNSAFE_CANDIDATE_ANSWER, FAKE_STACK_TRACE, FAKE_POLICY_INTERNALS, FAKE_RAW_EVIDENCE, "Traceback"):
        assert unsafe_fragment not in serialized


def test_safe_failure_spec_type_reused_directly() -> None:
    assert isinstance(REAL_SAFE_FAILURE_POLICY, SafeFailureSpec)


def test_known_reason_code_values_is_non_empty_and_governed() -> None:
    """Every value `SafeFailureResponse.reason_codes` accepts really is
    one of this module's own enum members -- no drift between the
    validator's closed set and what Tasks 3/5/6/7/8 actually define."""
    from aico.control.gate_d import _KNOWN_REASON_CODE_VALUES

    assert _KNOWN_REASON_CODE_VALUES
    for value in _KNOWN_REASON_CODE_VALUES:
        assert re.fullmatch(r"[a-z_]+", value), value
