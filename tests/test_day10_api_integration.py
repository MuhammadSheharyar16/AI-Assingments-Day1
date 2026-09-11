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
    `config/control-plane.yaml`'s `gate_b.enabled` (`true` is the
    committed default -- a shipped deployment authorizes through Gate-B
    unless it explicitly opts out, the way `test_day09_api_integration.py`
    does for its own ungoverned synthetic identity) plus unconditional
    `identity` forwarding.
  - Even with Gate-B reachable, `/ask/governed`'s public request body had
    no field to declare a data-classification preference, and every
    committed rule (`GB-R001`/`GB-R003`/`GB-R004`/`GB-R005`) authorizes
    2+ data classes -- so `clarify` was the only HTTP-reachable non-deny
    outcome; `allow` could never actually be reached over HTTP. Fixed by
    `GovernedAskRequest.data_class` (`control_plane_contracts.py`, Day 10
    Task 13's *one* Gate-B-relevant request-body field -- a narrowing
    preference only, never a grant: a value the matched rule does not
    itself authorize still denies).

Every request below overrides `get_control_plane_config` explicitly to
one of two variants (`_gate_b_enabled_config()`/`_gate_b_disabled_config()`)
rather than relying on whatever the committed file's default happens to
be at the time this file runs -- an explicit override is the same
override-pattern every other test file in this project uses, and it is
what lets `test_gate_b_disabled_reproduces_day9_behavior_unchanged_for_
the_identical_request` below stay meaningful (and still compile the same
way) regardless of which value `config/control-plane.yaml` ships with.

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
  - `gate_b.enabled: false`, explicitly requested via override, reproduces
    Day 9's exact `/ask/governed` behavior unchanged for the identical
    request -- a direct side-by-side confirmation that Gate-B activation
    is a genuine, working toggle in both directions, not a label with no
    effect.
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


def _config_with_gate_b(enabled: bool):
    """The real committed `config/control-plane.yaml`, with only
    `gate_b.enabled` explicitly set to `enabled` -- every other governed
    value (registry path, enabled lanes, clarification policy) stays
    exactly the committed default, `dataclasses.replace` touching nothing
    else. Used for both directions (`True`/`False`) so every test below
    is explicit about which one it needs, rather than a `True` case
    leaning on an override and a `False` case leaning on whatever the
    committed file's own default happens to be.

    `gate_c` is always forced `disabled` here, regardless of `enabled` --
    this file's own scope is Gate-B's live wiring, proven with
    `CountingRetriever`'s fake, ungoverned `EvidenceChunk` (`source_file=
    "DOC-001.md"`, not one of the real corpus's own governed documents);
    Gate-C's live wiring gets its own dedicated proof
    (`test_day11_real_corpus_integration.py`) against the real corpus.
    Leaving Gate-C at the committed default here would make every
    `rag`-lane request in this file fail Gate-C's `unknown_source` check
    for a reason this file was never testing."""
    real = load_control_plane_config()
    return dataclasses.replace(
        real,
        gate_b=dataclasses.replace(real.gate_b, enabled=enabled),
        gate_c=dataclasses.replace(real.gate_c, enabled=False),
    )


def _client(identity: TrustedIdentity, gateway: CountingGateway, retriever: CountingRetriever, *, gate_b_enabled: bool = True) -> TestClient:
    service = GroundedAnswerService(gateway=gateway, retriever=retriever)
    store = InMemorySessionStore()
    app.dependency_overrides[get_answer_service] = lambda: service
    app.dependency_overrides[get_trusted_identity] = lambda: identity
    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[get_control_plane_config] = lambda: _config_with_gate_b(gate_b_enabled)
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
# gate_b.enabled: false, explicitly requested -- the opt-out, not the default
# ---------------------------------------------------------------------------


def test_gate_b_disabled_reproduces_day9_behavior_unchanged_for_the_identical_request():
    """The exact same request/identity as the clarify case above, but with
    `gate_b.enabled` explicitly overridden `false` (`true` is the
    committed default -- see the module docstring) -- Gate-B never runs
    at all, and the request answers exactly as `test_day09_api_
    integration.py::test_rag_lane_answers_and_calls_gateway_and_retriever_
    exactly_once` already proves for Day 9's own identity. Same identity,
    same question, materially different outcome shape depending only on
    this one override -- the side-by-side confirmation that
    `gate_b.enabled` is a genuine, working toggle in both directions, not
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
