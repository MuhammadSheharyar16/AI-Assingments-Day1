"""
Day 11 Task 16 — the required-coverage audit, plus the four rows no
earlier Day 11 test file owns (the Day 7/Day 8/Day 9/Day 10 permanent-gate
re-checks).

Required coverage table, each row mapped to the test(s) that prove it —
every row below is a real, behavioral test (never merely "the object
exists"/"the field is present"; Task 16's own rule: "Reject
existence-only tests"):

    Evidence envelope         -> Malformed evidence rejected
      test_day11_evidence_envelope.py (whole file)
    Source registry load      -> Valid registry becomes typed objects
      test_day11_source_registry.py::test_pack_fixture_loads_into_typed_objects,
      ::test_load_reads_the_real_committed_registry
    Duplicate source          -> Invalid registry rejected
      test_day11_source_registry.py::test_duplicate_source_id_rejected
    Unknown source             -> Gate-C rejects
      test_day11_gate_c.py::test_reject_unknown_source
    Disabled source              -> Gate-C rejects
      test_day11_gate_c.py::test_reject_disabled_source
    Intent/source mismatch        -> Unsupported source use rejected
      test_day11_gate_c.py::test_reject_source_intent_not_supported,
      ::test_reject_source_type_not_allowed_for_this_rule
    Valid provenance                -> Traceable item accepted
      test_day11_provenance.py::test_valid_item_passes_every_check,
      ::test_gc001_valid_payment_evidence_passes_provenance
    Hash mismatch                     -> Mutated evidence rejected
      test_day11_provenance.py::test_stale_hash_after_content_mutation_rejected;
      test_day11_gate_c.py::test_reject_content_hash_mismatch
    Version mismatch                    -> Wrong source version rejected
      test_day11_provenance.py::test_conflicting_source_versions_for_the_same_source_rejects_both
    Tenant mismatch                       -> Gate-B scope preserved
      test_day11_provenance.py::test_tenant_outside_gate_b_scope_rejected;
      test_day11_gate_c.py::test_reject_cross_tenant_evidence,
      ::test_evidence_above_permitted_classification_rejected (Task 12 section)
    Classification mismatch                 -> Gate-B scope preserved
      test_day11_provenance.py::test_data_classification_outside_gate_b_scope_rejected;
      test_day11_gate_c.py::test_evidence_above_permitted_classification_rejected
    Fresh evidence                            -> Passes freshness
      test_day11_freshness.py::test_fresh_evidence, and the real
      `freshness_cases` fixture parametrization
    Threshold edge                              -> Deterministic documented behavior
      test_day11_freshness.py::test_exactly_at_threshold_evidence_is_fresh
    Stale evidence                                -> Does not pass
      test_day11_freshness.py::test_stale_evidence
    Missing timestamp                               -> Fails documented freshness rule
      test_day11_freshness.py::test_missing_freshness_timestamp
    Complete facets                                   -> Completeness passes
      test_day11_completeness.py::test_all_required_facets_covered_passes
    Missing facet                                       -> Insufficient evidence
      test_day11_completeness.py::test_one_required_facet_missing_is_insufficient
    Duplicate facet evidence                              -> Does not fake completeness
      test_day11_completeness.py::test_duplicate_coverage_of_the_same_facet_is_not_double_counted
    Invalid item excluded                                   -> Invalid evidence cannot satisfy facet
      test_day11_completeness.py::test_invalid_item_excluded_from_coverage
    Non-conflicting evidence                                  -> Conflict check passes
      test_day11_conflicts.py::test_non_conflicting_evidence_passes,
      ::test_exact_duplicate_supporting_evidence_is_not_a_conflict
    Same-authority conflict                                     -> Conflict detected
      test_day11_conflicts.py::test_contradictory_same_authority_evidence_is_detected
    Governed precedence                                           -> Resolution only when policy allows
      test_day11_conflicts.py::test_governed_authority_precedence_resolves_a_conflict,
      ::test_authority_precedence_not_applied_for_an_unrecognized_policy
    Gate-C allow                                                    -> Only validated evidence forwarded
      test_day11_gate_c.py::test_allow_decision, ::test_filtering_five_retrieved_two_invalid_allow_using_three;
      test_day11_no_fallthrough.py::test_rejected_evidence_content_never_reaches_the_prompt_even_on_allow
    Gate-C insufficient                                               -> Model calls = 0
      test_day11_no_fallthrough.py::test_insufficient_evidence_makes_zero_generation_calls
    Gate-C reject                                                       -> Model calls = 0
      test_day11_no_fallthrough.py::test_reject_unknown_source_makes_zero_generation_calls (+ siblings)
    Unresolved conflict                                                   -> Model calls = 0
      test_day11_no_fallthrough.py::test_unresolved_conflict_makes_zero_generation_calls
    Citation boundary                                                       -> Existing citation validation still enforced
      test_day11_control_plane_integration.py::test_forged_citation_still_fails_after_gate_c_allows,
      ::test_a_citation_naming_a_gate_c_rejected_chunk_also_fails
    Telemetry redaction                                                       -> Raw evidence/PII absent
      test_day11_observability.py::test_no_span_attribute_ever_contains_raw_evidence_content_or_claims
    Day 7 gate                                                                  -> Permanent evaluation remains green
      this file's own `test_day7_permanent_evaluation_gate_still_passes` below
    Day 8 regression                                                              -> Memory isolation remains green
      this file's own `test_day8_isolation_matrix_still_holds` below
    Day 9 regression                                                                -> Gate-A/lane remains green
      this file's own `test_day9_gate_a_and_lane_selector_still_classify_and_route_correctly` below
    Day 10 regression                                                                 -> Gate-B tenant/disclosure remains green
      this file's own `test_day10_gate_b_tenant_and_disclosure_still_behave_correctly` below

The last four rows are this file's own job: proving Day 11's evidence-
quality additions have not silently broken any earlier permanent gate,
run for real (not asserted by inference from "the other tests still
pass") — the same "actual call behavior must prove it" standard
`test_day11_no_fallthrough.py` already applies to no-generation-fall-
through, and the identical pattern `test_day09_regression.py`/
`test_day10_regression.py` already established for each earlier day's own
equivalent audit.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aico.api.identity import TrustedIdentity
from aico.control.disclosure import ProtectedField, apply_disclosure
from aico.control.gate_a import GateA
from aico.control.gate_b import GateB, GateBRequest
from aico.control.lane_selector import LaneSelector
from aico.control.models import GateADecision, GateAStatus, GateBStatus, LaneDecision
from aico.control.ontology import LaneId
from aico.control.ontology_registry import OntologyRegistry
from aico.control.policy_models import DataClassification, PiiCategory
from aico.control.policy_registry import PolicyRegistry
from aico.evals import day07
from aico.memory.errors import SessionNotFoundError
from aico.memory.service import MemorySessionService
from aico.memory.store import InMemorySessionStore

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Day 7 gate -> permanent evaluation green
# ---------------------------------------------------------------------------


def test_day7_permanent_evaluation_gate_still_passes(tmp_path):
    """Runs the real Day 7 evaluation CLI (`aico.evals.day07.main`, the
    same entry point `test_day07_regression_gate.py`/`test_day09_
    regression.py`/`test_day10_regression.py` already exercise) against
    the real committed dataset/thresholds/baseline/index, in a process
    that has every one of Day 11's evidence-quality modules importable and
    exercised elsewhere in this same test session -- proving Day 11's
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


_IDENTITY_A = TrustedIdentity(tenant_id="TENANT-D11-REGRESSION", user_id="USER-D11-REGRESSION-1")
_IDENTITY_A_OTHER_USER = TrustedIdentity(tenant_id="TENANT-D11-REGRESSION", user_id="USER-D11-REGRESSION-2")
_IDENTITY_B_SAME_LOOKING_USER = TrustedIdentity(tenant_id="TENANT-D11-REGRESSION-2", user_id="USER-D11-REGRESSION-1")


def test_day8_isolation_matrix_still_holds():
    """The Day 8 Task 3 case matrix (`test_day08_isolation.py`'s own
    proof, already re-run in `test_day09_regression.py`/`test_day10_
    regression.py`), re-run again here directly against the real
    `MemorySessionService`/`InMemorySessionStore` in a Day 11 test process
    -- same-owner access allowed; cross-user, cross-tenant, and a guessed/
    nonexistent session id all denied identically (fail-closed,
    indistinguishable from outside); a second session under the same
    owner never leaks into the first. Confirms Day 11's evidence-quality
    additions (which never touch session/memory storage at all -- Gate-C
    consumes an `EvidencePackage`, not session state) have not weakened
    session isolation."""
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
    `test_day09_lane_selector.py`, already re-checked in `test_day10_
    regression.py`), re-checked directly here against the real committed
    registry -- exact match, synonym match, ambiguous, unsupported, and
    each governed lane outcome -- proving Gate-C's addition (a stage that
    runs strictly *after* Gate-A/lane selection, and only inside the
    `rag` lane's own branch) has not altered what either one decides."""
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
    field denied outright. Confirms Gate-C's addition (which runs strictly
    *after* Gate-B, only inside the `rag` lane, and never widens what
    Gate-B already granted -- Task 12) has not altered Gate-B's own tenant
    scoping or disclosure decisions."""
    registry = OntologyRegistry.load()
    policy_registry = PolicyRegistry.load(ontology_registry=registry)
    gate_b = GateB(policy_registry, ontology_registry=registry)
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-D11-REGRESSION", roles=("supplier_reader",))

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
