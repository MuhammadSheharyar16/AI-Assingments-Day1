"""
Day 11 Task 14 -- observability / provenance metadata (`GateC.evaluate()`,
`src/aico/control/gate_c.py`).

Proves the required sanitized fields are captured and the required spans
exist, against a real `GateC` (Task 9) built from the real committed
`SourceRegistry`/`GateCPolicyRegistry`, using the exact same in-memory OTel
exporter `test_day09/10_observability.py` already use
(`aico.observability.telemetry.get_finished_spans`/`clear_finished_spans`):

  - a `"gate_c"` span exists for every `evaluate()` call, carrying (at
    minimum) `policy_version`, `source_registry_version`,
    `candidate_evidence_count`, `validated_evidence_count`,
    `rejected_evidence_count`, `missing_facet_count`, `conflict_count`,
    `freshness_result`, `decision`, `reason_codes` and a measured
    `latency_ms` -- Task 14's own named field list, mapped one-for-one
    (`request_id`/`correlation_id` are carried by the parent-span
    mechanism proven below, not set as attributes here; `ontology_version`/
    `gate_b_policy_version` are not `GateC`'s own to set -- both already
    appear on the sibling `"gate_a"`/`"gate_b"` spans a real caller opens
    in the same trace, per `gate_c.py`'s own "Day 11 Task 14" docstring
    section);
  - `"provenance_validation"`/`"freshness_validation"` child spans exist
    for every call that reaches stage 3 (i.e. every call past Gate-B/
    request_kind/governed-rule resolution), each nested under the same
    `"gate_c"` span and sharing its `trace_id`;
  - a `"completeness_validation"` child span exists only for calls that
    reach stage 7 (`allow` or an `insufficient_evidence` reached via
    missing facets) -- absent for `reject`/`clarify`/an `insufficient_
    evidence` reached via `minimum_valid_items` instead, which return
    before stage 7 ever runs;
  - `gate_c`/its child spans correctly nest under a caller's own span and
    share its `trace_id` (Day 6's correlation-propagation mechanism,
    reused unmodified, exactly as `test_day09/10_observability.py` already
    prove for `gate_a`/`gate_b`);
  - no span attribute anywhere ever contains raw evidence content, a raw
    protected record, a claimed value, or the question text -- only
    governed ids/labels, sanitized reason-code strings, and counts.
"""
from __future__ import annotations

import pytest
from opentelemetry import trace

from aico.control.gate_c import GateC, GateCRequest
from aico.control.models import GateBDecision, GateBStatus
from aico.control.ontology import LaneId
from aico.control.policy_models import DataClassification
from aico.evidence.models import EvidencePackage
from aico.evidence.policy import GateCPolicyRegistry
from aico.evidence.provenance import stable_content_hash
from aico.evidence.source_registry import SourceRegistry
from aico.observability.telemetry import clear_finished_spans, configure_tracing, get_finished_spans

_tracer = trace.get_tracer(__name__)


@pytest.fixture(scope="module", autouse=True)
def _tracing_configured():
    configure_tracing()


@pytest.fixture(scope="module")
def source_registry() -> SourceRegistry:
    return SourceRegistry.load()


@pytest.fixture(scope="module")
def policy_registry(source_registry: SourceRegistry) -> GateCPolicyRegistry:
    return GateCPolicyRegistry.load(source_registry=source_registry)


@pytest.fixture
def gate_c(source_registry: SourceRegistry, policy_registry: GateCPolicyRegistry) -> GateC:
    return GateC(source_registry=source_registry, policy_registry=policy_registry)


def _allow_decision(*, tenant_ids=("TENANT-A",), data_classes=(DataClassification.INTERNAL,)) -> GateBDecision:
    return GateBDecision(
        decision=GateBStatus.ALLOW,
        effective_tenant_scope=tenant_ids,
        effective_data_classes=data_classes,
        reason_code="test_fixture",
        policy_version="1.0",
        lane=LaneId.RAG,
    )


_SECRET_CLAIM_VALUE = "net 30, do not disclose to auditors"


def _item(evidence_id: str, *, source_id: str, facets: list[str], claims: dict[str, str] | None = None, **overrides) -> dict:
    content = overrides.pop("content", f"Raw evidence content for {evidence_id}, never logged.")
    data = {
        "evidence_id": evidence_id,
        "chunk_id": f"CH-{evidence_id}",
        "source_id": source_id,
        "source_version": "3",
        "source_updated_at": "2026-09-05T09:00:00+05:00",
        "retrieved_at": "2026-09-11T11:59:00+05:00",
        "content_hash": stable_content_hash(content),
        "tenant_id": "TENANT-A",
        "data_classification": "internal",
        "evidence_facets": facets,
        "content": content,
        "claims": claims or {},
    }
    data.update(overrides)
    return data


def _package(items: list[dict], **overrides) -> EvidencePackage:
    data = {
        "request_id": "REQ-001",
        "intent_id": "INT-POLICY-QUESTION",
        "lane": "rag",
        "as_of": "2026-09-11T12:00:00+05:00",
        "required_facets": [],
        "items": items,
    }
    data.update(overrides)
    return EvidencePackage.model_validate(data)


# ---------------------------------------------------------------------------
# "gate_c" span exists with the required sanitized fields
# ---------------------------------------------------------------------------


def test_gate_c_span_exists_with_required_fields_on_allow(gate_c, source_registry, policy_registry):
    clear_finished_spans()
    package = _package([_item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"])])

    gate_c.evaluate(gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only"))

    spans = {s.name: s for s in get_finished_spans()}
    assert "gate_c" in spans
    gate_c_span = spans["gate_c"]
    for field_name in (
        "policy_version", "source_registry_version", "candidate_evidence_count", "validated_evidence_count",
        "rejected_evidence_count", "missing_facet_count", "conflict_count", "freshness_result", "decision",
        "reason_codes", "latency_ms",
    ):
        assert f"gate_c.{field_name}" in gate_c_span.attributes

    assert gate_c_span.attributes["gate_c.policy_version"] == policy_registry.policy_version
    assert gate_c_span.attributes["gate_c.source_registry_version"] == source_registry.registry_version
    assert gate_c_span.attributes["gate_c.decision"] == "allow"
    assert gate_c_span.attributes["gate_c.candidate_evidence_count"] == 1
    assert gate_c_span.attributes["gate_c.validated_evidence_count"] == 1
    assert gate_c_span.attributes["gate_c.rejected_evidence_count"] == 0
    assert gate_c_span.attributes["gate_c.missing_facet_count"] == 0
    assert gate_c_span.attributes["gate_c.conflict_count"] == 0
    assert gate_c_span.attributes["gate_c.freshness_result"] == "fresh=1;stale=0;other=0"
    assert isinstance(gate_c_span.attributes["gate_c.latency_ms"], float)


def test_gate_c_span_reflects_reject(gate_c):
    clear_finished_spans()
    package = _package([_item("E-A", source_id="SRC-DOES-NOT-EXIST", facets=["payment_terms"])])

    gate_c.evaluate(gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only"))

    span = {s.name: s for s in get_finished_spans()}["gate_c"]
    assert span.attributes["gate_c.decision"] == "reject"
    assert "unknown_source" in span.attributes["gate_c.reason_codes"]
    assert span.attributes["gate_c.validated_evidence_count"] == 0
    assert span.attributes["gate_c.rejected_evidence_count"] == 1


def test_gate_c_span_reflects_insufficient_evidence(gate_c):
    clear_finished_spans()
    package = _package([_item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity"])])

    gate_c.evaluate(gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_and_invoice"))

    span = {s.name: s for s in get_finished_spans()}["gate_c"]
    assert span.attributes["gate_c.decision"] == "insufficient_evidence"
    assert span.attributes["gate_c.missing_facet_count"] == 2  # payment_terms, invoice_window


def test_gate_c_span_reflects_clarify(gate_c):
    clear_finished_spans()
    package = _package([_item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"])])

    gate_c.evaluate(gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind=None))

    span = {s.name: s for s in get_finished_spans()}["gate_c"]
    assert span.attributes["gate_c.decision"] == "clarify"
    assert "request_kind_not_resolved" in span.attributes["gate_c.reason_codes"]
    assert span.attributes["gate_c.candidate_evidence_count"] == 1  # package was already known


def test_gate_c_span_reflects_unresolved_conflict(gate_c):
    clear_finished_spans()
    package = _package(
        [
            _item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"], claims={"payment_terms": "net 30"}),
            _item("E-B", source_id="SRC-POLICY-A", facets=["payment_terms"], claims={"payment_terms": "net 45"}),
        ]
    )

    gate_c.evaluate(gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only"))

    span = {s.name: s for s in get_finished_spans()}["gate_c"]
    assert span.attributes["gate_c.decision"] == "reject"
    assert span.attributes["gate_c.conflict_count"] == 1
    assert "unresolved_conflict" in span.attributes["gate_c.reason_codes"]


# ---------------------------------------------------------------------------
# provenance_validation / freshness_validation child spans
# ---------------------------------------------------------------------------


def test_provenance_and_freshness_validation_spans_exist_and_nest_under_gate_c(gate_c):
    clear_finished_spans()
    package = _package([_item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"])])

    gate_c.evaluate(gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only"))

    spans = {s.name: s for s in get_finished_spans()}
    gate_c_span = spans["gate_c"]
    for name in ("provenance_validation", "freshness_validation"):
        assert name in spans
        child = spans[name]
        assert child.context.trace_id == gate_c_span.context.trace_id
        assert child.parent is not None
        assert child.parent.span_id == gate_c_span.context.span_id

    assert spans["provenance_validation"].attributes["provenance_validation.candidate_count"] == 1
    assert spans["provenance_validation"].attributes["provenance_validation.failed_count"] == 0
    assert spans["provenance_validation"].attributes["provenance_validation.integrity_checked"] is False
    assert spans["freshness_validation"].attributes["freshness_validation.result"] == "fresh=1;stale=0;other=0"


def test_provenance_validation_span_reflects_a_failed_item(gate_c):
    clear_finished_spans()
    package = _package([_item("E-A", source_id="SRC-DOES-NOT-EXIST", facets=["payment_terms"])])

    gate_c.evaluate(gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only"))

    span = {s.name: s for s in get_finished_spans()}["provenance_validation"]
    assert span.attributes["provenance_validation.failed_count"] == 1


def test_provenance_and_freshness_validation_spans_absent_when_gate_b_did_not_allow(gate_c):
    """Stage 0 returns before stage 3 ever runs -- no candidate item is
    ever examined at all."""
    clear_finished_spans()
    package = _package([_item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"])])
    deny_decision = GateBDecision(decision=GateBStatus.DENY, reason_code="test_fixture", policy_version="1.0")

    gate_c.evaluate(gate_b_decision=deny_decision, package=package, request=GateCRequest(request_kind="payment_terms_only"))

    span_names = {s.name for s in get_finished_spans()}
    assert "gate_c" in span_names
    assert "provenance_validation" not in span_names
    assert "freshness_validation" not in span_names


# ---------------------------------------------------------------------------
# completeness_validation child span: only when stage 7 is reached
# ---------------------------------------------------------------------------


def test_completeness_validation_span_exists_on_allow(gate_c):
    clear_finished_spans()
    package = _package([_item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"])])

    gate_c.evaluate(gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only"))

    spans = {s.name: s for s in get_finished_spans()}
    assert "completeness_validation" in spans
    completeness_span = spans["completeness_validation"]
    gate_c_span = spans["gate_c"]
    assert completeness_span.parent.span_id == gate_c_span.context.span_id
    assert completeness_span.attributes["completeness_validation.required_facet_count"] == 2
    assert completeness_span.attributes["completeness_validation.covered_facet_count"] == 2
    assert completeness_span.attributes["completeness_validation.missing_facet_count"] == 0


def test_completeness_validation_span_absent_on_reject(gate_c):
    clear_finished_spans()
    package = _package([_item("E-A", source_id="SRC-DOES-NOT-EXIST", facets=["payment_terms"])])

    gate_c.evaluate(gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only"))

    assert "completeness_validation" not in {s.name for s in get_finished_spans()}


def test_completeness_validation_span_absent_on_clarify(gate_c):
    clear_finished_spans()
    package = _package([_item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"])])

    gate_c.evaluate(gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind=None))

    assert "completeness_validation" not in {s.name for s in get_finished_spans()}


def test_completeness_validation_span_absent_when_below_minimum_valid_items(source_registry):
    """`insufficient_evidence` reached via Task 3's own `minimum_valid_items`
    floor (stage 6) also returns before stage 7 -- a throwaway policy is
    needed since every real committed rule's `minimum_valid_items` is 1."""
    from aico.evidence.policy import GateCPolicyDocument

    throwaway_policy_registry = GateCPolicyRegistry(
        GateCPolicyDocument.model_validate(
            {
                "policy_version": "throwaway",
                "status": "active",
                "freshness_policies": [{"policy_id": "FRESH-POLICY-30D", "max_age_hours": 720}],
                "intent_requirements": [
                    {
                        "rule_id": "GC-THROWAWAY",
                        "intent_id": "INT-POLICY-QUESTION",
                        "request_kind": "payment_terms_only",
                        "required_facets": ["payment_terms"],
                        "minimum_valid_items": 2,
                        "allowed_source_types": ["policy_document"],
                        "conflict_policy": "authority_then_reject_tie",
                    }
                ],
            }
        )
    )
    throwaway_gate_c = GateC(source_registry=source_registry, policy_registry=throwaway_policy_registry)
    clear_finished_spans()
    package = _package([_item("E-A", source_id="SRC-POLICY-A", facets=["payment_terms"])])

    throwaway_gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only")
    )

    span = {s.name: s for s in get_finished_spans()}["gate_c"]
    assert span.attributes["gate_c.decision"] == "insufficient_evidence"
    assert "completeness_validation" not in {s.name for s in get_finished_spans()}


# ---------------------------------------------------------------------------
# Correlation context: nests under a caller's own span, shares trace_id
# ---------------------------------------------------------------------------


def test_gate_c_shares_trace_id_with_a_parent_span(gate_c):
    clear_finished_spans()
    package = _package([_item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"])])

    with _tracer.start_as_current_span("test.caller"):
        gate_c.evaluate(gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only"))

    spans = {s.name: s for s in get_finished_spans()}
    root_span = spans["test.caller"]
    gate_c_span = spans["gate_c"]

    assert gate_c_span.context.trace_id == root_span.context.trace_id
    assert gate_c_span.parent is not None
    assert gate_c_span.parent.span_id == root_span.context.span_id

    # Every span this call produced shares the identical trace_id.
    for span in get_finished_spans():
        assert span.context.trace_id == root_span.context.trace_id


# ---------------------------------------------------------------------------
# Sanitization: never raw evidence content, a claimed value, or the
# question text
# ---------------------------------------------------------------------------


def test_no_span_attribute_ever_contains_raw_evidence_content_or_claims(gate_c):
    clear_finished_spans()
    package = _package(
        [_item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"], claims={"payment_terms": _SECRET_CLAIM_VALUE})]
    )

    gate_c.evaluate(gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only"))

    all_attribute_values = {str(v) for s in get_finished_spans() for v in s.attributes.values()}
    assert not any("Raw evidence content" in v for v in all_attribute_values)
    assert _SECRET_CLAIM_VALUE not in all_attribute_values
    assert not any(_SECRET_CLAIM_VALUE in v for v in all_attribute_values)
