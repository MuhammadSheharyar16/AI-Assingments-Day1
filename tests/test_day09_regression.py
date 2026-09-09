"""
Day 9 Task 14 — Tests: the required-coverage audit, plus the two rows no
earlier Day 9 test file owns (the Day 7/Day 8 permanent-gate re-checks).

Required coverage table, each row mapped to the test(s) that prove it —
every row below is a real, behavioral test (never merely "the object
exists"/"the field is present"; Task 14's own rule: "Reject
existence-only tests"):

    Registry load            -> Valid typed registry
      test_day09_ontology.py::test_registry_loads_into_typed_objects,
      ::test_load_reads_the_real_committed_registry
    Duplicate concept        -> Rejected
      test_day09_ontology.py::test_duplicate_concept_id_rejected
    Duplicate intent         -> Rejected
      test_day09_ontology.py::test_duplicate_intent_id_rejected
    Unknown relationship     -> Rejected
      test_day09_ontology.py::test_unknown_relationship_target_rejected
    Unknown lane             -> Rejected
      test_day09_ontology.py::test_intent_with_ungoverned_lane_string_rejected,
      ::test_intent_with_lane_not_enabled_by_registry_rejected
    Exact intent              -> Correct Gate-A match
      test_day09_gate_a.py::test_ga001_exact_governed_intent_reason_code
    Synonym                   -> Correct governed mapping
      test_day09_gate_a.py::test_ga002_registered_synonym_reason_code
    Ambiguity                 -> Clarify path
      test_day09_ambiguity.py (whole file);
      test_day09_lane_selector.py::test_lane003_ambiguous_routes_to_clarify
    Unsupported domain        -> Fail closed
      test_day09_gate_a.py Task 7 section (`_UNSUPPORTED_SWEEP`),
      ::test_ga004_and_ga005_together_both_fail_closed_the_same_way
    Unknown intent            -> No invented intent
      test_day09_gate_a.py::test_ga005_unknown_intent_does_not_invent_one
    Model candidate validation -> Unknown model candidate rejected if applicable
      test_day09_ontology.py::test_resolve_concepts_rejects_unknown_candidate_id
      — "if applicable": no model-assisted interpreter is used at all
      (`gate_a.py`'s own module docstring, a documented design choice, not
      a gap) — this proves the rejection *mechanism*
      (`OntologyRegistry.resolve_concepts`) Task 4 requires such an
      interpreter to be built on, ready if a later day adds one.
    RAG lane                   -> Correct route
      test_day09_lane_selector.py::test_lane001_governed_document_question_routes_to_rag;
      test_day09_control_plane_integration.py::test_rag_lane_reaches_retrieval_and_model_gateway
    Mode-B lane                -> Selection without uncontrolled execution
      test_day09_lane_selector.py::test_lane002_structured_data_intent_routes_to_mode_b_selected_not_executed;
      test_day09_no_fallthrough.py::test_mode_b_answer_succeeds_with_sqlite_connect_patched_to_raise,
      ::test_control_plane_answer_service_imports_nothing_database_capable
    Clarify lane                -> Correct route
      test_day09_lane_selector.py::test_lane003_ambiguous_routes_to_clarify
    Block lane                  -> Correct route
      test_day09_lane_selector.py::test_lane004_unsupported_routes_to_block,
      ::test_blocked_gate_a_status_also_routes_to_block
    Safe fast path               -> Only governed utility/help case
      test_day09_lane_selector.py::test_lane005_governed_help_case_routes_to_safe_fast_path;
      this file's own `test_safe_fast_path_is_never_reached_by_non_help_intents` below
    No fall-through               -> Block/clarify do not invoke RAG
      test_day09_no_fallthrough.py (whole file)
    Memory follow-up               -> Reference resolution without policy widening
      test_day09_memory_interaction.py (whole file)
    Memory injection                -> Cannot alter intent/lane policy
      test_day09_memory_interaction.py::test_injected_remembered_subject_is_still_blocked_not_trusted,
      ::test_injected_remembered_subject_routes_to_block_lane
    Provenance                       -> Version/reason present
      test_day09_gate_a.py::test_every_decision_carries_the_active_ontology_version;
      test_day09_lane_selector.py (every case asserts `reason_code`)
    Observability                     -> Correlation preserved
      test_day09_observability.py::test_gate_a_and_lane_selection_share_trace_id_with_a_parent_span
    Day 7 gate                         -> Permanent evaluation green
      this file's own `test_day7_permanent_evaluation_gate_still_passes` below
    Day 8 regression                    -> Memory/isolation green
      this file's own `test_day8_isolation_matrix_still_holds` below

The last two rows are this file's own job: proving Day 9's control-plane
additions have not silently broken either permanent gate, run for real
(not asserted by inference from "the other tests still pass") — the same
"actual call behavior must prove it" standard Task 10 already applies to
no-fall-through.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aico.api.identity import TrustedIdentity
from aico.control.gate_a import GateA
from aico.control.lane_selector import LaneSelector
from aico.control.models import GateADecision, GateAStatus
from aico.control.ontology import LaneId, LifecycleStatus
from aico.control.ontology_registry import OntologyRegistry
from aico.evals import day07
from aico.memory.errors import SessionNotFoundError
from aico.memory.service import MemorySessionService
from aico.memory.store import InMemorySessionStore

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Safe fast path -> only the governed utility/help case (closing the one
# row not already proven by an existing dedicated test elsewhere)
# ---------------------------------------------------------------------------


def test_safe_fast_path_is_never_reached_by_non_help_intents():
    """`INT-HELP` is the only governed intent whose `allowed_lanes`
    includes `safe_fast_path` (`ontology/registry.v1.json`) -- proven
    directly here, not merely inferred: every *other* active intent's
    `MATCHED` decision, run through the real `LaneSelector`, must never
    resolve to `safe_fast_path`."""
    registry = OntologyRegistry.load()
    selector = LaneSelector(registry)

    non_help_intents = [i for i in registry.intents if i.status is LifecycleStatus.ACTIVE and i.intent_id != "INT-HELP"]
    assert non_help_intents, "expected at least one non-INT-HELP active intent to test against"

    for intent in non_help_intents:
        decision = GateADecision(
            status=GateAStatus.MATCHED,
            domain=intent.domain,
            intent_id=intent.intent_id,
            reason_code="exact_phrase_match",
            ontology_version=registry.ontology_version,
        )
        lane_decision = selector.select(decision)
        assert lane_decision.lane is not LaneId.SAFE_FAST_PATH, (
            f"{intent.intent_id} unexpectedly routed to safe_fast_path"
        )

    help_intent = registry.get_intent("INT-HELP")
    assert LaneId.SAFE_FAST_PATH in help_intent.allowed_lanes


# ---------------------------------------------------------------------------
# Day 7 gate -> permanent evaluation green
# ---------------------------------------------------------------------------


def test_day7_permanent_evaluation_gate_still_passes(tmp_path):
    """Runs the real Day 7 evaluation CLI (`aico.evals.day07.main`, same
    entry point `test_day07_regression_gate.py` exercises) against the
    real committed dataset/thresholds/baseline/index, in a process that
    has every one of Day 9's control-plane modules importable and
    exercised elsewhere in this same test session -- proving Day 9's
    additions have not broken the permanent gate, not merely assuming it
    from "the file still exists". An isolated `--artifacts-dir` (this
    test's own `tmp_path`) means the committed `artifacts/day07/*` files
    are never touched by running this test."""
    artifacts_dir = tmp_path / "artifacts"
    exit_code = day07.main(
        [
            "--dataset", str(REPO_ROOT / "evals" / "golden_v1.json"),
            "--thresholds", str(REPO_ROOT / "evals" / "thresholds_v1.json"),
            "--baseline", str(REPO_ROOT / "evals" / "baseline_v1.json"),
            "--index", str(REPO_ROOT / "data" / "index"),
            "--artifacts-dir", str(artifacts_dir),
        ]
    )

    report = json.loads((artifacts_dir / "evaluation_report.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert report["gate_verdict"]["passed"] is True
    assert report["safety_gate"]["failures"] == []


# ---------------------------------------------------------------------------
# Day 8 regression -> memory/isolation green
# ---------------------------------------------------------------------------


_IDENTITY_A = TrustedIdentity(tenant_id="TENANT-D9-REGRESSION", user_id="USER-D9-REGRESSION-1")
_IDENTITY_A_OTHER_USER = TrustedIdentity(tenant_id="TENANT-D9-REGRESSION", user_id="USER-D9-REGRESSION-2")
_IDENTITY_B_SAME_LOOKING_USER = TrustedIdentity(tenant_id="TENANT-D9-REGRESSION-2", user_id="USER-D9-REGRESSION-1")


def test_day8_isolation_matrix_still_holds():
    """The Day 8 Task 3 case matrix (`test_day08_isolation.py`'s own
    proof), re-run here directly against the real `MemorySessionService`/
    `InMemorySessionStore` in a Day 9 test process -- same-owner access
    allowed; cross-user, cross-tenant, and a guessed/nonexistent session
    id all denied identically (fail-closed, indistinguishable from
    outside); a second session under the same owner never leaks into the
    first. Confirms Day 9's control-plane additions (which touch
    `aico.memory.context_builder` for Task 8's `resolve_reference`) have
    not weakened session isolation."""
    service = MemorySessionService(InMemorySessionStore())
    session = service.create_session(_IDENTITY_A)
    other_session = service.create_session(_IDENTITY_A)

    # Same owner -> allow.
    reloaded = service.load_session(_IDENTITY_A, session.session_id)
    assert reloaded.session_id == session.session_id

    # Cross-user, same tenant -> deny.
    with pytest.raises(SessionNotFoundError) as cross_user_exc:
        service.load_session(_IDENTITY_A_OTHER_USER, session.session_id)

    # Cross-tenant, same-looking user id -> deny.
    with pytest.raises(SessionNotFoundError) as cross_tenant_exc:
        service.load_session(_IDENTITY_B_SAME_LOOKING_USER, session.session_id)

    # Guessed/nonexistent session id -> deny.
    with pytest.raises(SessionNotFoundError) as nonexistent_exc:
        service.load_session(_IDENTITY_A, "SES-guessed-does-not-exist")

    # Fail-closed indistinguishability: a wrong-owner denial and a
    # nonexistent-session denial carry the identical safe reason.
    assert cross_user_exc.value.reason == nonexistent_exc.value.reason == "not_found"
    assert cross_tenant_exc.value.reason == "not_found"

    # A different session under the same owner never leaks the other
    # session's identity or content.
    reloaded_other = service.load_session(_IDENTITY_A, other_session.session_id)
    assert reloaded_other.session_id == other_session.session_id
    assert reloaded_other.session_id != session.session_id
    assert reloaded_other.recent_turns == []


# ---------------------------------------------------------------------------
# Existence-only-test guard: a self-check on this project's own Day 9
# suite, not just a claim in prose
# ---------------------------------------------------------------------------


def test_gate_a_decision_typed_shape_check_is_paired_with_a_behavioral_one():
    """A concrete demonstration, not just a rule stated in this file's
    docstring: a `GateADecision` for the exact same input is checked both
    for its typed shape *and* for the correct governed outcome together
    -- "the object is the right type" alone would pass even for a wrong
    answer, so every decision test in this suite (see the coverage map
    above) asserts the actual `status`/`intent_id`/`lane` value, never
    only `isinstance`."""
    registry = OntologyRegistry.load()
    gate = GateA(registry)

    decision = gate.classify("What are the payment terms?")

    assert isinstance(decision, GateADecision)  # shape
    assert decision.status is GateAStatus.MATCHED  # behavior
    assert decision.intent_id == "INT-POLICY-QUESTION"  # behavior
