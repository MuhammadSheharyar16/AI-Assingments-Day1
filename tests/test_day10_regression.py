"""
Day 10 Task 16 — the required-coverage audit, plus the three rows no
earlier Day 10 test file owns (the Day 7/Day 8/Day 9 permanent-gate
re-checks) and one dedicated provenance proof.

Required coverage table, each row mapped to the test(s) that prove it —
every row below is a real, behavioral test (never merely "the object
exists"/"the field is present"; Task 16's own rule: "Reject `assert True`
and existence-only tests"):

    Policy load                  -> Valid policy becomes typed objects
      test_day10_policy_registry.py::test_pack_fixture_loads_into_typed_objects,
      ::test_load_reads_the_real_committed_policy
    Duplicate rule                -> Invalid policy rejected
      test_day10_policy_registry.py::test_duplicate_rule_id_rejected
    Unknown intent                 -> Invalid policy reference rejected
      test_day10_policy_registry.py::test_rule_referencing_unknown_ontology_intent_rejected_with_context,
      ::test_load_defaults_to_the_real_committed_ontology_registry
    Unknown lane                    -> Invalid policy reference rejected
      test_day10_policy_registry.py::test_rule_with_ungoverned_lane_string_rejected
    Unknown classification           -> Invalid policy rejected
      test_day10_policy_registry.py::test_rule_with_ungoverned_data_classification_string_rejected,
      ::test_rule_with_data_classification_not_enabled_by_policy_rejected
    Deny by default                   -> No matching rule cannot allow
      test_day10_gate_b.py::test_default_decision_is_deny_never_allow_by_absence_of_a_rule
    Allowed permission                 -> Valid role/intent/lane rule allows
      test_day10_gate_b.py::test_perm_001_allowed_policy_document_read_grants_matching_effective_scope
      (and every PERM-* fixture case)
    Denied permission                   -> Disallowed combination denied
      test_day10_gate_b.py::test_perm_002_denied_structured_lookup_is_rule_denied_not_no_match
    Unknown role                         -> Fails closed
      test_day10_gate_b.py::test_unknown_role_denies, ::test_unknown_role_denies_never_clarifies
    Lane mismatch                         -> Fails closed
      test_day10_gate_b.py::test_permission_case_matches_fixture_expectation[PERM-006]
    Same tenant                            -> Allowed scope can proceed
      test_day10_tenant_scope.py::test_same_trusted_and_requested_tenant_may_continue
    Cross tenant                            -> Denied before data access
      test_day10_tenant_scope.py::test_cross_tenant_denies_before_protected_data_access
    Effective scope                          -> Narrowed intersection, never widened
      test_day10_gate_b.py::test_effective_data_classes_is_always_a_subset_of_the_matched_rules_allowed_classes;
      test_day10_tenant_scope.py::test_effective_tenant_scope_never_includes_a_tenant_the_caller_did_not_ask_for
    Public/internal classification            -> Policy applied correctly
      test_day10_gate_b.py::test_public_and_internal_are_allowed_for_a_policy_document_read
    Restricted classification                  -> Unauthorized disclosure denied
      test_day10_gate_b.py::test_restricted_is_denied_for_every_governed_role
    PII allow                                   -> Allowed category preserved
      test_day10_disclosure.py::test_pii_disclosure_case_matches_end_to_end[PII-001],[PII-005]
    PII redact                                   -> Deterministic redaction
      test_day10_disclosure.py::test_pii_disclosure_case_matches_end_to_end[PII-002],[PII-004];
      ::test_mask_value_is_deterministic_across_repeated_calls
    PII deny                                      -> Disallowed sensitive data not disclosed
      test_day10_disclosure.py::test_pii_disclosure_case_matches_end_to_end[PII-003],[PII-006]
    Safe disclosure                                -> Only approved fields returned
      test_day10_disclosure.py::test_fields_not_allowed_are_omitted_with_no_value,
      ::test_permitted_fields_remain_unchanged
    Clarification boundary                          -> User cannot self-assert role/tenant/clearance
      test_day10_gate_b.py's "Clarification behavior (Task 10)" section (whole)
    No fall-through                                  -> Deny/clarify makes zero protected calls
      test_day10_no_fallthrough.py (whole file); test_day10_control_plane_integration.py's
      deny/clarify cases (zero retrieval/model calls at the service-integration level)
    Memory privilege escalation                       -> Remembered role/tenant text ignored
      test_day10_gate_b.py::test_memory_containing_role_tenant_claims_does_not_change_authorization
    Model privilege escalation                         -> Model cannot widen Gate-B result
      test_day10_gate_b.py::test_model_suggested_widening_is_structurally_unreachable_for_five_of_six_dimensions,
      ::test_model_suggested_data_class_cannot_widen_beyond_the_real_matched_rule
    Provenance                                          -> Policy version/rule/reason present
      this file's own `test_gate_b_decision_provenance_is_present_and_correct_across_outcomes` below
      (every other decision test above also asserts these fields incidentally; this is the one
      dedicated, direct proof)
    Telemetry redaction                                  -> Raw PII/claims absent
      test_day10_observability.py's sanitization section (whole)
    Day 7 gate                                            -> Permanent evaluation remains green
      this file's own `test_day7_permanent_evaluation_gate_still_passes` below
    Day 8 regression                                       -> Memory isolation remains green
      this file's own `test_day8_isolation_matrix_still_holds` below
    Day 9 regression                                        -> Gate-A/lane behavior remains green
      this file's own `test_gate_a_and_lane_selector_still_classify_and_route_correctly`,
      `test_control_plane_service_without_policy_registry_still_behaves_exactly_like_day9` below

The last few rows are this file's own job: proving Day 10's control-plane
additions have not silently broken any earlier permanent gate, run for
real (not asserted by inference from "the other tests still pass") — the
same "actual call behavior must prove it" standard
`test_day10_no_fallthrough.py` already applies to no-fall-through, and
the identical pattern `test_day09_regression.py` already established for
Day 9's own equivalent audit.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aico.api.identity import TrustedIdentity
from aico.control.gate_a import GateA
from aico.control.gate_b import GateB, GateBRequest
from aico.control.lane_selector import LaneSelector
from aico.control.models import GateADecision, GateAStatus, GateBStatus, LaneDecision
from aico.control.ontology import LaneId
from aico.control.ontology_registry import OntologyRegistry
from aico.control.policy_models import DataClassification
from aico.control.policy_registry import PolicyRegistry
from aico.evals import day07
from aico.memory.errors import SessionNotFoundError
from aico.memory.service import MemorySessionService
from aico.memory.store import InMemorySessionStore
from aico.rag.answer_service import GroundedAnswer, GroundedAnswerService
from aico.rag.citation_validator import EvidenceChunk
from aico.rag.control_plane_answer_service import ControlPlaneAnswerService

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Provenance -> policy version/rule/reason present (dedicated, direct proof)
# ---------------------------------------------------------------------------


def test_gate_b_decision_provenance_is_present_and_correct_across_outcomes():
    """`policy_version`/`rule_id`/`reason_code` are populated correctly for
    all three `GateBStatus` outcomes, not merely non-`None` -- the exact
    right value for each, proven against the real committed policy."""
    registry = OntologyRegistry.load()
    policy_registry = PolicyRegistry.load(ontology_registry=registry)
    gate_b = GateB(policy_registry, ontology_registry=registry)

    def matched(intent_id: str) -> GateADecision:
        return GateADecision(
            status=GateAStatus.MATCHED, domain="supplier_governance", intent_id=intent_id,
            reason_code="test_setup", ontology_version="1.0",
        )

    def lane(lane_id: LaneId, intent_id: str) -> LaneDecision:
        return LaneDecision(lane=lane_id, intent_id=intent_id, reason_code="test_setup", ontology_version="1.0")

    # Allow.
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("supplier_reader",))
    allow_decision = gate_b.authorize(
        identity, matched("INT-POLICY-QUESTION"), lane(LaneId.RAG, "INT-POLICY-QUESTION"),
        GateBRequest(data_class=DataClassification.INTERNAL),
    )
    assert allow_decision.decision is GateBStatus.ALLOW
    assert allow_decision.policy_version == policy_registry.policy_version
    assert allow_decision.rule_id == "GB-R001"
    assert allow_decision.reason_code == "rule_allowed"

    # Deny.
    unknown_identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-2", roles=("made_up_role",))
    deny_decision = gate_b.authorize(unknown_identity, matched("INT-POLICY-QUESTION"), lane(LaneId.RAG, "INT-POLICY-QUESTION"))
    assert deny_decision.decision is GateBStatus.DENY
    assert deny_decision.policy_version == policy_registry.policy_version
    assert deny_decision.rule_id is None  # no rule ever matched an unknown role
    assert deny_decision.reason_code == "unknown_role"

    # Clarify.
    compliance_identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-3", roles=("compliance_reviewer",))
    clarify_decision = gate_b.authorize(
        compliance_identity, matched("INT-STRUCTURED-LOOKUP"), lane(LaneId.MODE_B, "INT-STRUCTURED-LOOKUP")
    )
    assert clarify_decision.decision is GateBStatus.CLARIFY
    assert clarify_decision.policy_version == policy_registry.policy_version
    assert clarify_decision.rule_id == "GB-R005"
    assert clarify_decision.reason_code == "data_class_selection_required"


# ---------------------------------------------------------------------------
# Day 7 gate -> permanent evaluation green
# ---------------------------------------------------------------------------


def test_day7_permanent_evaluation_gate_still_passes(tmp_path):
    """Runs the real Day 7 evaluation CLI (`aico.evals.day07.main`, same
    entry point `test_day07_regression_gate.py`/`test_day09_regression.py`
    already exercise) against the real committed dataset/thresholds/
    baseline/index, in a process that has every one of Day 10's
    control-plane modules importable and exercised elsewhere in this same
    test session -- proving Day 10's additions have not broken the
    permanent gate, not merely assuming it from "the file still exists".
    An isolated `--artifacts-dir` (this test's own `tmp_path`) means the
    committed `artifacts/day07/*` files are never touched by running this
    test."""
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


_IDENTITY_A = TrustedIdentity(tenant_id="TENANT-D10-REGRESSION", user_id="USER-D10-REGRESSION-1")
_IDENTITY_A_OTHER_USER = TrustedIdentity(tenant_id="TENANT-D10-REGRESSION", user_id="USER-D10-REGRESSION-2")
_IDENTITY_B_SAME_LOOKING_USER = TrustedIdentity(tenant_id="TENANT-D10-REGRESSION-2", user_id="USER-D10-REGRESSION-1")


def test_day8_isolation_matrix_still_holds():
    """The Day 8 Task 3 case matrix (`test_day08_isolation.py`'s own
    proof, already re-run once in `test_day09_regression.py`), re-run
    again here directly against the real `MemorySessionService`/
    `InMemorySessionStore` in a Day 10 test process -- same-owner access
    allowed; cross-user, cross-tenant, and a guessed/nonexistent session
    id all denied identically (fail-closed, indistinguishable from
    outside); a second session under the same owner never leaks into the
    first. Confirms Day 10's control-plane additions (Gate-B, which reads
    `TrustedIdentity.roles` but never touches session/memory storage at
    all) have not weakened session isolation."""
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
# Day 9 regression -> Gate-A/lane behavior remains green
# ---------------------------------------------------------------------------


def test_gate_a_and_lane_selector_still_classify_and_route_correctly():
    """Day 9's own classification/routing table (`test_day09_gate_a.py`/
    `test_day09_lane_selector.py`), re-checked directly here against the
    real committed registry -- exact match, synonym match, ambiguous,
    unsupported, and each governed lane outcome -- proving Gate-B's
    addition (a stage that runs strictly *after* both of these) has not
    altered what either one decides."""
    registry = OntologyRegistry.load()
    gate_a = GateA(registry)
    lane_selector = LaneSelector(registry)

    exact = gate_a.classify("What are the payment terms?")
    assert exact.status is GateAStatus.MATCHED
    assert exact.intent_id == "INT-POLICY-QUESTION"
    assert lane_selector.select(exact).lane is LaneId.RAG

    synonym = gate_a.classify("What is the vendor payment window?")
    assert synonym.status is GateAStatus.MATCHED
    assert synonym.intent_id == "INT-POLICY-QUESTION"

    structured = gate_a.classify("List active contracts.")
    assert structured.status is GateAStatus.MATCHED
    assert structured.intent_id == "INT-STRUCTURED-LOOKUP"
    assert lane_selector.select(structured).lane is LaneId.MODE_B

    ambiguous = gate_a.classify("Show me the supplier information.")
    assert ambiguous.status is GateAStatus.AMBIGUOUS
    assert lane_selector.select(ambiguous).lane is LaneId.CLARIFY

    unsupported = gate_a.classify("What is tomorrow's weather?")
    assert unsupported.status is GateAStatus.UNSUPPORTED
    assert lane_selector.select(unsupported).lane is LaneId.BLOCK

    help_case = gate_a.classify("What can you help with?")
    assert help_case.status is GateAStatus.MATCHED
    assert help_case.intent_id == "INT-HELP"
    assert lane_selector.select(help_case).lane is LaneId.SAFE_FAST_PATH


def test_control_plane_service_without_policy_registry_still_behaves_exactly_like_day9():
    """A `ControlPlaneAnswerService` built the Day 9 way (no
    `policy_registry`) reaches the `rag` lane and the real retrieval/
    Model-Gateway fakes exactly as it did before Gate-B existed -- `gate_b`
    is `None`, no identity is required, and the returned type is Day 5's
    own `GroundedAnswer`, unwrapped. `test_day09_control_plane_integration.py`
    (and every other Day 9 test file) already prove this at full scale by
    remaining 100% green; this is the one direct, focused re-check living
    in this audit file."""
    registry = OntologyRegistry.load()

    class _CountingGateway:
        call_count = 0

        def chat(self, request):
            from aico.platform.model_gateway import CallMetadata, ChatResult

            self.call_count += 1
            return ChatResult(
                content='{"schema_version": "1.0", "status": "answered", "answer": "Payment terms are net 30 days.", '
                '"citations": [{"chunk_id": "C1", "source_file": "DOC-001.md"}], "confidence_label": "high"}',
                metadata=CallMetadata(
                    operation="chat", model_alias="fake", latency_ms=1.0, retry_count=0,
                    token_usage=None, budget_status="within_budget",
                ),
            )

    def retriever(query: str) -> list[EvidenceChunk]:
        return [EvidenceChunk(chunk_id="C1", source_file="DOC-001.md", text="Payment terms are net 30 days.")]

    gateway = _CountingGateway()
    rag_service = GroundedAnswerService(gateway=gateway, retriever=retriever)
    service = ControlPlaneAnswerService(registry=registry, rag_service=rag_service)  # no policy_registry

    assert service.gate_b is None

    result = service.answer("What are the payment terms?")  # no identity needed at all

    assert isinstance(result, GroundedAnswer)
    assert result.answer == "Payment terms are net 30 days."
    assert gateway.call_count == 1


# ---------------------------------------------------------------------------
# Existence-only-test guard: a self-check on this project's own Day 10
# suite, not just a claim in prose
# ---------------------------------------------------------------------------


def test_gate_b_decision_typed_shape_check_is_paired_with_a_behavioral_one():
    """A concrete demonstration, not just a rule stated in this file's
    docstring: a `GateBDecision` for a known input is checked both for
    its typed shape *and* for the correct governed outcome together --
    "the object is the right type" alone would pass even for a wrong
    answer, so every decision test in this suite (see the coverage map
    above) asserts the actual `decision`/`rule_id`/`reason_code` value,
    never only `isinstance`."""
    from aico.control.models import GateBDecision

    registry = OntologyRegistry.load()
    policy_registry = PolicyRegistry.load(ontology_registry=registry)
    gate_b = GateB(policy_registry, ontology_registry=registry)

    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("supplier_reader",))
    gate_a_decision = GateADecision(
        status=GateAStatus.MATCHED, domain="supplier_governance", intent_id="INT-POLICY-QUESTION",
        reason_code="test_setup", ontology_version="1.0",
    )
    lane_decision = LaneDecision(lane=LaneId.RAG, intent_id="INT-POLICY-QUESTION", reason_code="test_setup", ontology_version="1.0")

    decision = gate_b.authorize(identity, gate_a_decision, lane_decision, GateBRequest(data_class=DataClassification.INTERNAL))

    assert isinstance(decision, GateBDecision)  # shape
    assert decision.decision is GateBStatus.ALLOW  # behavior
    assert decision.rule_id == "GB-R001"  # behavior
