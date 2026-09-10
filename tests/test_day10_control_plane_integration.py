"""
Day 10 Task 13 -- integrating Gate-B into Day 9 routing
(`ControlPlaneAnswerService`, `src/aico/rag/control_plane_answer_service.py`).

Proves the full required order end to end, against the real committed
ontology registry (`ontology/registry.v1.json`), the real committed
Gate-B policy (`policy/gate_b_policy.v1.json`), a real `GateA`/
`LaneSelector`/`GateB` (Day 9 Tasks 3/5, Day 10 Tasks 3-12), and a
`GroundedAnswerService` wired to fakes (same pattern
`test_day09_control_plane_integration.py` already uses) so retrieval/
Model-Gateway calls are directly observable:

    trusted identity -> session -> input policy -> Gate-A -> lane selector
    -> Gate-B -> only then protected lane behavior

  - Gate-B `deny` (missing identity, unknown role) -> zero retrieval/model
    calls, a typed `GateBDenied`, never a `decision="deny"` returned after
    retrieval already ran (Task 11's own guarantee, proven again here at
    the service-integration level, not only against `GateB` directly as
    `test_day10_no_fallthrough.py` already does).
  - Gate-B `clarify` (Task 10's one safe case: a matched rule allows more
    than one governed data classification and none was requested) -> zero
    retrieval/model calls, a typed `GateBAuthorizationClarify`.
  - Gate-B `allow` -> for `rag`, retrieval/the Model Gateway ARE reached,
    exactly once each (the sanity check a raise-only fake alone cannot
    give); for `mode_b`, `ModeBSelected` is returned -- selected, still
    never executed (no database import anywhere in this module, Day 10
    working rule: "Do not implement an uncontrolled Mode-B query
    executor").
  - `block`/`clarify` (Gate-A's own, an earlier stage) and `safe_fast_path`
    never invoke Gate-B at all -- proven by a service whose `GateB` would
    raise if `authorize()` were ever called for those lanes, and by
    confirming those lanes need no `identity` at all to still work.
  - A `ControlPlaneAnswerService` built WITHOUT `policy_registry` behaves
    exactly as Day 9 left it (Gate-B fully inert) -- the regression
    `test_day09_control_plane_integration.py`/friends already prove this
    directly; this file adds one direct side-by-side confirmation.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

import pytest

from aico.api.identity import TrustedIdentity
from aico.control.gate_b import GateBRequest
from aico.control.ontology_registry import OntologyRegistry
from aico.control.policy_models import DataClassification
from aico.control.policy_registry import PolicyRegistry
from aico.platform.model_gateway import CallMetadata, ChatRequest, ChatResult
from aico.rag.answer_service import GroundedAnswer, GroundedAnswerService
from aico.rag.citation_validator import EvidenceChunk
from aico.rag.control_plane_answer_service import (
    ControlPlaneAnswerService,
    GateBAuthorizationClarify,
    GateBDenied,
    GateBlocked,
    GateClarify,
    ModeBSelected,
    SafeFastPathAnswer,
)


class _NeverCalledGateway:
    def chat(self, request: ChatRequest) -> ChatResult:  # pragma: no cover - only reached on failure
        raise AssertionError(f"Model Gateway must not be called (request: {request!r})")


def _never_called_retriever(query: str) -> list[EvidenceChunk]:  # pragma: no cover - only reached on failure
    raise AssertionError(f"retrieval must not be called (query: {query!r})")


@dataclass
class _CountingGateway:
    response_content: str
    calls: list[ChatRequest] = field(default_factory=list)

    def chat(self, request: ChatRequest) -> ChatResult:
        self.calls.append(request)
        return ChatResult(
            content=self.response_content,
            metadata=CallMetadata(
                operation="chat", model_alias="fake-chat-alias", latency_ms=1.0, retry_count=0,
                token_usage=None, budget_status="within_budget",
            ),
        )


@dataclass
class _CountingRetriever:
    chunks: list[EvidenceChunk] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    def __call__(self, query: str) -> list[EvidenceChunk]:
        self.calls.append(query)
        return self.chunks


_ANSWERED_JSON = (
    '{"schema_version": "1.0", "status": "answered", "answer": "Payment terms are net 30 days.", '
    '"citations": [{"chunk_id": "C1", "source_file": "DOC-001.md"}], "confidence_label": "high"}'
)


@pytest.fixture(scope="module")
def real_registry() -> OntologyRegistry:
    return OntologyRegistry.load()


@pytest.fixture(scope="module")
def real_policy_registry(real_registry: OntologyRegistry) -> PolicyRegistry:
    return PolicyRegistry.load(ontology_registry=real_registry)


def _governed_service_that_must_not_reach_retrieval_or_gateway(
    registry: OntologyRegistry, policy_registry: PolicyRegistry
) -> ControlPlaneAnswerService:
    rag_service = GroundedAnswerService(gateway=_NeverCalledGateway(), retriever=_never_called_retriever)
    return ControlPlaneAnswerService(registry=registry, rag_service=rag_service, policy_registry=policy_registry)


def _governed_service_with_counting_fakes(
    registry: OntologyRegistry, policy_registry: PolicyRegistry
) -> tuple[ControlPlaneAnswerService, _CountingGateway, _CountingRetriever]:
    gateway = _CountingGateway(_ANSWERED_JSON)
    retriever = _CountingRetriever(
        chunks=[EvidenceChunk(chunk_id="C1", source_file="DOC-001.md", text="Payment terms are net 30 days.")]
    )
    rag_service = GroundedAnswerService(gateway=gateway, retriever=retriever)
    service = ControlPlaneAnswerService(registry=registry, rag_service=rag_service, policy_registry=policy_registry)
    return service, gateway, retriever


_SUPPLIER_READER = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("supplier_reader",))
_SOURCING_ANALYST = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-2", roles=("sourcing_analyst",))
_UNKNOWN_ROLE = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-X", roles=("made_up_role",))


# ---------------------------------------------------------------------------
# Gate-B deny -> zero retrieval/model calls
# ---------------------------------------------------------------------------


def test_missing_identity_denies_with_zero_protected_calls(real_registry, real_policy_registry):
    service = _governed_service_that_must_not_reach_retrieval_or_gateway(real_registry, real_policy_registry)

    result = service.answer("What are the payment terms?")  # no identity supplied

    assert isinstance(result, GateBDenied)
    assert result.reason_code == "identity_missing"
    assert result.policy_version == real_policy_registry.policy_version


def test_unknown_role_denies_with_zero_protected_calls(real_registry, real_policy_registry):
    service = _governed_service_that_must_not_reach_retrieval_or_gateway(real_registry, real_policy_registry)

    result = service.answer("What are the payment terms?", identity=_UNKNOWN_ROLE)

    assert isinstance(result, GateBDenied)
    assert result.reason_code == "unknown_role"


def test_disallowed_role_for_mode_b_denies_with_zero_protected_calls(real_registry, real_policy_registry):
    """`supplier_reader` matches `GB-R002` for structured lookup, but that
    rule's own `allowed` is `False` -- still zero calls, no database
    reached, not "a rule was found so proceed."""
    service = _governed_service_that_must_not_reach_retrieval_or_gateway(real_registry, real_policy_registry)

    result = service.answer("List active contracts.", identity=_SUPPLIER_READER)

    assert isinstance(result, GateBDenied)
    assert result.reason_code == "rule_denied"
    assert result.rule_id == "GB-R002"


# ---------------------------------------------------------------------------
# Gate-B clarify -> zero retrieval/model calls
# ---------------------------------------------------------------------------


def test_ambiguous_data_class_clarifies_with_zero_protected_calls(real_registry, real_policy_registry):
    """`GB-R001` allows `[public, internal]` -- asking with no
    `requested.data_class` is genuinely ambiguous and safely clarifiable
    (Task 10), never a role/tenant/permission question."""
    service = _governed_service_that_must_not_reach_retrieval_or_gateway(real_registry, real_policy_registry)

    result = service.answer("What are the payment terms?", identity=_SUPPLIER_READER)

    assert isinstance(result, GateBAuthorizationClarify)
    assert result.reason_code == "data_class_selection_required"
    assert result.rule_id == "GB-R001"


# ---------------------------------------------------------------------------
# Gate-B allow -> rag reaches retrieval/model exactly once each
# ---------------------------------------------------------------------------


def test_allowed_rag_request_reaches_retrieval_and_model_exactly_once(real_registry, real_policy_registry):
    service, gateway, retriever = _governed_service_with_counting_fakes(real_registry, real_policy_registry)

    result = service.answer(
        "What are the payment terms?",
        identity=_SUPPLIER_READER,
        requested=GateBRequest(data_class=DataClassification.INTERNAL),
    )

    assert isinstance(result, GroundedAnswer)
    assert result.answer == "Payment terms are net 30 days."
    assert len(gateway.calls) == 1
    assert len(retriever.calls) == 1


def test_denied_then_allowed_request_shows_calls_only_happen_on_allow(real_registry, real_policy_registry):
    """The same service, same fakes: a denied request first (0 calls),
    then an allowed one (1 call each) -- proving the 0s above are not an
    artifact of the fakes being disconnected."""
    service, gateway, retriever = _governed_service_with_counting_fakes(real_registry, real_policy_registry)

    denied = service.answer("What are the payment terms?", identity=_UNKNOWN_ROLE)
    assert isinstance(denied, GateBDenied)
    assert gateway.calls == []
    assert retriever.calls == []

    allowed = service.answer(
        "What are the payment terms?",
        identity=_SUPPLIER_READER,
        requested=GateBRequest(data_class=DataClassification.INTERNAL),
    )
    assert isinstance(allowed, GroundedAnswer)
    assert len(gateway.calls) == 1
    assert len(retriever.calls) == 1


# ---------------------------------------------------------------------------
# mode_b: Gate-B allow -> selected, still never executed
# ---------------------------------------------------------------------------


def test_allowed_mode_b_request_is_selected_not_executed(real_registry, real_policy_registry):
    service = _governed_service_that_must_not_reach_retrieval_or_gateway(real_registry, real_policy_registry)

    result = service.answer(
        "List active contracts.",
        identity=_SOURCING_ANALYST,
        requested=GateBRequest(data_class=DataClassification.INTERNAL),
    )

    assert isinstance(result, ModeBSelected)
    assert result.intent_id == "INT-STRUCTURED-LOOKUP"
    assert "not" in result.message.lower()


def test_allowed_mode_b_request_never_opens_a_database_connection(real_registry, real_policy_registry, monkeypatch):
    """Behavioral proof, not just absence-of-import: `sqlite3.connect`
    itself raises if called at all, and the request still succeeds
    normally, mirroring `test_day09_no_fallthrough.py`'s identical proof
    for the ungoverned path."""

    def _must_not_connect(*args, **kwargs):
        raise AssertionError("sqlite3.connect must not be called for an authorized mode_b selection")

    monkeypatch.setattr(sqlite3, "connect", _must_not_connect)
    service = _governed_service_that_must_not_reach_retrieval_or_gateway(real_registry, real_policy_registry)

    result = service.answer(
        "List active contracts.",
        identity=_SOURCING_ANALYST,
        requested=GateBRequest(data_class=DataClassification.INTERNAL),
    )

    assert isinstance(result, ModeBSelected)


# ---------------------------------------------------------------------------
# block/clarify (Gate-A's own) and safe_fast_path never invoke Gate-B
# ---------------------------------------------------------------------------


class _GateBMustNotBeCalled:
    """Stands in for `GateB` on a service instance, proving `.authorize()`
    is genuinely never reached for lanes that terminate before step 5 --
    an assertion-based proof, not an inference from "the result type looks
    right"."""

    def authorize(self, *args, **kwargs):  # pragma: no cover - only reached on failure
        raise AssertionError("GateB.authorize() must not be called for this lane")


def _service_with_gate_b_that_must_not_be_called(
    registry: OntologyRegistry, policy_registry: PolicyRegistry
) -> ControlPlaneAnswerService:
    rag_service = GroundedAnswerService(gateway=_NeverCalledGateway(), retriever=_never_called_retriever)
    service = ControlPlaneAnswerService(registry=registry, rag_service=rag_service, policy_registry=policy_registry)
    service.gate_b = _GateBMustNotBeCalled()  # type: ignore[assignment]
    return service


def test_gate_a_unsupported_block_never_invokes_gate_b(real_registry, real_policy_registry):
    service = _service_with_gate_b_that_must_not_be_called(real_registry, real_policy_registry)

    result = service.answer("What is tomorrow's weather?", identity=_SUPPLIER_READER)

    assert isinstance(result, GateBlocked)
    assert result.reason_code == "no_governed_match"


def test_gate_a_ambiguous_clarify_never_invokes_gate_b(real_registry, real_policy_registry):
    service = _service_with_gate_b_that_must_not_be_called(real_registry, real_policy_registry)

    result = service.answer("Show me the supplier information.", identity=_SUPPLIER_READER)

    assert isinstance(result, GateClarify)


def test_safe_fast_path_never_invokes_gate_b_and_needs_no_identity(real_registry, real_policy_registry):
    """`safe_fast_path` never touches protected data at all -- it works
    even with no identity supplied, and even on a service whose `GateB`
    would raise if ever called."""
    service = _service_with_gate_b_that_must_not_be_called(real_registry, real_policy_registry)

    result = service.answer("What can you help with?")  # no identity

    assert isinstance(result, SafeFastPathAnswer)
    assert result.intent_id == "INT-HELP"


# ---------------------------------------------------------------------------
# Gate-B integration is opt-in: no policy_registry -> exact Day 9 behavior
# ---------------------------------------------------------------------------


def test_service_without_policy_registry_behaves_exactly_like_day9(real_registry):
    """Direct side-by-side confirmation of the module docstring's own
    claim -- Day 9's own regression files already prove this at scale;
    this is the one-test spot-check living next to Day 10's own new
    coverage."""
    gateway = _CountingGateway(_ANSWERED_JSON)
    retriever = _CountingRetriever(
        chunks=[EvidenceChunk(chunk_id="C1", source_file="DOC-001.md", text="Payment terms are net 30 days.")]
    )
    rag_service = GroundedAnswerService(gateway=gateway, retriever=retriever)
    service = ControlPlaneAnswerService(registry=real_registry, rag_service=rag_service)  # no policy_registry

    assert service.gate_b is None

    result = service.answer("What are the payment terms?")  # no identity needed at all

    assert isinstance(result, GroundedAnswer)
    assert len(gateway.calls) == 1


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_governed_control_plane_answer_is_deterministic(real_registry, real_policy_registry):
    service = _governed_service_that_must_not_reach_retrieval_or_gateway(real_registry, real_policy_registry)
    cases = [
        (dict(identity=None), "What are the payment terms?"),
        (dict(identity=_UNKNOWN_ROLE), "What are the payment terms?"),
        (dict(identity=_SUPPLIER_READER), "What are the payment terms?"),
        (dict(identity=_SOURCING_ANALYST, requested=GateBRequest(data_class=DataClassification.INTERNAL)), "List active contracts."),
    ]
    first_pass = [service.answer(question, **kwargs) for kwargs, question in cases]
    second_pass = [service.answer(question, **kwargs) for kwargs, question in cases]
    assert first_pass == second_pass


# ---------------------------------------------------------------------------
# governed_ask_response_from_result maps Gate-B's two outcomes completely
# ---------------------------------------------------------------------------


def test_gate_b_denied_maps_to_the_public_contract_with_sanitized_provenance_only(real_registry, real_policy_registry):
    from aico.api.control_plane_contracts import GovernedAskStatus, governed_ask_response_from_result

    service = _governed_service_that_must_not_reach_retrieval_or_gateway(real_registry, real_policy_registry)
    result = service.answer("What are the payment terms?", identity=_UNKNOWN_ROLE)

    response = governed_ask_response_from_result(
        result, request_id="REQ-1", correlation_id="CORR-1", session_id="SESSION-1", ontology_version=real_registry.ontology_version
    )

    assert response.status is GovernedAskStatus.GATE_B_DENIED
    assert response.reason_code == "unknown_role"
    assert response.policy_version == real_policy_registry.policy_version
    assert response.answer is None
    assert response.citations == []


def test_gate_b_clarify_maps_to_the_public_contract(real_registry, real_policy_registry):
    from aico.api.control_plane_contracts import GovernedAskStatus, governed_ask_response_from_result

    service = _governed_service_that_must_not_reach_retrieval_or_gateway(real_registry, real_policy_registry)
    result = service.answer("What are the payment terms?", identity=_SUPPLIER_READER)

    response = governed_ask_response_from_result(
        result, request_id="REQ-2", correlation_id="CORR-2", session_id="SESSION-2", ontology_version=real_registry.ontology_version
    )

    assert response.status is GovernedAskStatus.GATE_B_CLARIFY
    assert response.reason_code == "data_class_selection_required"
    assert response.rule_id == "GB-R001"
