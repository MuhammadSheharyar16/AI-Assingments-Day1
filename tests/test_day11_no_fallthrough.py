"""
Day 11 Task 11 -- No generation fall-through.

"Do not generate first and validate evidence later." Day 11's own
integration point (Task 13: "Integrate with existing RAG flow") does not
exist yet, so this file proves the property the same way
`test_day10_no_fallthrough.py` already proves the identical property for
Gate-B ahead of *its* own Task 13: a real `GateC` (Task 9, built against
the real committed `SourceRegistry`/`GateCPolicyRegistry`) produces a real
`GateCDecision`, and `_generate_if_allowed()` below -- the exact discipline
any future Task 13 caller must follow -- gates one real-shaped fake
(`CountingGateway`, matching `aico.platform.model_gateway`'s own real
`chat()` protocol, the identical fake `test_day09_no_fallthrough.py`/
`test_day10_no_fallthrough.py` already use) behind it.

Proves:

    Gate-C allow                -> validated evidence reaches generation,
                                    the fake IS called exactly once (the
                                    sanity check a raise-only/disconnected
                                    fake could never give).
    Gate-C insufficient_evidence -> model generation calls = 0
    Gate-C reject                -> model generation calls = 0
    Gate-C unresolved conflict   -> model generation calls = 0 (named
                                    separately in Task 11's own bullet
                                    list even though it is one of the
                                    `reject` outcomes Gate-C's own decision
                                    algorithm reaches -- proven with its
                                    own dedicated scenario so the specific
                                    wording is not just incidentally
                                    covered)
    Gate-C clarify                -> model generation calls = 0 (not named
                                    explicitly by Task 11, but the same
                                    "only `allow` may generate" rule
                                    applies to every non-`allow` outcome)

Also proves, deliberately: a second, "wrong" harness
(`_generate_checking_decision_too_late`) that calls the Model Gateway
before ever checking `decision.decision` -- what the documented failure
mode actually looks like in code, not just what success looks like -- and
that `_generate_if_allowed()`'s own source checks the decision before it
ever references the gateway at all (an ordering proof, not just an
outcome proof). Finally, proves Task 10's own "rejected evidence must
never be passed to generation": on an `allow` decision reached only
because *some* items were filtered out, the prompt the fake gateway
actually receives contains only the validated items' content, never a
rejected item's.
"""
from __future__ import annotations

import inspect

import pytest

from aico.control.gate_c import GateC, GateCDecision, GateCRequest, GateCStatus
from aico.control.models import GateBDecision, GateBStatus
from aico.control.ontology import LaneId
from aico.control.policy_models import DataClassification
from aico.evidence.models import EvidencePackage
from aico.evidence.policy import GateCPolicyRegistry
from aico.evidence.provenance import stable_content_hash
from aico.evidence.source_registry import SourceRegistry
from aico.platform.model_gateway import CallMetadata, ChatMessage, ChatRequest, ChatResult

# ---------------------------------------------------------------------------
# Real-shaped counting fake (matching test_day09/10_no_fallthrough.py's own
# CountingGateway -- the same fake a real Task 13 integration would
# actually plug in)
# ---------------------------------------------------------------------------


class CountingGateway:
    """Stands in for the Model Gateway call that would generate the final
    answer. Counts calls (and records what it was actually asked to
    generate from) rather than merely refusing them, so the same instance
    can also prove it DOES get called when a request is actually allowed
    through, and prove exactly what evidence reached it."""

    def __init__(self, response_content: str = "fake generated answer"):
        self.response_content = response_content
        self.call_count = 0
        self.last_request: ChatRequest | None = None

    def chat(self, request: ChatRequest) -> ChatResult:
        self.call_count += 1
        self.last_request = request
        return ChatResult(
            content=self.response_content,
            metadata=CallMetadata(
                operation="chat", model_alias="fake-chat-alias", latency_ms=1.0, retry_count=0,
                token_usage=None, budget_status="within_budget",
            ),
        )


# ---------------------------------------------------------------------------
# The discipline: check the decision FIRST, only then touch the Model
# Gateway -- the exact pattern Task 13's real routing integration must
# follow, and Task 10's "rejected evidence must never be passed to
# generation" (only `validated_evidence_ids` ever reaches the prompt).
# ---------------------------------------------------------------------------


def _generate_if_allowed(decision: GateCDecision, package: EvidencePackage, gateway: CountingGateway) -> ChatResult | None:
    """Returns `None` without touching `gateway` at all unless
    `decision.decision is GateCStatus.ALLOW` -- Task 11's own required
    ordering: the control is enforced *before* the Model Gateway is ever
    called, never a `decision != "allow"` checked only after generation
    already ran (see `_generate_checking_decision_too_late` below for the
    documented failure mode this function does not exhibit). Only the
    items named in `decision.validated_evidence_ids` are ever included in
    the prompt -- a rejected item's content is never read, even when it
    is still physically present in `package.items`."""
    if decision.decision is not GateCStatus.ALLOW:
        return None
    validated_items = [item for item in package.items if item.evidence_id in decision.validated_evidence_ids]
    prompt = f"Evidence: {[item.content for item in validated_items]}"
    return gateway.chat(ChatRequest(messages=[ChatMessage(role="user", content=prompt)]))


def _generate_checking_decision_too_late(
    decision: GateCDecision, package: EvidencePackage, gateway: CountingGateway
) -> ChatResult | None:
    """The documented failure mode itself ("Do not generate first and
    validate evidence later"), reproduced deliberately so
    `test_checking_decision_too_late_is_the_documented_failure_mode` can
    show what it actually looks like in code -- generation happens
    unconditionally, *then* the decision is consulted. Never called by any
    test that asserts real system behavior; exists only as the negative
    example."""
    validated_items = [item for item in package.items if item.evidence_id in decision.validated_evidence_ids]
    prompt = f"Evidence: {[item.content for item in validated_items]}"
    result = gateway.chat(ChatRequest(messages=[ChatMessage(role="user", content=prompt)]))  # already ran
    if decision.decision is not GateCStatus.ALLOW:
        return None  # too late -- the call above already happened
    return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def source_registry() -> SourceRegistry:
    return SourceRegistry.load()


@pytest.fixture(scope="module")
def policy_registry(source_registry) -> GateCPolicyRegistry:
    return GateCPolicyRegistry.load(source_registry=source_registry)


@pytest.fixture
def gate_c(source_registry, policy_registry) -> GateC:
    return GateC(source_registry=source_registry, policy_registry=policy_registry)


@pytest.fixture
def gateway() -> CountingGateway:
    return CountingGateway()


def _allow_decision(*, tenant_ids=("TENANT-A",), data_classes=(DataClassification.INTERNAL,)) -> GateBDecision:
    return GateBDecision(
        decision=GateBStatus.ALLOW,
        effective_tenant_scope=tenant_ids,
        effective_data_classes=data_classes,
        reason_code="test_fixture",
        policy_version="1.0",
        lane=LaneId.RAG,
    )


def _item(evidence_id: str, *, source_id: str, facets: list[str], claims: dict[str, str] | None = None, **overrides) -> dict:
    content = overrides.pop("content", f"Real content for {evidence_id}.")
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


def _package(items: list[dict], *, intent_id: str = "INT-POLICY-QUESTION", **overrides) -> EvidencePackage:
    data = {
        "request_id": "REQ-001",
        "intent_id": intent_id,
        "lane": "rag",
        "as_of": "2026-09-11T12:00:00+05:00",
        "required_facets": [],
        "items": items,
    }
    data.update(overrides)
    return EvidencePackage.model_validate(data)


# ---------------------------------------------------------------------------
# Gate-C allow -> generation reached, exactly once
# ---------------------------------------------------------------------------


def test_allow_reaches_generation_exactly_once(gate_c, gateway):
    """Without this, "0 calls" on every non-allow test below could just as
    well mean the fake is disconnected from `_generate_if_allowed`
    entirely, not that it is correctly declining to call it."""
    package = _package([_item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"])])
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only")
    )
    assert decision.decision is GateCStatus.ALLOW

    result = _generate_if_allowed(decision, package, gateway)

    assert result is not None
    assert gateway.call_count == 1


# ---------------------------------------------------------------------------
# Gate-C insufficient_evidence -> 0 generation calls
# ---------------------------------------------------------------------------


def test_insufficient_evidence_makes_zero_generation_calls(gate_c, gateway):
    package = _package([_item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"])])
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_and_invoice")
    )
    assert decision.decision is GateCStatus.INSUFFICIENT_EVIDENCE

    result = _generate_if_allowed(decision, package, gateway)

    assert result is None
    assert gateway.call_count == 0


def test_empty_candidate_package_makes_zero_generation_calls(gate_c, gateway):
    package = _package([])
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only")
    )
    assert decision.decision is GateCStatus.INSUFFICIENT_EVIDENCE

    result = _generate_if_allowed(decision, package, gateway)

    assert result is None
    assert gateway.call_count == 0


# ---------------------------------------------------------------------------
# Gate-C reject -> 0 generation calls
# ---------------------------------------------------------------------------


def test_reject_unknown_source_makes_zero_generation_calls(gate_c, gateway):
    package = _package([_item("E-A", source_id="SRC-DOES-NOT-EXIST", facets=["payment_terms"])])
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only")
    )
    assert decision.decision is GateCStatus.REJECT

    result = _generate_if_allowed(decision, package, gateway)

    assert result is None
    assert gateway.call_count == 0


def test_reject_gate_b_not_allowed_makes_zero_generation_calls(gate_c, gateway):
    package = _package([_item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"])])
    deny_decision = GateBDecision(decision=GateBStatus.DENY, reason_code="test_fixture", policy_version="1.0")

    decision = gate_c.evaluate(
        gate_b_decision=deny_decision, package=package, request=GateCRequest(request_kind="payment_terms_only")
    )
    assert decision.decision is GateCStatus.REJECT

    result = _generate_if_allowed(decision, package, gateway)

    assert result is None
    assert gateway.call_count == 0


def test_reject_content_hash_mismatch_makes_zero_generation_calls(gate_c, gateway):
    package = _package(
        [_item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"], content_hash="not-a-real-hash")]
    )
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only")
    )
    assert decision.decision is GateCStatus.REJECT

    result = _generate_if_allowed(decision, package, gateway)

    assert result is None
    assert gateway.call_count == 0


# ---------------------------------------------------------------------------
# Gate-C unresolved conflict -> 0 generation calls (Task 11's own named
# bullet, even though it is a `reject` outcome under the hood)
# ---------------------------------------------------------------------------


def test_unresolved_conflict_makes_zero_generation_calls(gate_c, gateway):
    package = _package(
        [
            _item(
                "E-A",
                source_id="SRC-POLICY-A",
                facets=["supplier_identity", "payment_terms"],
                claims={"payment_terms": "net 30"},
            ),
            _item("E-B", source_id="SRC-POLICY-A", facets=["payment_terms"], claims={"payment_terms": "net 45"}),
        ]
    )
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only")
    )
    assert decision.decision is GateCStatus.REJECT
    assert decision.conflict_facets == ("payment_terms",)

    result = _generate_if_allowed(decision, package, gateway)

    assert result is None
    assert gateway.call_count == 0


# ---------------------------------------------------------------------------
# Gate-C clarify -> 0 generation calls (the same rule, not explicitly
# named by Task 11 but implied: only `allow` may ever generate)
# ---------------------------------------------------------------------------


def test_clarify_makes_zero_generation_calls(gate_c, gateway):
    package = _package([_item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"])])
    decision = gate_c.evaluate(gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind=None))
    assert decision.decision is GateCStatus.CLARIFY

    result = _generate_if_allowed(decision, package, gateway)

    assert result is None
    assert gateway.call_count == 0


# ---------------------------------------------------------------------------
# Task 10's own rule, checked at the generation boundary: rejected
# evidence must never be passed to generation
# ---------------------------------------------------------------------------


def test_rejected_evidence_content_never_reaches_the_prompt_even_on_allow(gate_c, gateway):
    """An `allow` reached only because a mixed-in invalid item was
    filtered out -- the fake gateway's own recorded prompt must contain
    the validated item's content and must not contain the rejected item's
    distinctive content string."""
    package = _package(
        [
            _item("E-good", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"], content="Governed payment terms text."),
            _item(
                "E-bad",
                source_id="SRC-DOES-NOT-EXIST",
                facets=["payment_terms"],
                content="REJECTED_SENTINEL_CONTENT_MUST_NOT_APPEAR",
            ),
        ]
    )
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only")
    )
    assert decision.decision is GateCStatus.ALLOW
    assert decision.validated_evidence_ids == ("E-good",)
    assert decision.rejected_evidence_ids == ("E-bad",)

    result = _generate_if_allowed(decision, package, gateway)

    assert result is not None
    assert gateway.call_count == 1
    prompt_text = gateway.last_request.messages[0].content
    assert "Governed payment terms text." in prompt_text
    assert "REJECTED_SENTINEL_CONTENT_MUST_NOT_APPEAR" not in prompt_text


# ---------------------------------------------------------------------------
# The documented failure mode itself: checking the decision too late
# ---------------------------------------------------------------------------


def test_checking_decision_too_late_is_the_documented_failure_mode(gate_c, gateway):
    """Deliberately proves the *wrong* pattern actually fails the property
    Task 11 names: "Do not generate first and validate evidence later."
    `_generate_checking_decision_too_late` calls the Model Gateway
    unconditionally before ever looking at `decision` -- this is what that
    failure looks like in code, contrasted with `_generate_if_allowed`'s
    correct ordering above."""
    package = _package([_item("E-A", source_id="SRC-DOES-NOT-EXIST", facets=["payment_terms"])])
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only")
    )
    assert decision.decision is GateCStatus.REJECT

    result = _generate_checking_decision_too_late(decision, package, gateway)

    assert result is None  # the final *answer* still correctly withholds a result...
    assert gateway.call_count == 1  # ...but generation already ran by then. This is the failure.


def test_correct_harness_checks_decision_before_referencing_gateway():
    """Ordering proof, not just an outcome proof: the same *outcome* (0
    calls) could coincidentally arise from a differently-broken
    implementation too. Inspects `_generate_if_allowed`'s own source and
    asserts the decision check appears before the gateway is ever
    referenced -- the wrong-ordering contrast function above is checked
    too, to prove this assertion actually distinguishes the two."""
    correct_source = inspect.getsource(_generate_if_allowed)
    decision_check_pos = correct_source.index("decision.decision is not GateCStatus.ALLOW")
    gateway_pos = correct_source.index("gateway.chat")
    assert decision_check_pos < gateway_pos

    wrong_source = inspect.getsource(_generate_checking_decision_too_late)
    wrong_gateway_pos = wrong_source.index("gateway.chat")
    wrong_decision_check_pos = wrong_source.index("decision.decision is not GateCStatus.ALLOW")
    assert wrong_gateway_pos < wrong_decision_check_pos  # confirms the contrast function really is backwards
