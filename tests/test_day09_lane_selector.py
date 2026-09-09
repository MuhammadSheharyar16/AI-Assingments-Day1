"""
Day 9 Task 5 -- the lane selector (`src/aico/control/lane_selector.py`)
and its typed result (`LaneDecision`, `src/aico/control/models.py`).

Proves:
  - the typed result shape itself (`extra="forbid"`, required fields, and
    that `lane` only ever accepts one of the five governed `LaneId`
    values -- no arbitrary lane strings, `lane_policy.md`);
  - every case in the pack's own `lane_selection_cases.json` routes
    exactly as the fixture requires, run against the real committed
    registry (`ontology/registry.v1.json`);
  - the required policy table from `lane_policy.md` end to end: a
    governed document/policy question routes to `rag`, a governed
    structured-data intent to `mode_b` (selected, never executed --
    `must_not_execute_database`), ambiguity to `clarify`, an unsupported
    *or* blocked Gate-A result to `block`, and an explicitly governed
    utility/help case to `safe_fast_path`;
  - the full pipeline, `GateA.classify()` -> `LaneSelector.select()`, for
    every case in `gate_a_cases.json`;
  - `LaneSelector` never touches a database -- there is nothing in this
    module or its dependencies capable of one (Day 9 rule: "Day 9 selects
    Mode B but does not implement uncontrolled Mode-B execution").

Task 10's fuller "no fall-through" proof (instrumented fakes/counters
showing zero retrieval/model calls for clarify/block) is out of scope
here; this file proves routing correctness, not call-count instrumentation.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aico.control.errors import LaneSelectionError, OntologyLookupError
from aico.control.gate_a import GateA
from aico.control.lane_selector import LaneSelector
from aico.control.models import GateADecision, GateAStatus, LaneDecision
from aico.control.ontology import LaneId
from aico.control.ontology_registry import OntologyRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent
LANE_CASES_PATH = REPO_ROOT / "data" / "day09_pack" / "fixtures" / "lane_selection_cases.json"
GATE_A_CASES_PATH = REPO_ROOT / "data" / "day09_pack" / "fixtures" / "gate_a_cases.json"


def _load_cases(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


LANE_CASES = _load_cases(LANE_CASES_PATH)
GATE_A_CASES = _load_cases(GATE_A_CASES_PATH)


@pytest.fixture(scope="module")
def real_registry() -> OntologyRegistry:
    return OntologyRegistry.load()


@pytest.fixture
def selector(real_registry: OntologyRegistry) -> LaneSelector:
    return LaneSelector(real_registry)


@pytest.fixture
def gate(real_registry: OntologyRegistry) -> GateA:
    return GateA(real_registry)


def _gate_decision_from_fixture(case: dict) -> GateADecision:
    """`lane_selection_cases.json` supplies a bare `status`/`intent_id`/
    `domain` shape as the Gate-A input, not a full `GateADecision` --
    `reason_code`/`ontology_version` are filled in here with fixed
    synthetic values since the fixture itself never varies them and
    `LaneSelector` never inspects `reason_code`'s content, only propagates
    it (see `select()`)."""
    ga = case["gate_a"]
    return GateADecision(
        status=GateAStatus(ga["status"]),
        domain=ga.get("domain"),
        intent_id=ga.get("intent_id"),
        reason_code="fixture_synthetic_reason",
        ontology_version="1.0",
    )


# ---------------------------------------------------------------------------
# Typed result shape
# ---------------------------------------------------------------------------


def test_lane_decision_requires_lane_reason_code_and_ontology_version():
    with pytest.raises(ValidationError):
        LaneDecision.model_validate({})


def test_lane_decision_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        LaneDecision.model_validate(
            {
                "lane": "rag",
                "reason_code": "x",
                "ontology_version": "1.0",
                "not_a_governed_field": True,
            }
        )


def test_lane_decision_rejects_arbitrary_lane_string():
    """`lane_policy.md`: "no arbitrary lane strings" -- enforced by typing
    `lane` against the closed `LaneId` enum, the same way `OntologyDocument`
    rejects an ungoverned lane reference at the ontology layer (Task 1)."""
    with pytest.raises(ValidationError):
        LaneDecision.model_validate({"lane": "made_up_lane", "reason_code": "x", "ontology_version": "1.0"})


def test_lane_decision_minimal_valid_shape():
    decision = LaneDecision.model_validate({"lane": "block", "reason_code": "no_governed_match", "ontology_version": "1.0"})
    assert decision.lane is LaneId.BLOCK
    assert decision.intent_id is None
    assert decision.domain is None


# ---------------------------------------------------------------------------
# lane_selection_cases.json, run against the real committed registry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", LANE_CASES, ids=[c["id"] for c in LANE_CASES])
def test_lane_selection_cases_fixture(selector: LaneSelector, case: dict):
    gate_decision = _gate_decision_from_fixture(case)
    lane_decision = selector.select(gate_decision)

    assert isinstance(lane_decision, LaneDecision)
    assert lane_decision.lane.value == case["expected_lane"], (case["id"], lane_decision)
    assert lane_decision.ontology_version == "1.0"


def test_lane001_governed_document_question_routes_to_rag(selector: LaneSelector):
    decision = GateADecision(
        status=GateAStatus.MATCHED,
        domain="supplier_governance",
        intent_id="INT-POLICY-QUESTION",
        reason_code="exact_phrase_match",
        ontology_version="1.0",
    )
    lane_decision = selector.select(decision)
    assert lane_decision.lane is LaneId.RAG
    assert lane_decision.intent_id == "INT-POLICY-QUESTION"
    assert lane_decision.domain == "supplier_governance"
    assert lane_decision.reason_code == "intent_allowed_lane"


def test_lane002_structured_data_intent_routes_to_mode_b_selected_not_executed(selector: LaneSelector):
    decision = GateADecision(
        status=GateAStatus.MATCHED,
        domain="supplier_governance",
        intent_id="INT-STRUCTURED-LOOKUP",
        reason_code="exact_phrase_match",
        ontology_version="1.0",
    )
    lane_decision = selector.select(decision)
    assert lane_decision.lane is LaneId.MODE_B
    # "selected, never executed": the decision is a plain typed value with
    # no callable/connection attached anywhere on it or on the selector.
    assert not hasattr(lane_decision, "execute")
    assert not hasattr(selector, "execute")


def test_lane003_ambiguous_routes_to_clarify(selector: LaneSelector):
    decision = GateADecision(
        status=GateAStatus.AMBIGUOUS,
        candidate_intents=["INT-POLICY-QUESTION", "INT-STRUCTURED-LOOKUP"],
        matched_concepts=["CON-SUPPLIER"],
        reason_code="ambiguous_multiple_intents",
        ontology_version="1.0",
    )
    lane_decision = selector.select(decision)
    assert lane_decision.lane is LaneId.CLARIFY
    assert lane_decision.reason_code == "ambiguous_multiple_intents"
    assert lane_decision.intent_id is None
    assert lane_decision.domain is None


def test_lane004_unsupported_routes_to_block(selector: LaneSelector):
    decision = GateADecision(status=GateAStatus.UNSUPPORTED, reason_code="no_governed_match", ontology_version="1.0")
    lane_decision = selector.select(decision)
    assert lane_decision.lane is LaneId.BLOCK
    assert lane_decision.reason_code == "no_governed_match"


def test_blocked_gate_a_status_also_routes_to_block(selector: LaneSelector):
    """`lane_policy.md`: "unsupported or blocked request -> block" -- both
    fail-closed statuses share one lane, not tested separately in
    `lane_selection_cases.json` but explicit in the policy text."""
    decision = GateADecision(
        status=GateAStatus.BLOCKED, reason_code="policy_blocked_system_prompt_extraction", ontology_version="1.0"
    )
    lane_decision = selector.select(decision)
    assert lane_decision.lane is LaneId.BLOCK
    assert lane_decision.reason_code == "policy_blocked_system_prompt_extraction"


def test_lane005_governed_help_case_routes_to_safe_fast_path(selector: LaneSelector):
    decision = GateADecision(
        status=GateAStatus.MATCHED,
        domain="supplier_governance",
        intent_id="INT-HELP",
        reason_code="exact_phrase_match",
        ontology_version="1.0",
    )
    lane_decision = selector.select(decision)
    assert lane_decision.lane is LaneId.SAFE_FAST_PATH


# ---------------------------------------------------------------------------
# Full pipeline: GateA.classify() -> LaneSelector.select()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", GATE_A_CASES, ids=[c["id"] for c in GATE_A_CASES])
def test_full_pipeline_gate_a_then_lane_selector(gate: GateA, selector: LaneSelector, case: dict):
    gate_decision = gate.classify(case["input"])
    lane_decision = selector.select(gate_decision)

    assert isinstance(lane_decision, LaneDecision)
    assert lane_decision.ontology_version == gate_decision.ontology_version

    if gate_decision.status is GateAStatus.MATCHED:
        intent = gate.registry.get_intent(gate_decision.intent_id)
        assert lane_decision.lane in intent.allowed_lanes
    elif gate_decision.status is GateAStatus.AMBIGUOUS:
        assert lane_decision.lane is LaneId.CLARIFY
    else:  # UNSUPPORTED or BLOCKED
        assert lane_decision.lane is LaneId.BLOCK


# ---------------------------------------------------------------------------
# Never invents a lane / only picks from the intent's own governed lanes
# ---------------------------------------------------------------------------


def test_matched_lane_always_a_member_of_the_intents_own_allowed_lanes(selector: LaneSelector, real_registry: OntologyRegistry):
    for intent in real_registry.intents:
        decision = GateADecision(
            status=GateAStatus.MATCHED,
            domain=intent.domain,
            intent_id=intent.intent_id,
            reason_code="exact_phrase_match",
            ontology_version=real_registry.ontology_version,
        )
        lane_decision = selector.select(decision)
        assert lane_decision.lane in intent.allowed_lanes


def test_matched_decision_without_intent_id_raises_lane_selection_error(selector: LaneSelector):
    """A `MATCHED` `GateADecision` with no `intent_id` violates
    `GateADecision`'s own invariant (Task 3) -- `GateA.classify()` never
    produces one, so this can only arise from a hand-built decision, and
    `LaneSelector` fails loudly rather than silently misrouting it."""
    decision = GateADecision(status=GateAStatus.MATCHED, reason_code="exact_phrase_match", ontology_version="1.0")
    with pytest.raises(LaneSelectionError):
        selector.select(decision)


def test_matched_decision_with_unknown_intent_id_raises(selector: LaneSelector):
    decision = GateADecision(
        status=GateAStatus.MATCHED,
        intent_id="INT-DOES-NOT-EXIST",
        reason_code="exact_phrase_match",
        ontology_version="1.0",
    )
    with pytest.raises(OntologyLookupError):  # propagated from the registry, not swallowed
        selector.select(decision)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_select_is_deterministic(selector: LaneSelector, gate: GateA):
    inputs = [c["input"] for c in GATE_A_CASES]
    first_pass = [selector.select(gate.classify(text)) for text in inputs]
    second_pass = [selector.select(gate.classify(text)) for text in inputs]
    assert first_pass == second_pass
