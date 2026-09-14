"""
Day 12 Task 15 — the required-coverage audit, plus the five rows no
earlier Day 12 test file owns (the Day 7/Day 8/Day 9/Day 10/Day 11
permanent-gate re-checks).

Required coverage table, each row mapped to the test(s) that prove it —
every row below is a real, behavioral test (never merely "the object
exists"/"the field is present"; Task 15's own rule: "Reject
existence-only tests"):

    Final-response envelope   -> Malformed candidate rejected
      test_day12_gate_d.py::test_missing_response_status_rejected,
      ::test_malformed_citation_structure_rejected,
      ::test_invalid_negative_timing_rejected,
      ::test_missing_control_metadata_rejected,
      ::test_unknown_final_status_rejected
    Gate-D policy load        -> Valid typed/versioned policy
      test_day12_gate_d_policy.py::test_committed_policy_parses,
      ::test_load_real_committed_policy_succeeds
    Invalid budget policy     -> Negative/invalid budget rejected
      test_day12_gate_d_policy.py::test_invalid_or_negative_latency_budget_rejected
    Valid citations           -> Approved evidence citations pass
      test_day12_final_citations.py::test_valid_single_citation_case_names_no_failure_reason
    Forged citation           -> Final response fails
      test_day12_final_citations.py::test_forged_citation_case_reason_is_not_gate_c_validated
    Rejected-evidence citation -> Cannot be reintroduced after Gate-C
      test_day12_final_citations.py::test_gate_c_rejected_item_reintroduced_case_same_reason_as_forged
    Mixed citations            -> One invalid citation fails final response
      test_day12_final_citations.py::test_mixed_valid_invalid_case_names_both_reasons
    Missing required citation  -> Answered result cannot pass
      test_day12_final_citations.py::test_answered_with_zero_citations_fails_when_required
    Insufficient-evidence citations -> Fabricated citations rejected
      test_day12_final_citations.py::test_insufficient_evidence_with_fabricated_citation_still_fails
    Provenance mismatch             -> Citation/source version mismatch rejected
      test_day12_final_citations.py::test_source_version_mismatch_case_reason_is_provenance_mismatch,
      ::test_provenance_mismatch_on_any_single_field_fails
    Contract failure                  -> Gate-D cannot allow invalid contract result
      test_day12_final_quality.py::test_contract_invalid_case_is_reject;
      test_day12_gate_d.py::test_contract_invalid_alone_is_reject
    Semantic failure                    -> Gate-D cannot allow semantic-invalid result
      test_day12_final_quality.py::test_semantic_invalid_case_is_reject;
      test_day12_gate_d.py::test_semantic_invalid_alone_is_reject
    Empty answered output                 -> Rejected
      test_day12_final_quality.py::test_empty_answer_case_is_safe_failure_not_reject
    Output-size limit                       -> Oversized answer fails
      test_day12_final_quality.py::test_oversized_answer_case_is_safe_failure_not_reject,
      ::test_answer_one_over_max_chars_fails
    Allowed disclosure                        -> Safe output passes
      test_day12_disclosure.py::test_safe_output_case_names_no_failure_reason
    Redactable leak                             -> Unredacted protected value rejected
      test_day12_disclosure.py::test_redactable_contact_leak_case_reason
    Denied field leak                             -> Rejected
      test_day12_disclosure.py::test_denied_tax_identifier_case_reason,
      ::test_denied_bank_account_case_reason
    Secret/token leak                               -> Rejected
      test_day12_disclosure.py::test_secret_token_leak_case_reason_and_pattern_name;
      test_day12_api_integration.py::test_gate_d_safe_failure_blocks_a_live_secret_pattern_leak
      (the same leak, proven over a real, live /ask/governed request, not
      only a direct check_final_disclosure()/detect_protected_value_leak()
      call against fixture data)
    Within latency budget                             -> Passes
      test_day12_latency_budget.py::test_within_budget_case_names_no_failure_reason
    Model budget exceeded                               -> Safe failure
      test_day12_latency_budget.py::test_model_budget_exceeded_case_reason
    Total budget exceeded                                 -> Safe failure
      test_day12_latency_budget.py::test_total_budget_exceeded_case_reason
    Threshold edge                                          -> Deterministic expected result
      test_day12_latency_budget.py::test_exact_threshold_case_passes_inclusively
    Safe failure redaction                                    -> Unsafe candidate not echoed
      test_day12_safe_failure.py::test_unsafe_candidate_answer_never_appears_regardless_of_caller_intent,
      ::test_serialized_response_never_contains_unsafe_content
    API integration                                             -> Normal success requires Gate-D allow
      test_day12_api_integration.py::test_gate_d_allows_a_real_grounded_answer_unchanged
    No bypass                                                     -> Gate-D failure candidate never returned
      test_day12_api_integration.py::test_gate_d_safe_failure_blocks_an_oversized_answer,
      ::test_gate_d_reject_via_narrowed_policy_is_a_server_error
    Post-validation immutability                                    -> Returned answer/citations equal approved payload
      test_day12_api_integration.py::test_gate_d_allow_returns_the_exact_validated_object_unchanged,
      ::test_api_mapping_drops_metadata_but_never_alters_citation_content;
      test_day12_gate_d.py::test_final_response_candidate_is_frozen
    Telemetry redaction                                               -> Raw candidate/protected values absent
      test_day12_observability.py::test_no_span_attribute_ever_contains_the_candidate_answer_text,
      ::test_no_span_attribute_ever_contains_a_raw_protected_value_or_matched_secret
    Day 7 gate                                                          -> Permanent evaluation remains green
      this file's own `test_day7_permanent_evaluation_gate_still_passes` below
    Day 8 regression                                                      -> Memory isolation remains green
      this file's own `test_day8_isolation_matrix_still_holds` below
    Day 9 regression                                                        -> Gate-A/lane remains green
      this file's own `test_day9_gate_a_and_lane_selector_still_classify_and_route_correctly` below
    Day 10 regression                                                         -> Gate-B tenant/disclosure remains green
      this file's own `test_day10_gate_b_tenant_and_disclosure_still_behave_correctly` below
    Day 11 regression                                                           -> Gate-C evidence controls remain green
      this file's own `test_day11_gate_c_evidence_controls_still_behave_correctly` below

The last five rows are this file's own job: proving Day 12's final-
response-validation additions have not silently broken any earlier
permanent gate, run for real (not asserted by inference from "the other
tests still pass") — the identical "actual call behavior must prove it"
standard `test_day11_regression.py` already applies, extended one day
further to also re-check Day 11's own Gate-C behavior.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aico.api.identity import TrustedIdentity
from aico.control.disclosure import ProtectedField, apply_disclosure
from aico.control.gate_a import GateA
from aico.control.gate_b import GateB, GateBRequest
from aico.control.gate_c import GateC, GateCRequest, GateCStatus
from aico.control.lane_selector import LaneSelector
from aico.control.models import GateADecision, GateAStatus, GateBDecision, GateBStatus, LaneDecision
from aico.control.ontology import LaneId
from aico.control.ontology_registry import OntologyRegistry
from aico.control.policy_models import DataClassification, PiiCategory
from aico.control.policy_registry import PolicyRegistry
from aico.evals import day07
from aico.evidence.models import EvidencePackage
from aico.evidence.policy import GateCPolicyRegistry
from aico.evidence.source_registry import SourceRegistry
from aico.memory.errors import SessionNotFoundError
from aico.memory.service import MemorySessionService
from aico.memory.store import InMemorySessionStore

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Day 7 gate -> permanent evaluation green
# ---------------------------------------------------------------------------


def test_day7_permanent_evaluation_gate_still_passes(tmp_path):
    """Runs the real Day 7 evaluation CLI (`aico.evals.day07.main`, the
    same entry point `test_day09/10/11_regression.py` already exercise)
    against the real committed dataset/thresholds/baseline/index, in a
    process that has every one of Day 12's final-response-validation
    modules importable and exercised elsewhere in this same test session
    -- proving Day 12's additions have not broken the permanent gate, not
    merely assuming it from "the file still exists". An isolated
    `--artifacts-dir` (this test's own `tmp_path`) means the committed
    `artifacts/day07/*` files are never touched by running this test."""
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


_IDENTITY_A = TrustedIdentity(tenant_id="TENANT-D12-REGRESSION", user_id="USER-D12-REGRESSION-1")
_IDENTITY_A_OTHER_USER = TrustedIdentity(tenant_id="TENANT-D12-REGRESSION", user_id="USER-D12-REGRESSION-2")
_IDENTITY_B_SAME_LOOKING_USER = TrustedIdentity(tenant_id="TENANT-D12-REGRESSION-2", user_id="USER-D12-REGRESSION-1")


def test_day8_isolation_matrix_still_holds():
    """The Day 8 Task 3 case matrix (`test_day08_isolation.py`'s own
    proof, already re-run in `test_day09/10/11_regression.py`), re-run
    again here directly against the real `MemorySessionService`/
    `InMemorySessionStore` in a Day 12 test process -- same-owner access
    allowed; cross-user, cross-tenant, and a guessed/nonexistent session
    id all denied identically (fail-closed, indistinguishable from
    outside); a second session under the same owner never leaks into the
    first. Confirms Day 12's final-response-validation additions (which
    never touch session/memory storage at all -- Gate-D consumes a
    `FinalResponseCandidate`, not session state) have not weakened session
    isolation."""
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


def test_day9_gate_a_and_lane_selector_still_classify_and_route_correctly():
    """Day 9's own classification/routing table (`test_day09_gate_a.py`/
    `test_day09_lane_selector.py`, already re-checked in
    `test_day10/11_regression.py`), re-checked directly here against the
    real committed registry -- exact match, synonym match, ambiguous,
    unsupported, and each governed lane outcome -- proving Gate-D's
    addition (a stage that runs strictly *after* Gate-A/lane selection,
    generation, and every earlier gate) has not altered what either one
    decides."""
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


# ---------------------------------------------------------------------------
# Day 10 regression -> Gate-B tenant/disclosure remains green
# ---------------------------------------------------------------------------


def _matched(intent_id: str) -> GateADecision:
    return GateADecision(
        status=GateAStatus.MATCHED, domain="supplier_governance", intent_id=intent_id,
        reason_code="test_setup", ontology_version="1.0",
    )


def _lane_decision(lane: LaneId, intent_id: str) -> LaneDecision:
    return LaneDecision(lane=lane, intent_id=intent_id, reason_code="test_setup", ontology_version="1.0")


def test_day10_gate_b_tenant_and_disclosure_still_behave_correctly():
    """Day 10's own tenant-isolation (`test_day10_tenant_scope.py`) and
    disclosure (`test_day10_disclosure.py`) behavior, re-checked directly
    here against the real committed Gate-B policy -- same-tenant allow,
    cross-tenant deny (before any protected data access, proven by an
    empty effective scope, not merely a `deny` label), a `public`/non-PII
    field passing through disclosure unchanged, a `contact`-category PII
    field deterministically redacted, and a `personal_identifier`-category
    field denied outright. Confirms Gate-D's addition (which runs strictly
    *after* Gate-B and never widens what Gate-B already granted -- Task 6
    reuses `apply_disclosure()` wholesale, it never reimplements it) has
    not altered Gate-B's own tenant scoping or disclosure decisions."""
    registry = OntologyRegistry.load()
    policy_registry = PolicyRegistry.load(ontology_registry=registry)
    gate_b = GateB(policy_registry, ontology_registry=registry)
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-D12-REGRESSION", roles=("supplier_reader",))

    # Same-tenant -> allow.
    same_tenant = gate_b.authorize(
        identity, _matched("INT-POLICY-QUESTION"), _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION"),
        GateBRequest(data_class=DataClassification.INTERNAL, tenant_ids=("TENANT-A",)),
    )
    assert same_tenant.decision is GateBStatus.ALLOW
    assert same_tenant.effective_tenant_scope == ("TENANT-A",)

    # Cross-tenant -> deny before any protected data access.
    cross_tenant = gate_b.authorize(
        identity, _matched("INT-POLICY-QUESTION"), _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION"),
        GateBRequest(data_class=DataClassification.INTERNAL, tenant_ids=("TENANT-B",)),
    )
    assert cross_tenant.decision is GateBStatus.DENY
    assert cross_tenant.reason_code == "cross_tenant_denied"
    assert cross_tenant.effective_tenant_scope == ()
    assert cross_tenant.disclosure_profile is None

    # Disclosure: allow / redact / deny across three real governed fields.
    profile = policy_registry.get_disclosure_profile(same_tenant.disclosure_profile)
    fields = [
        ProtectedField(name="supplier_name", value="Synthetic Supplier Alpha", data_class=DataClassification.PUBLIC, pii_category=PiiCategory.NONE),
        ProtectedField(name="contact_email", value="alice@example.test", data_class=DataClassification.CONFIDENTIAL, pii_category=PiiCategory.CONTACT),
        ProtectedField(name="tax_identifier", value="SYN-ID-123456", data_class=DataClassification.CONFIDENTIAL, pii_category=PiiCategory.PERSONAL_IDENTIFIER),
    ]
    view = apply_disclosure(same_tenant, profile, fields)
    by_name = {f.name: f for f in view.fields}

    assert by_name["supplier_name"].value == "Synthetic Supplier Alpha"  # public/non-PII: unchanged
    assert by_name["contact_email"].value != "alice@example.test"  # redacted: never the raw value
    assert by_name["contact_email"].value is not None  # ...but a masked value is still shown
    assert by_name["tax_identifier"].value is None  # denied outright: no value at all


# ---------------------------------------------------------------------------
# Day 11 regression -> Gate-C evidence controls remain green
# ---------------------------------------------------------------------------


def _gate_c_allow_decision(*, tenant_ids=("TENANT-A",), data_classes=(DataClassification.INTERNAL,)) -> GateBDecision:
    return GateBDecision(
        decision=GateBStatus.ALLOW,
        effective_tenant_scope=tenant_ids,
        effective_data_classes=data_classes,
        reason_code="test_fixture",
        policy_version="1.0",
        lane=LaneId.RAG,
    )


def _gate_c_item(evidence_id: str, *, source_id: str, facets: list[str], **overrides) -> dict:
    from aico.evidence.provenance import stable_content_hash

    content = overrides.pop("content", f"Synthetic regression evidence text for {evidence_id}.")
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
        "claims": {},
    }
    data.update(overrides)
    return data


def _gate_c_package(items: list[dict], **overrides) -> EvidencePackage:
    data = {
        "request_id": "REQ-D12-REGRESSION",
        "intent_id": "INT-POLICY-QUESTION",
        "lane": "rag",
        "as_of": "2026-09-11T12:00:00+05:00",
        "required_facets": [],
        "items": items,
    }
    data.update(overrides)
    return EvidencePackage.model_validate(data)


def test_day11_gate_c_evidence_controls_still_behave_correctly():
    """Day 11's own `GateC.evaluate()` behavior (`test_day11_gate_c.py`),
    re-checked directly here against the real committed source registry/
    Gate-C policy -- fully valid evidence allows and forwards only the
    validated item, an unknown source rejects, and incomplete evidence
    (a required facet no candidate item covers) reports
    `insufficient_evidence` with the missing facet named. Confirms Gate-D's
    addition (which runs strictly *after* Gate-C, only ever consuming its
    already-decided `validated_evidence_ids` -- never re-deciding evidence
    trust itself) has not altered any of Gate-C's own decisions."""
    ontology_registry = OntologyRegistry.load()
    source_registry = SourceRegistry.load(ontology_registry=ontology_registry)
    policy_registry = GateCPolicyRegistry.load(ontology_registry=ontology_registry, source_registry=source_registry)
    gate_c = GateC(source_registry=source_registry, policy_registry=policy_registry)

    # Fully valid evidence -> allow, forwarding only the validated item.
    valid_package = _gate_c_package(
        [_gate_c_item("E-VALID", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"])]
    )
    allow_decision = gate_c.evaluate(
        gate_b_decision=_gate_c_allow_decision(), package=valid_package, request=GateCRequest(request_kind="payment_terms_only")
    )
    assert allow_decision.decision is GateCStatus.ALLOW
    assert allow_decision.validated_evidence_ids == ("E-VALID",)

    # Unknown source -> reject.
    unknown_source_package = _gate_c_package([_gate_c_item("E-UNKNOWN", source_id="SRC-UNKNOWN", facets=["payment_terms"])])
    reject_decision = gate_c.evaluate(
        gate_b_decision=_gate_c_allow_decision(), package=unknown_source_package, request=GateCRequest(request_kind="payment_terms_only")
    )
    assert reject_decision.decision is GateCStatus.REJECT
    assert "unknown_source" in reject_decision.reason_codes
    assert reject_decision.validated_evidence_ids == ()

    # Incomplete evidence (missing payment_terms) -> insufficient_evidence.
    incomplete_package = _gate_c_package([_gate_c_item("E-PARTIAL", source_id="SRC-POLICY-A", facets=["supplier_identity"])])
    insufficient_decision = gate_c.evaluate(
        gate_b_decision=_gate_c_allow_decision(), package=incomplete_package, request=GateCRequest(request_kind="payment_terms_only")
    )
    assert insufficient_decision.decision is GateCStatus.INSUFFICIENT_EVIDENCE
    assert "payment_terms" in insufficient_decision.missing_facets
