"""
Day 12 Task 13 -- observability / audit metadata (`GateD.evaluate()`,
`src/aico/control/gate_d.py`).

Mirrors `test_day11_observability.py`'s own structure exactly, one gate
further down the pipeline, using the identical in-memory OTel exporter
(`aico.observability.telemetry.get_finished_spans`/`clear_finished_spans`):

  - a `"gate_d"` span exists for every `evaluate()` call, carrying Task
    13's own named fields (mapped one-for-one in `gate_d.py`'s own "Task
    13" docstring subsection) -- `request_id`/`correlation_id`/`ontology_
    version`/`gate_b_policy_version`/`gate_c_policy_version` are
    deliberately NOT set here, the identical omission `gate_c.py`'s own
    span makes for the same reasons;
  - `"final_citation_validation"`/`"final_disclosure_validation"`/
    `"latency_budget_validation"` child spans exist on *every* call
    (unlike Gate-C's own conditional child spans -- `GateD.evaluate()`
    never short-circuits, every check always runs; see `gate_d.py`'s own
    "Day 12 Task 10" docstring section, "Decision algorithm");
  - `gate_d`/its child spans correctly nest under a caller's own span and
    share its `trace_id` (Day 6's correlation-propagation mechanism,
    reused unmodified);
  - no span attribute anywhere ever contains the candidate answer text, a
    raw protected value, a matched secret substring, or raw evidence
    content -- only governed ids/labels, sanitized reason-code strings,
    and counts.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from opentelemetry import trace

from aico.control.disclosure import ProtectedField
from aico.control.final_response import FinalResponseCandidate, parse_final_response_candidate
from aico.control.gate_d import GateCEvidenceRecord, GateD
from aico.control.models import GateBDecision, GateBStatus
from aico.control.ontology import LaneId
from aico.control.policy_models import DataClassification, PiiCategory
from aico.control.policy_registry import GateDPolicyRegistry, PolicyRegistry
from aico.observability.telemetry import clear_finished_spans, configure_tracing, get_finished_spans

_tracer = trace.get_tracer(__name__)

_STARTED_AT = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)

_UNSAFE_ANSWER_TEXT = "Synthetic Supplier Alpha uses net 30 payment terms."
_SECRET_TAX_ID = "SYN-ID-999999"


@pytest.fixture(scope="module", autouse=True)
def _tracing_configured():
    configure_tracing()


@pytest.fixture(scope="module")
def gate_b_policy() -> PolicyRegistry:
    return PolicyRegistry.load()


@pytest.fixture(scope="module")
def gate_d_policy(gate_b_policy: PolicyRegistry) -> GateDPolicyRegistry:
    return GateDPolicyRegistry.load(gate_b_policy=gate_b_policy)


@pytest.fixture
def gate_d(gate_d_policy: GateDPolicyRegistry) -> GateD:
    return GateD(policy_registry=gate_d_policy)


def _gate_b_decision(gate_b_policy: PolicyRegistry, *, profile_id: str = "policy_reader") -> GateBDecision:
    return GateBDecision(
        decision=GateBStatus.ALLOW,
        effective_tenant_scope=("TENANT-A",),
        effective_data_classes=(DataClassification.PUBLIC, DataClassification.INTERNAL),
        effective_pii_policy=(PiiCategory.NONE, PiiCategory.CONTACT),
        disclosure_profile=profile_id,
        lane=LaneId.RAG,
        reason_code="test_fixture",
        policy_version=gate_b_policy.policy_version,
    )


_VALID_CITATION = {"evidence_id": "E-101", "chunk_id": "CH-101", "source_id": "SRC-POLICY-A", "source_version": "3"}
_VALID_EVIDENCE = (GateCEvidenceRecord.model_validate(_VALID_CITATION),)


def _candidate(**overrides: object) -> FinalResponseCandidate:
    payload: dict = {
        "request_id": "REQ-1",
        "correlation_id": "CORR-1",
        "candidate_status": "answered",
        "candidate_answer": _UNSAFE_ANSWER_TEXT,
        "candidate_citations": [_VALID_CITATION],
        "gate_c_validated_evidence_ids": ["E-101"],
        "gate_b_disclosure_profile": "policy_reader",
        "started_at": _STARTED_AT.isoformat(),
        "elapsed_ms": 1200,
        "model_latency_ms": 800,
        "contract_validation_status": "passed",
        "semantic_validation_status": "passed",
    }
    payload.update(overrides)
    return parse_final_response_candidate(payload)


def _evaluate(gate_d: GateD, gate_b_policy: PolicyRegistry, candidate: FinalResponseCandidate, **overrides: object):
    kwargs: dict = {
        "candidate": candidate,
        "gate_c_validated_evidence": _VALID_EVIDENCE,
        "gate_b_decision": _gate_b_decision(gate_b_policy),
        "disclosure_profile": gate_b_policy.get_disclosure_profile("policy_reader"),
        "protected_fields": (),
    }
    kwargs.update(overrides)
    return gate_d.evaluate(**kwargs)


# ---------------------------------------------------------------------------
# "gate_d" span exists with the required sanitized fields
# ---------------------------------------------------------------------------


def test_gate_d_span_exists_with_required_fields_on_allow(gate_d, gate_b_policy, gate_d_policy):
    clear_finished_spans()
    _evaluate(gate_d, gate_b_policy, _candidate())

    spans = {s.name: s for s in get_finished_spans()}
    assert "gate_d" in spans
    gate_d_span = spans["gate_d"]
    for field_name in (
        "policy_version", "candidate_status", "citation_count", "validated_citation_count",
        "quality_result", "disclosure_result", "latency_result", "total_latency_ms",
        "model_latency_ms", "decision", "reason_codes", "latency_ms",
    ):
        assert f"gate_d.{field_name}" in gate_d_span.attributes

    assert gate_d_span.attributes["gate_d.policy_version"] == gate_d_policy.policy_version
    assert gate_d_span.attributes["gate_d.candidate_status"] == "answered"
    assert gate_d_span.attributes["gate_d.citation_count"] == 1
    assert gate_d_span.attributes["gate_d.validated_citation_count"] == 1
    assert gate_d_span.attributes["gate_d.quality_result"] == "passed"
    assert gate_d_span.attributes["gate_d.disclosure_result"] == "passed"
    assert gate_d_span.attributes["gate_d.latency_result"] == "passed"
    assert gate_d_span.attributes["gate_d.total_latency_ms"] == 1200
    assert gate_d_span.attributes["gate_d.model_latency_ms"] == 800
    assert gate_d_span.attributes["gate_d.decision"] == "allow"
    assert gate_d_span.attributes["gate_d.reason_codes"] == ""
    assert isinstance(gate_d_span.attributes["gate_d.latency_ms"], float)


def test_gate_d_span_never_carries_request_or_ontology_metadata_of_its_own(gate_d, gate_b_policy):
    """Deliberately not `GateD`'s own to set -- see `gate_d.py`'s own
    "Task 13" docstring subsection."""
    clear_finished_spans()
    _evaluate(gate_d, gate_b_policy, _candidate())

    gate_d_span = {s.name: s for s in get_finished_spans()}["gate_d"]
    for absent_key in (
        "gate_d.request_id", "gate_d.correlation_id", "gate_d.ontology_version",
        "gate_d.gate_b_policy_version", "gate_d.gate_c_policy_version",
    ):
        assert absent_key not in gate_d_span.attributes


def test_gate_d_span_reflects_safe_failure(gate_d, gate_b_policy):
    clear_finished_spans()
    forged = _candidate(
        candidate_citations=[{"evidence_id": "E-999", "chunk_id": "CH-999", "source_id": "SRC-FAKE", "source_version": "1"}],
        gate_c_validated_evidence_ids=[],
    )

    _evaluate(gate_d, gate_b_policy, forged, gate_c_validated_evidence=())

    span = {s.name: s for s in get_finished_spans()}["gate_d"]
    assert span.attributes["gate_d.decision"] == "safe_failure"
    assert "citation_not_gate_c_validated" in span.attributes["gate_d.reason_codes"]
    assert span.attributes["gate_d.validated_citation_count"] == 0


def test_gate_d_span_reflects_reject(gate_d, gate_b_policy):
    clear_finished_spans()
    invalid_contract = _candidate(contract_validation_status="failed")

    _evaluate(gate_d, gate_b_policy, invalid_contract)

    span = {s.name: s for s in get_finished_spans()}["gate_d"]
    assert span.attributes["gate_d.decision"] == "reject"
    assert span.attributes["gate_d.quality_result"] == "reject"
    assert "contract_validation_failed" in span.attributes["gate_d.reason_codes"]


# ---------------------------------------------------------------------------
# Child spans: always present, nested under "gate_d"
# ---------------------------------------------------------------------------


def test_all_three_named_child_spans_exist_and_nest_under_gate_d(gate_d, gate_b_policy):
    clear_finished_spans()
    _evaluate(gate_d, gate_b_policy, _candidate())

    spans = {s.name: s for s in get_finished_spans()}
    gate_d_span = spans["gate_d"]
    for name in ("final_citation_validation", "final_disclosure_validation", "latency_budget_validation"):
        assert name in spans, name
        child = spans[name]
        assert child.context.trace_id == gate_d_span.context.trace_id
        assert child.parent is not None
        assert child.parent.span_id == gate_d_span.context.span_id


def test_child_spans_present_even_on_reject():
    """Unlike Gate-C's own conditional child spans, Gate-D never
    short-circuits -- every check runs regardless of any other check's
    outcome (`gate_d.py`'s own "Decision algorithm" section)."""
    clear_finished_spans()
    gate_b_policy = PolicyRegistry.load()
    gate_d = GateD(policy_registry=GateDPolicyRegistry.load(gate_b_policy=gate_b_policy))

    _evaluate(gate_d, gate_b_policy, _candidate(contract_validation_status="failed"))

    span_names = {s.name for s in get_finished_spans()}
    for name in ("final_citation_validation", "final_disclosure_validation", "latency_budget_validation"):
        assert name in span_names


def test_no_fifth_quality_validation_child_span_exists(gate_d, gate_b_policy):
    """Task 13 names exactly four spans (`gate_d` plus the three child
    spans) -- there is deliberately no separate `final_quality_validation`
    span."""
    clear_finished_spans()
    _evaluate(gate_d, gate_b_policy, _candidate())

    span_names = {s.name for s in get_finished_spans()}
    assert "final_quality_validation" not in span_names


# ---------------------------------------------------------------------------
# Correlation context: nests under a caller's own span, shares trace_id
# ---------------------------------------------------------------------------


def test_gate_d_shares_trace_id_with_a_parent_span(gate_d, gate_b_policy):
    clear_finished_spans()

    with _tracer.start_as_current_span("test.caller"):
        _evaluate(gate_d, gate_b_policy, _candidate())

    spans = {s.name: s for s in get_finished_spans()}
    root_span = spans["test.caller"]
    gate_d_span = spans["gate_d"]

    assert gate_d_span.context.trace_id == root_span.context.trace_id
    assert gate_d_span.parent is not None
    assert gate_d_span.parent.span_id == root_span.context.span_id

    for span in get_finished_spans():
        assert span.context.trace_id == root_span.context.trace_id


# ---------------------------------------------------------------------------
# Sanitization: never the candidate answer, a raw protected value, a
# matched secret, or raw evidence
# ---------------------------------------------------------------------------


def test_no_span_attribute_ever_contains_the_candidate_answer_text(gate_d, gate_b_policy):
    clear_finished_spans()
    _evaluate(gate_d, gate_b_policy, _candidate())

    all_values = {str(v) for s in get_finished_spans() for v in s.attributes.values()}
    assert not any(_UNSAFE_ANSWER_TEXT in v for v in all_values)


def test_no_span_attribute_ever_contains_a_raw_protected_value_or_matched_secret(gate_d, gate_b_policy):
    clear_finished_spans()
    candidate = _candidate(
        candidate_answer=f"Tax identifier is {_SECRET_TAX_ID}.",
    )

    _evaluate(
        gate_d,
        gate_b_policy,
        candidate,
        protected_fields=(
            ProtectedField(
                name="tax_identifier",
                value=_SECRET_TAX_ID,
                data_class=DataClassification.CONFIDENTIAL,
                pii_category=PiiCategory.PERSONAL_IDENTIFIER,
            ),
        ),
    )

    all_values = {str(v) for s in get_finished_spans() for v in s.attributes.values()}
    assert not any(_SECRET_TAX_ID in v for v in all_values)


def test_no_span_attribute_ever_contains_raw_evidence_content(gate_d, gate_b_policy):
    clear_finished_spans()
    _evaluate(gate_d, gate_b_policy, _candidate())

    all_values = {str(v) for s in get_finished_spans() for v in s.attributes.values()}
    # Only governed ids/counts/labels ever appear -- never a citation's own
    # source_id/chunk_id/evidence_id values (which would only be safe/
    # useful as *counts* here, not as raw strings).
    assert "E-101" not in all_values
    assert "SRC-POLICY-A" not in all_values
