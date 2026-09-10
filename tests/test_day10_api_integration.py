"""
Day 10 Task 13 -- `POST /ask/governed` through Gate-B, end to end, over a
real HTTP request (`api/app.py`, `api/control_plane.py`,
`api/control_plane_contracts.py`, `api/dependencies.py`).

`test_day10_control_plane_integration.py` already proves the full
required order at the `ControlPlaneAnswerService` level (constructing the
service directly with a real `policy_registry`). This file closes the two
gaps that left open: whether the live route actually reaches Gate-B at
all, and whether a genuine `allow` (not just `deny`/`clarify`) is
reachable through it. Neither was true, by default, before this file's
own change set:

  - `get_control_plane_answer_service` (`dependencies.py`) never passed
    `policy_registry` into the service, and `ask_governed()`
    (`control_plane.py`) never forwarded the already-resolved trusted
    `identity` into `.answer()` -- so Gate-B never ran at all. Fixed via
    `config/control-plane.yaml`'s `gate_b.enabled` (default `false`,
    preserving `test_day09_api_integration.py`'s exact behavior
    unchanged) plus unconditional `identity` forwarding.
  - Even with Gate-B reachable, `/ask/governed`'s public request body had
    no field to declare a data-classification preference, and every
    committed rule (`GB-R001`/`GB-R003`/`GB-R004`/`GB-R005`) authorizes
    2+ data classes -- so `clarify` was the only HTTP-reachable non-deny
    outcome; `allow` could never actually be reached over HTTP. Fixed by
    `GovernedAskRequest.data_class` (`control_plane_contracts.py`, Day 10
    Task 13's *one* Gate-B-relevant request-body field -- a narrowing
    preference only, never a grant: a value the matched rule does not
    itself authorize still denies).

`config/control-plane.yaml`'s `gate_b.enabled` is overridden `true` here
via `get_control_plane_config`, mirroring the override pattern every
other test file in this project uses.

Proves, over a real `TestClient(app)` request:

  - Gate-B `deny` (missing governed role) -> `status="gate_b_denied"`,
    zero retrieval/model calls (counting fakes, same discipline
    `test_day09_api_integration.py`/`test_day10_no_fallthrough.py` use).
  - Gate-B `deny` (a `data_class` the matched rule does not authorize --
    `restricted` for `supplier_reader`) -> `status="gate_b_denied"`,
    `reason_code="data_classification_not_allowed"`, zero protected
    calls -- proving the field can only narrow, never widen.
  - Gate-B `clarify` (a matched, allowed rule authorizing more than one
    data classification, `data_class` omitted) -> `status="gate_b_clarify"`,
    zero retrieval/model calls.
  - Gate-B `allow` (a `data_class` the matched rule *does* authorize) ->
    `status="answered"`, retrieval/the Model Gateway reached exactly once
    each -- the genuine HTTP-reachable `allow` this file's own change set
    unlocks, not merely inferred from the `ControlPlaneAnswerService`-level
    proof `test_day10_control_plane_integration.py::test_allowed_rag_
    request_reaches_retrieval_and_model_exactly_once` already gives.
  - `gate_b.enabled: false` (the committed default) reproduces Day 9's
    exact `/ask/governed` behavior unchanged for the identical request --
    a direct side-by-side confirmation that activating Gate-B is genuinely
    opt-in at the deployment level, not a silent behavior change.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from fastapi.testclient import TestClient

from aico.api.app import app
from aico.api.dependencies import get_answer_service, get_control_plane_config, get_session_store
from aico.api.identity import TrustedIdentity, get_trusted_identity
from aico.control.config import load_control_plane_config
from aico.memory.store import InMemorySessionStore
from aico.platform.model_gateway import CallMetadata, ChatRequest, ChatResult
from aico.rag.answer_service import GroundedAnswerService
from aico.rag.citation_validator import EvidenceChunk

_SUPPLIER_READER = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("supplier_reader",))
_UNKNOWN_ROLE = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-X", roles=("made_up_role",))

_ANSWERED_JSON = (
    '{"schema_version": "1.0", "status": "answered", "answer": "Payment terms are net 30 days.", '
    '"citations": [{"chunk_id": "C1", "source_file": "DOC-001.md"}], "confidence_label": "high"}'
)


@dataclass
class CountingGateway:
    call_count: int = field(default=0, init=False)

    def chat(self, request: ChatRequest) -> ChatResult:
        self.call_count += 1
        return ChatResult(
            content=_ANSWERED_JSON,
            metadata=CallMetadata(
                operation="chat", model_alias="fake-chat-alias", latency_ms=1.0, retry_count=0,
                token_usage=None, budget_status="within_budget",
            ),
        )


@dataclass
class CountingRetriever:
    call_count: int = field(default=0, init=False)

    def __call__(self, query: str) -> list[EvidenceChunk]:
        self.call_count += 1
        return [EvidenceChunk(chunk_id="C1", source_file="DOC-001.md", text="Payment terms are net 30 days.")]


def _gate_b_enabled_config():
    """The real committed `config/control-plane.yaml`, with only
    `gate_b.enabled` flipped `true` -- every other governed value
    (registry path, enabled lanes, clarification policy) stays exactly
    the committed default, `dataclasses.replace` touching nothing else."""
    real = load_control_plane_config()
    return dataclasses.replace(real, gate_b=dataclasses.replace(real.gate_b, enabled=True))


def _client(identity: TrustedIdentity, gateway: CountingGateway, retriever: CountingRetriever, *, gate_b_enabled: bool = True) -> TestClient:
    service = GroundedAnswerService(gateway=gateway, retriever=retriever)
    store = InMemorySessionStore()
    app.dependency_overrides[get_answer_service] = lambda: service
    app.dependency_overrides[get_trusted_identity] = lambda: identity
    app.dependency_overrides[get_session_store] = lambda: store
    if gate_b_enabled:
        app.dependency_overrides[get_control_plane_config] = _gate_b_enabled_config
    return TestClient(app)


def teardown_function() -> None:
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Gate-B deny, reached through a real HTTP request
# ---------------------------------------------------------------------------


def test_unknown_role_denies_through_the_live_route_with_zero_protected_calls():
    gateway, retriever = CountingGateway(), CountingRetriever()
    client = _client(_UNKNOWN_ROLE, gateway, retriever)

    resp = client.post("/ask/governed", json={"question": "What are the payment terms?"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "gate_b_denied"
    assert body["reason_code"] == "unknown_role"
    assert body["policy_version"] == "1.0"
    assert gateway.call_count == 0
    assert retriever.call_count == 0


def test_disallowed_data_class_denies_through_the_live_route_with_zero_protected_calls():
    """`supplier_reader`'s matched rule (`GB-R001`) authorizes
    `[public, internal]` -- `restricted` is a real governed classification
    (`policy/gate_b_policy.v1.json`'s own `data_classifications`) this
    caller's rule simply does not authorize. Proves `GovernedAskRequest.
    data_class` can only ever narrow what Gate-B considers, never widen
    it -- declaring a classification the matched rule does not authorize
    still denies, exactly as if nothing had been declared at all."""
    gateway, retriever = CountingGateway(), CountingRetriever()
    client = _client(_SUPPLIER_READER, gateway, retriever)

    resp = client.post(
        "/ask/governed", json={"question": "What are the payment terms?", "data_class": "restricted"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "gate_b_denied"
    assert body["reason_code"] == "data_classification_not_allowed"
    assert body["rule_id"] == "GB-R001"
    assert gateway.call_count == 0
    assert retriever.call_count == 0


# ---------------------------------------------------------------------------
# Gate-B clarify, reached through a real HTTP request
# ---------------------------------------------------------------------------


def test_ambiguous_data_class_clarifies_through_the_live_route_with_zero_protected_calls():
    gateway, retriever = CountingGateway(), CountingRetriever()
    client = _client(_SUPPLIER_READER, gateway, retriever)

    resp = client.post("/ask/governed", json={"question": "What are the payment terms?"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "gate_b_clarify"
    assert body["reason_code"] == "data_class_selection_required"
    assert body["rule_id"] == "GB-R001"
    assert body["policy_version"] == "1.0"
    assert gateway.call_count == 0
    assert retriever.call_count == 0


# ---------------------------------------------------------------------------
# Gate-B allow, reached through a real HTTP request
# ---------------------------------------------------------------------------


def test_authorized_data_class_answers_through_the_live_route_reaching_retrieval_and_model_exactly_once():
    """The genuine HTTP-reachable Gate-B `allow`: same identity/question
    as the clarify case above, but with `data_class="internal"` declared
    -- a value `GB-R001` (`supplier_reader`'s matched rule) does
    authorize. Retrieval/the Model Gateway are reached exactly once each,
    the same sanity check `test_day09_api_integration.py`'s own rag-lane
    test gives, now proven with Gate-B genuinely in front of it rather
    than absent."""
    gateway, retriever = CountingGateway(), CountingRetriever()
    client = _client(_SUPPLIER_READER, gateway, retriever)

    resp = client.post(
        "/ask/governed", json={"question": "What are the payment terms?", "data_class": "internal"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "answered"
    assert body["lane"] == "rag"
    assert body["answer"] == "Payment terms are net 30 days."
    assert gateway.call_count == 1
    assert retriever.call_count == 1


# ---------------------------------------------------------------------------
# gate_b.enabled: false (the committed default) -- unchanged from Day 9
# ---------------------------------------------------------------------------


def test_gate_b_disabled_reproduces_day9_behavior_unchanged_for_the_identical_request():
    """The exact same request/identity as the clarify case above, but with
    the committed `gate_b.enabled: false` default left untouched (no
    config override) -- Gate-B never runs at all, and the request answers
    exactly as `test_day09_api_integration.py::
    test_rag_lane_answers_and_calls_gateway_and_retriever_exactly_once`
    already proves for Day 9's own identity. Same identity, same
    question, materially different outcome shape -- the side-by-side
    confirmation that `gate_b.enabled` is a genuine, working toggle, not
    a label with no effect."""
    gateway, retriever = CountingGateway(), CountingRetriever()
    client = _client(_SUPPLIER_READER, gateway, retriever, gate_b_enabled=False)

    resp = client.post("/ask/governed", json={"question": "What are the payment terms?"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "answered"
    assert body["lane"] == "rag"
    assert gateway.call_count == 1
    assert retriever.call_count == 1
