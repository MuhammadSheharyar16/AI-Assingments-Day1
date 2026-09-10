"""
Day 10 Task 11 -- No fall-through.

"A decision='deny' field returned after the data was already retrieved is
a failure" (Day 10 assignment). Day 9's own `test_day09_no_fallthrough.py`
proves this property against a real, already-wired orchestration service
(`ControlPlaneAnswerService`, built in that day's own Task 9) -- Day 10's
equivalent integration (Task 13: "Integrate with Day 9 routing", wiring
Gate-B in front of the real RAG/Mode-B protected lanes) does not exist yet.
This file therefore proves the identical property the way Task 11 itself
asks for it: "Instrument deterministic fakes/counters" -- a real `GateB`
(Task 3-10, built against the real committed `policy/gate_b_policy.v1.json`)
produces a real `GateBDecision`, and `_run_protected_lane_if_allowed()`
below -- the exact discipline any future Task 13 caller must follow --
gates two real-shaped fakes (`CountingRetriever`/`CountingGateway`,
matching `aico.rag`/`aico.platform.model_gateway`'s own real protocols, the
same fakes `test_day09_no_fallthrough.py` already uses) behind it.

Proves:

    Gate-B deny     -> 0 protected retrieval calls, 0 model generation calls
    Gate-B clarify  -> 0 protected retrieval calls, 0 model generation calls
    Gate-B allow    -> both fakes ARE reached, exactly once each -- the
                        sanity check a raise-only/disconnected fake could
                        never give (see `CountingGateway`/`CountingRetriever`'s
                        own docstrings): "0 calls" on deny/clarify only means
                        something if the same wiring provably calls them
                        when it should.

Also proves the *documented failure mode itself*, deliberately: a second,
"wrong" harness that calls the protected retriever before checking
`decision.decision` -- showing what "the control enforced before protected
evidence access" fails to look like, not just what success looks like -- and
that `_run_protected_lane_if_allowed()`'s own source checks the decision
before it ever references the retriever/gateway at all (an ordering proof,
not just an outcome proof: the same *outcome* -- 0 calls -- could
coincidentally arise from a differently-broken implementation too).
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field

import pytest

from aico.api.identity import TrustedIdentity
from aico.control.gate_b import GateB, GateBRequest
from aico.control.models import GateADecision, GateAStatus, GateBDecision, GateBStatus, LaneDecision
from aico.control.ontology import LaneId
from aico.control.policy_models import DataClassification
from aico.control.policy_registry import PolicyRegistry
from aico.platform.model_gateway import CallMetadata, ChatMessage, ChatRequest, ChatResult
from aico.rag.citation_validator import EvidenceChunk

# ---------------------------------------------------------------------------
# Real-shaped counting fakes (matching test_day09_no_fallthrough.py's own
# CountingGateway/CountingRetriever -- the same fakes a real Task 13
# integration would actually plug in)
# ---------------------------------------------------------------------------


@dataclass
class CountingRetriever:
    """Stands in for the protected evidence retrieval a real `rag`/`mode_b`
    lane performs. Counts calls rather than merely refusing them, so the
    *same* instance can also prove it DOES get called when a request is
    actually allowed through -- a raise-only fake could show "0 calls" for
    the wrong reason (e.g. never wired in at all)."""

    chunks: list[EvidenceChunk] = field(default_factory=list)
    call_count: int = field(default=0, init=False)

    def retrieve(self, query: str) -> list[EvidenceChunk]:
        self.call_count += 1
        return self.chunks


@dataclass
class CountingGateway:
    """Stands in for the Model Gateway call that would generate the
    protected answer."""

    response_content: str = "fake generated answer"
    call_count: int = field(default=0, init=False)

    def chat(self, request: ChatRequest) -> ChatResult:
        self.call_count += 1
        return ChatResult(
            content=self.response_content,
            metadata=CallMetadata(
                operation="chat", model_alias="fake-chat-alias", latency_ms=1.0, retry_count=0,
                token_usage=None, budget_status="within_budget",
            ),
        )


# ---------------------------------------------------------------------------
# The discipline: check the decision FIRST, only then touch protected
# retrieval/model generation -- the exact pattern Task 13's real routing
# integration must follow.
# ---------------------------------------------------------------------------


def _run_protected_lane_if_allowed(
    decision: GateBDecision, retriever: CountingRetriever, gateway: CountingGateway, query: str
) -> ChatResult | None:
    """Returns `None` without touching `retriever`/`gateway` at all unless
    `decision.decision is GateBStatus.ALLOW` -- Task 11's own required
    ordering: the control is enforced *before* protected evidence access,
    never a `decision="deny"` field checked only after `retriever`/
    `gateway` already ran (see `_run_protected_lane_checking_decision_too_late`
    below for the documented failure mode this function does not
    exhibit)."""
    if decision.decision is not GateBStatus.ALLOW:
        return None
    evidence = retriever.retrieve(query)
    prompt = f"{query}\n\nEvidence: {[chunk.text for chunk in evidence]}"
    return gateway.chat(ChatRequest(messages=[ChatMessage(role="user", content=prompt)]))


def _run_protected_lane_checking_decision_too_late(
    decision: GateBDecision, retriever: CountingRetriever, gateway: CountingGateway, query: str
) -> ChatResult | None:
    """The documented failure mode itself (Day 10 assignment: "A
    decision='deny' field returned after the data was already retrieved is
    a failure"), reproduced deliberately so `test_checking_decision_too_late_
    is_the_documented_failure_mode` can show what it actually looks like in
    code -- retrieval happens unconditionally, *then* the decision is
    consulted. Never called by any test that asserts real system
    behavior; exists only as the negative example."""
    evidence = retriever.retrieve(query)  # protected access already happened
    if decision.decision is not GateBStatus.ALLOW:
        return None  # too late -- the call above already ran
    prompt = f"{query}\n\nEvidence: {[chunk.text for chunk in evidence]}"
    return gateway.chat(ChatRequest(messages=[ChatMessage(role="user", content=prompt)]))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def registry() -> PolicyRegistry:
    return PolicyRegistry.load()


@pytest.fixture
def gate_b(registry: PolicyRegistry) -> GateB:
    return GateB(registry)


@pytest.fixture
def retriever() -> CountingRetriever:
    return CountingRetriever(chunks=[EvidenceChunk(chunk_id="C1", source_file="DOC-001.md", text="Payment terms are net 30 days.")])


@pytest.fixture
def gateway() -> CountingGateway:
    return CountingGateway()


def _matched(intent_id: str) -> GateADecision:
    return GateADecision(
        status=GateAStatus.MATCHED,
        domain="supplier_governance",
        intent_id=intent_id,
        reason_code="test_setup",
        ontology_version="1.0",
    )


def _lane_decision(lane: LaneId, intent_id: str | None) -> LaneDecision:
    return LaneDecision(lane=lane, intent_id=intent_id, reason_code="test_setup", ontology_version="1.0")


# ---------------------------------------------------------------------------
# Gate-B deny -> 0 protected retrieval / model calls
# ---------------------------------------------------------------------------


def test_missing_identity_deny_makes_zero_protected_calls(gate_b, retriever, gateway):
    decision = gate_b.authorize(None, _matched("INT-POLICY-QUESTION"), _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION"))
    assert decision.decision is GateBStatus.DENY

    result = _run_protected_lane_if_allowed(decision, retriever, gateway, "What are the payment terms?")

    assert result is None
    assert retriever.call_count == 0
    assert gateway.call_count == 0


def test_unknown_role_deny_makes_zero_protected_calls(gate_b, retriever, gateway):
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("made_up_role",))
    decision = gate_b.authorize(identity, _matched("INT-POLICY-QUESTION"), _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION"))
    assert decision.decision is GateBStatus.DENY

    result = _run_protected_lane_if_allowed(decision, retriever, gateway, "What are the payment terms?")

    assert result is None
    assert retriever.call_count == 0
    assert gateway.call_count == 0


def test_matched_but_denied_rule_makes_zero_protected_calls(gate_b, retriever, gateway):
    """`GB-R002`: `supplier_reader` matches a real rule for structured
    lookup, but that rule's own `allowed` is `False` -- still zero calls,
    not "a rule was found so proceed."""
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("supplier_reader",))
    decision = gate_b.authorize(
        identity,
        _matched("INT-STRUCTURED-LOOKUP"),
        _lane_decision(LaneId.MODE_B, "INT-STRUCTURED-LOOKUP"),
        GateBRequest(data_class=DataClassification.INTERNAL),
    )
    assert decision.decision is GateBStatus.DENY

    result = _run_protected_lane_if_allowed(decision, retriever, gateway, "List Supplier Alpha's contract status.")

    assert result is None
    assert retriever.call_count == 0
    assert gateway.call_count == 0


def test_cross_tenant_deny_makes_zero_protected_calls(gate_b, retriever, gateway):
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-2", roles=("sourcing_analyst",))
    decision = gate_b.authorize(
        identity,
        _matched("INT-POLICY-QUESTION"),
        _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION"),
        GateBRequest(data_class=DataClassification.INTERNAL, tenant_ids=("TENANT-B",)),
    )
    assert decision.decision is GateBStatus.DENY

    result = _run_protected_lane_if_allowed(decision, retriever, gateway, "What are the payment terms?")

    assert result is None
    assert retriever.call_count == 0
    assert gateway.call_count == 0


def test_disallowed_classification_deny_makes_zero_protected_calls(gate_b, retriever, gateway):
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("supplier_reader",))
    decision = gate_b.authorize(
        identity,
        _matched("INT-POLICY-QUESTION"),
        _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION"),
        GateBRequest(data_class=DataClassification.RESTRICTED),
    )
    assert decision.decision is GateBStatus.DENY

    result = _run_protected_lane_if_allowed(decision, retriever, gateway, "What are the payment terms?")

    assert result is None
    assert retriever.call_count == 0
    assert gateway.call_count == 0


def test_no_matching_rule_deny_makes_zero_protected_calls(gate_b, retriever, gateway):
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-3", roles=("compliance_reviewer",))
    decision = gate_b.authorize(identity, _matched("INT-POLICY-QUESTION"), _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION"))
    assert decision.decision is GateBStatus.DENY

    result = _run_protected_lane_if_allowed(decision, retriever, gateway, "What are the payment terms?")

    assert result is None
    assert retriever.call_count == 0
    assert gateway.call_count == 0


def test_unmatched_gate_a_decision_deny_makes_zero_protected_calls(gate_b, retriever, gateway):
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("supplier_reader",))
    gate_a_decision = GateADecision(status=GateAStatus.UNSUPPORTED, reason_code="no_governed_match", ontology_version="1.0")
    decision = gate_b.authorize(identity, gate_a_decision, _lane_decision(LaneId.BLOCK, None))
    assert decision.decision is GateBStatus.DENY

    result = _run_protected_lane_if_allowed(decision, retriever, gateway, "What is tomorrow's weather?")

    assert result is None
    assert retriever.call_count == 0
    assert gateway.call_count == 0


# ---------------------------------------------------------------------------
# Gate-B clarify -> 0 protected retrieval / model calls
# ---------------------------------------------------------------------------


def test_clarify_makes_zero_protected_calls(gate_b, retriever, gateway):
    """`GB-R005`: a real, matched, `allowed=true` rule -- clarify is not a
    partial allow, and still reaches zero protected calls even though a
    rule genuinely did match."""
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-3", roles=("compliance_reviewer",))
    decision = gate_b.authorize(
        identity,
        _matched("INT-STRUCTURED-LOOKUP"),
        _lane_decision(LaneId.MODE_B, "INT-STRUCTURED-LOOKUP"),
        GateBRequest(data_class=None),
    )
    assert decision.decision is GateBStatus.CLARIFY

    result = _run_protected_lane_if_allowed(decision, retriever, gateway, "List Supplier Alpha's contract status.")

    assert result is None
    assert retriever.call_count == 0
    assert gateway.call_count == 0


# ---------------------------------------------------------------------------
# The counters are actually wired: allow DOES reach them, exactly once each
# ---------------------------------------------------------------------------


def test_allow_reaches_protected_retrieval_and_model_exactly_once(gate_b, retriever, gateway):
    """Without this, "0 calls" on every deny/clarify test above could just
    as well mean the fakes are disconnected from `_run_protected_lane_if_
    allowed` entirely, not that it is correctly declining to call them."""
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("supplier_reader",))
    decision = gate_b.authorize(
        identity,
        _matched("INT-POLICY-QUESTION"),
        _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION"),
        GateBRequest(data_class=DataClassification.INTERNAL),
    )
    assert decision.decision is GateBStatus.ALLOW

    result = _run_protected_lane_if_allowed(decision, retriever, gateway, "What are the payment terms?")

    assert result is not None
    assert retriever.call_count == 1
    assert gateway.call_count == 1


# ---------------------------------------------------------------------------
# The documented failure mode itself: checking the decision too late
# ---------------------------------------------------------------------------


def test_checking_decision_too_late_is_the_documented_failure_mode(gate_b, retriever, gateway):
    """Deliberately proves the *wrong* pattern actually fails the property
    Task 11 names: "A decision='deny' field returned after the data was
    already retrieved is a failure." `_run_protected_lane_checking_decision_
    too_late` retrieves unconditionally before ever looking at `decision` --
    this is what that failure looks like in code, contrasted with
    `_run_protected_lane_if_allowed`'s correct ordering above."""
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("made_up_role",))
    decision = gate_b.authorize(identity, _matched("INT-POLICY-QUESTION"), _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION"))
    assert decision.decision is GateBStatus.DENY

    result = _run_protected_lane_checking_decision_too_late(decision, retriever, gateway, "What are the payment terms?")

    assert result is None  # the final *answer* still correctly withholds a result...
    assert retriever.call_count == 1  # ...but protected evidence was already retrieved by then. This is the failure.


def test_correct_harness_checks_decision_before_referencing_retriever_or_gateway():
    """Ordering proof, not just an outcome proof: the same *outcome* (0
    calls) could coincidentally arise from a differently-broken
    implementation too. Inspects `_run_protected_lane_if_allowed`'s own
    source and asserts the decision check appears before either fake is
    ever referenced -- the wrong-ordering contrast function above is
    checked too, to prove this assertion actually distinguishes the two."""
    correct_source = inspect.getsource(_run_protected_lane_if_allowed)
    decision_check_pos = correct_source.index("decision.decision is not GateBStatus.ALLOW")
    retriever_pos = correct_source.index("retriever.retrieve")
    assert decision_check_pos < retriever_pos

    wrong_source = inspect.getsource(_run_protected_lane_checking_decision_too_late)
    wrong_retriever_pos = wrong_source.index("retriever.retrieve")
    wrong_decision_check_pos = wrong_source.index("decision.decision is not GateBStatus.ALLOW")
    assert wrong_retriever_pos < wrong_decision_check_pos  # confirms the contrast function really is backwards
