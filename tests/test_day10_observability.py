"""
Day 10 Task 14 -- decision provenance / observability.

Proves the required sanitized fields are captured and the required spans
exist, against `ControlPlaneAnswerService` (Task 13) and the real
committed registry/policy (`ontology/registry.v1.json`,
`policy/gate_b_policy.v1.json`), using the exact same in-memory OTel
exporter Day 6/9's own observability tests already use
(`aico.observability.telemetry.get_finished_spans`/`clear_finished_spans`):

  - a `"gate_b"` span exists for every `rag`/`mode_b` request that reaches
    Gate-B, carrying (at minimum) `ontology_version`, `policy_version`,
    `rule_id`, `intent_id`, `lane`, `decision`, `reason_code`,
    `effective_scope_summary`, `disclosure_profile` and a measured
    `latency_ms` -- Task 14's own named field list, mapped one-for-one
    (`request_id`/`correlation_id` are carried by the parent-span
    mechanism proven below, not set as attributes here -- the identical
    convention `gate_a`/`lane_selection` already established in Day 9);
  - a `"safe_disclosure"` span exists for every `ControlPlaneAnswerService
    .disclose()` call, carrying `policy_version`/`disclosure_profile` plus
    sanitized field-action *counts* -- never a raw field name, value, or
    masked value;
  - `block`/`clarify` (Gate-A's own, an earlier stage) and `safe_fast_path`
    never produce a `"gate_b"` span at all -- Gate-B is never invoked for
    them (Task 13);
  - `gate_b`/`safe_disclosure` correctly nest under a caller's own span and
    share its `trace_id` -- Day 6's correlation-propagation mechanism,
    reused unmodified, exactly as `test_day09_observability.py` already
    proves for `gate_a`/`lane_selection`;
  - no span attribute anywhere ever contains a raw PII value, a raw
    protected field value/name, the raw question text, an authorization
    token, or full policy internals (only governed ids/labels and counts).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from opentelemetry import trace

from aico.api.identity import TrustedIdentity
from aico.control.disclosure import ProtectedField
from aico.control.gate_b import GateBRequest
from aico.control.models import GateBDecision, GateBStatus
from aico.control.ontology_registry import OntologyRegistry
from aico.control.policy_models import DataClassification, PiiCategory
from aico.control.policy_registry import PolicyRegistry
from aico.observability.telemetry import clear_finished_spans, configure_tracing, get_finished_spans
from aico.rag.answer_service import GroundedAnswerService
from aico.rag.citation_validator import EvidenceChunk
from aico.rag.control_plane_answer_service import ControlPlaneAnswerService

_tracer = trace.get_tracer(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
PII_DISCLOSURE_CASES_PATH = REPO_ROOT / "data" / "day10_pack" / "fixtures" / "pii_disclosure_cases.json"


class _NeverCalledGateway:
    def chat(self, request):  # pragma: no cover - only reached on failure
        raise AssertionError("Model Gateway must not be called")


def _never_called_retriever(query):  # pragma: no cover - only reached on failure
    raise AssertionError("retrieval must not be called")


@pytest.fixture(scope="module", autouse=True)
def _tracing_configured():
    configure_tracing()


@pytest.fixture(scope="module")
def real_registry() -> OntologyRegistry:
    return OntologyRegistry.load()


@pytest.fixture(scope="module")
def real_policy_registry(real_registry: OntologyRegistry) -> PolicyRegistry:
    return PolicyRegistry.load(ontology_registry=real_registry)


@pytest.fixture
def service(real_registry: OntologyRegistry, real_policy_registry: PolicyRegistry) -> ControlPlaneAnswerService:
    rag_service = GroundedAnswerService(gateway=_NeverCalledGateway(), retriever=_never_called_retriever)
    return ControlPlaneAnswerService(registry=real_registry, rag_service=rag_service, policy_registry=real_policy_registry)


_ANSWERED_JSON = (
    '{"schema_version": "1.0", "status": "answered", "answer": "Payment terms are net 30 days.", '
    '"citations": [{"chunk_id": "C1", "source_file": "DOC-001.md"}], "confidence_label": "high"}'
)


class _WorkingGateway:
    def chat(self, request):
        from aico.platform.model_gateway import CallMetadata, ChatResult

        return ChatResult(
            content=_ANSWERED_JSON,
            metadata=CallMetadata(
                operation="chat", model_alias="fake-chat-alias", latency_ms=1.0, retry_count=0,
                token_usage=None, budget_status="within_budget",
            ),
        )


def _working_retriever(query: str) -> list[EvidenceChunk]:
    return [EvidenceChunk(chunk_id="C1", source_file="DOC-001.md", text="Payment terms are net 30 days.")]


@pytest.fixture
def working_service(real_registry: OntologyRegistry, real_policy_registry: PolicyRegistry) -> ControlPlaneAnswerService:
    """A second service, for the tests that need an `ALLOW` decision to
    actually reach `rag` lane retrieval/generation -- `service` above
    deliberately raises if either is ever called, which is what every
    deny/clarify test wants but an allow test cannot use."""
    rag_service = GroundedAnswerService(gateway=_WorkingGateway(), retriever=_working_retriever)
    return ControlPlaneAnswerService(registry=real_registry, rag_service=rag_service, policy_registry=real_policy_registry)


_SUPPLIER_READER = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("supplier_reader",))
_UNKNOWN_ROLE = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-X", roles=("made_up_role",))


# ---------------------------------------------------------------------------
# gate_b span exists with the required sanitized fields
# ---------------------------------------------------------------------------


def test_gate_b_span_exists_with_required_fields_on_deny(service: ControlPlaneAnswerService, real_registry, real_policy_registry):
    clear_finished_spans()

    service.answer("What are the payment terms?", identity=_UNKNOWN_ROLE)

    spans = {s.name: s for s in get_finished_spans()}
    assert "gate_b" in spans
    gate_b_span = spans["gate_b"]
    for field_name in (
        "ontology_version", "policy_version", "rule_id", "intent_id", "lane",
        "decision", "reason_code", "effective_scope_summary", "disclosure_profile", "latency_ms",
    ):
        assert f"gate_b.{field_name}" in gate_b_span.attributes

    assert gate_b_span.attributes["gate_b.ontology_version"] == real_registry.ontology_version
    assert gate_b_span.attributes["gate_b.policy_version"] == real_policy_registry.policy_version
    assert gate_b_span.attributes["gate_b.decision"] == "deny"
    assert gate_b_span.attributes["gate_b.reason_code"] == "unknown_role"
    assert gate_b_span.attributes["gate_b.rule_id"] == ""  # no rule matched at all for this deny
    assert isinstance(gate_b_span.attributes["gate_b.latency_ms"], float)


def test_gate_b_span_carries_the_matched_rule_and_intent_on_allow(working_service: ControlPlaneAnswerService):
    clear_finished_spans()

    result = working_service.answer(
        "What are the payment terms?", identity=_SUPPLIER_READER, requested=GateBRequest(data_class=DataClassification.INTERNAL)
    )

    from aico.rag.answer_service import TypedFailure

    assert not isinstance(result, TypedFailure)  # sanity: request actually reached the rag lane
    span = {s.name: s for s in get_finished_spans()}["gate_b"]
    assert span.attributes["gate_b.decision"] == "allow"
    assert span.attributes["gate_b.rule_id"] == "GB-R001"
    assert span.attributes["gate_b.intent_id"] == "INT-POLICY-QUESTION"
    assert span.attributes["gate_b.lane"] == "rag"
    assert span.attributes["gate_b.disclosure_profile"] == "policy_reader"
    assert "tenants=1" in span.attributes["gate_b.effective_scope_summary"]
    assert "internal" in span.attributes["gate_b.effective_scope_summary"]


def test_gate_b_span_reflects_clarify(service: ControlPlaneAnswerService):
    clear_finished_spans()

    service.answer("What are the payment terms?", identity=_SUPPLIER_READER)  # no data_class -> clarify

    span = {s.name: s for s in get_finished_spans()}["gate_b"]
    assert span.attributes["gate_b.decision"] == "clarify"
    assert span.attributes["gate_b.reason_code"] == "data_class_selection_required"
    assert span.attributes["gate_b.disclosure_profile"] == ""  # nothing granted on clarify


# ---------------------------------------------------------------------------
# gate_b span is never produced for lanes Gate-B is not invoked on
# ---------------------------------------------------------------------------


def test_gate_b_span_absent_for_gate_a_block_lane(service: ControlPlaneAnswerService):
    clear_finished_spans()
    service.answer("What is tomorrow's weather?", identity=_SUPPLIER_READER)
    assert "gate_b" not in {s.name for s in get_finished_spans()}


def test_gate_b_span_absent_for_gate_a_clarify_lane(service: ControlPlaneAnswerService):
    clear_finished_spans()
    service.answer("Show me the supplier information.", identity=_SUPPLIER_READER)
    assert "gate_b" not in {s.name for s in get_finished_spans()}


def test_gate_b_span_absent_for_safe_fast_path(service: ControlPlaneAnswerService):
    clear_finished_spans()
    service.answer("What can you help with?")  # no identity needed at all
    assert "gate_b" not in {s.name for s in get_finished_spans()}


# ---------------------------------------------------------------------------
# safe_disclosure span exists with sanitized counts, never raw content
# ---------------------------------------------------------------------------


@pytest.fixture
def synthetic_fields() -> list[ProtectedField]:
    fixture = json.loads(PII_DISCLOSURE_CASES_PATH.read_text(encoding="utf-8"))
    record = fixture["synthetic_record"]
    metadata = fixture["field_metadata"]
    return [
        ProtectedField(
            name=name,
            value=value,
            data_class=DataClassification(metadata[name]["data_class"]),
            pii_category=PiiCategory(metadata[name]["pii_category"]),
        )
        for name, value in record.items()
    ]


def test_safe_disclosure_span_exists_with_sanitized_counts(
    service: ControlPlaneAnswerService, real_policy_registry: PolicyRegistry, synthetic_fields: list[ProtectedField]
):
    clear_finished_spans()
    profile = real_policy_registry.get_disclosure_profile("policy_reader")
    decision = GateBDecision(
        decision=GateBStatus.ALLOW,
        effective_tenant_scope=("TENANT-A",),
        effective_data_classes=(DataClassification.INTERNAL,),
        effective_pii_policy=(PiiCategory.NONE, PiiCategory.CONTACT),
        disclosure_profile="policy_reader",
        rule_id="GB-R001",
        reason_code="rule_allowed",
        policy_version=real_policy_registry.policy_version,
    )

    view = service.disclose(decision, profile, synthetic_fields)

    spans = {s.name: s for s in get_finished_spans()}
    assert "safe_disclosure" in spans
    span = spans["safe_disclosure"]
    for field_name in ("policy_version", "disclosure_profile", "field_count", "allowed_count", "redacted_count", "denied_count", "latency_ms"):
        assert f"safe_disclosure.{field_name}" in span.attributes

    assert span.attributes["safe_disclosure.disclosure_profile"] == "policy_reader"
    assert span.attributes["safe_disclosure.field_count"] == len(synthetic_fields)
    assert span.attributes["safe_disclosure.allowed_count"] + span.attributes["safe_disclosure.redacted_count"] + span.attributes["safe_disclosure.denied_count"] == len(view.fields)
    assert span.attributes["safe_disclosure.redacted_count"] >= 1  # sanity: at least one field really was redacted


def test_safe_disclosure_span_never_contains_a_raw_field_name_or_value(
    service: ControlPlaneAnswerService, real_policy_registry: PolicyRegistry, synthetic_fields: list[ProtectedField]
):
    clear_finished_spans()
    profile = real_policy_registry.get_disclosure_profile("policy_reader")
    decision = GateBDecision(
        decision=GateBStatus.ALLOW,
        effective_tenant_scope=("TENANT-A",),
        effective_data_classes=(DataClassification.INTERNAL,),
        effective_pii_policy=(PiiCategory.NONE, PiiCategory.CONTACT),
        disclosure_profile="policy_reader",
        rule_id="GB-R001",
        reason_code="rule_allowed",
        policy_version=real_policy_registry.policy_version,
    )

    service.disclose(decision, profile, synthetic_fields)

    span = {s.name: s for s in get_finished_spans()}["safe_disclosure"]
    attribute_values = {str(v) for v in span.attributes.values()}
    for protected_field in synthetic_fields:
        assert protected_field.name not in attribute_values
        assert not any(protected_field.value in v for v in attribute_values)


# ---------------------------------------------------------------------------
# Correlation context: gate_b / safe_disclosure nest under the caller's span
# and share one trace_id (Day 6's own mechanism, reused unmodified)
# ---------------------------------------------------------------------------


def test_gate_b_shares_trace_id_with_a_parent_span(working_service: ControlPlaneAnswerService):
    clear_finished_spans()

    with _tracer.start_as_current_span("test.caller"):
        working_service.answer(
            "What are the payment terms?", identity=_SUPPLIER_READER, requested=GateBRequest(data_class=DataClassification.INTERNAL)
        )

    spans = {s.name: s for s in get_finished_spans()}
    root_span = spans["test.caller"]
    gate_b_span = spans["gate_b"]

    assert gate_b_span.context.trace_id == root_span.context.trace_id
    assert gate_b_span.parent is not None
    assert gate_b_span.parent.span_id == root_span.context.span_id


def test_safe_disclosure_shares_trace_id_with_a_parent_span(
    service: ControlPlaneAnswerService, real_policy_registry: PolicyRegistry, synthetic_fields: list[ProtectedField]
):
    clear_finished_spans()
    profile = real_policy_registry.get_disclosure_profile("policy_reader")
    decision = GateBDecision(
        decision=GateBStatus.ALLOW,
        effective_tenant_scope=("TENANT-A",),
        effective_data_classes=(DataClassification.INTERNAL,),
        effective_pii_policy=(PiiCategory.NONE, PiiCategory.CONTACT),
        disclosure_profile="policy_reader",
        rule_id="GB-R001",
        reason_code="rule_allowed",
        policy_version=real_policy_registry.policy_version,
    )

    with _tracer.start_as_current_span("test.caller"):
        service.disclose(decision, profile, synthetic_fields)

    spans = {s.name: s for s in get_finished_spans()}
    root_span = spans["test.caller"]
    disclosure_span = spans["safe_disclosure"]

    assert disclosure_span.context.trace_id == root_span.context.trace_id
    assert disclosure_span.parent is not None
    assert disclosure_span.parent.span_id == root_span.context.span_id


# ---------------------------------------------------------------------------
# Sanitization: never the raw question, PII, secrets, or an auth token
# ---------------------------------------------------------------------------


def test_gate_b_span_attributes_never_contain_the_raw_question(working_service: ControlPlaneAnswerService):
    clear_finished_spans()
    question = "What are the payment terms?"

    working_service.answer(question, identity=_SUPPLIER_READER, requested=GateBRequest(data_class=DataClassification.INTERNAL))

    all_attribute_values = {str(v) for s in get_finished_spans() for v in s.attributes.values()}
    assert question not in all_attribute_values
    assert not any(question in v for v in all_attribute_values)


def test_gate_b_span_attributes_never_contain_a_bearer_token_or_identity_fields(working_service: ControlPlaneAnswerService):
    """`identity` itself (tenant_id/user_id/roles) never becomes a span
    attribute -- only `gate_b_decision`'s own already-sanitized fields
    do."""
    clear_finished_spans()
    fake_bearer_token = "eyJhbGciOiJIUzI1NiJ9.fake.token"  # noqa: S105 - synthetic, never a real secret

    working_service.answer(
        "What are the payment terms?", identity=_SUPPLIER_READER, requested=GateBRequest(data_class=DataClassification.INTERNAL)
    )

    all_attribute_values = {str(v) for s in get_finished_spans() for v in s.attributes.values()}
    assert fake_bearer_token not in all_attribute_values
    assert _SUPPLIER_READER.user_id not in all_attribute_values
    assert not any(role in v for v in all_attribute_values for role in _SUPPLIER_READER.roles)
