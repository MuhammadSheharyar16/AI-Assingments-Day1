"""
Day 13 Task 15 — the required-coverage audit, plus the two rows no
earlier Day 13 test file owns (the Day 7 permanent gate and the Day 8-12
regression re-checks).

Required coverage table (`Day 13 Task.pdf`, TASK 15), each row mapped to
the real, behavioral test(s) that prove it — never merely "the object
exists"/"the field is present" (Task 15's own rule: "Reject
existence-only tests"):

    Registry load                 -> Valid registry becomes typed objects
      test_day13_tool_registry.py::TestPackFixtureParses::
      test_valid_registry_becomes_typed_objects
    Duplicate tool/version         -> Rejected
      test_day13_tool_registry.py::test_duplicate_tool_version_rejected
    Invalid version                -> Rejected
      test_day13_tool_registry.py::test_invalid_semver_rejected
    Unknown tool/version           -> Fail closed
      test_day13_tool_registry.py::TestToolRegistryLookup::
      test_get_tool_unknown_tool_id_raises_tool_not_found,
      ::test_get_tool_unknown_version_raises_version_not_found;
      test_day13_executor.py::test_unknown_version_of_a_known_tool_is_version_not_found
    Disabled tool                  -> Transport calls = 0
      test_day13_executor.py::test_disabled_tool_case_is_tool_disabled_with_zero_transport_calls;
      test_day13_transport_failures.py::TestTransportFailureCasesFixture::test_tr13_005_disabled_tool_never_retries_or_calls_transport
    Default deny                   -> Missing rule/permission cannot execute
      test_day13_tool_policy.py::test_no_matching_rule_denies,
      ::test_empty_policy_denies_every_request
    Trusted permission             -> Required permission enforced
      test_day13_tool_policy.py::test_missing_trusted_permission_denies
    Argument privilege injection   -> Args cannot grant tenant/permission
      test_day13_tool_registry.py::TestToolExecutionRequestTrustRules (Task 3);
      test_day13_tool_policy.py::test_argument_permission_does_not_substitute_for_trusted_permission;
      test_day13_no_direct_execution.py::test_model_smuggled_privilege_keys_in_arguments_are_still_inert
    Valid input                    -> Reaches transport
      test_day13_input_schema.py::TestValidInput;
      test_day13_mcp_gateway.py::test_allowed_call_reaches_transport_once_and_returns_success
    Missing/wrong/extra input      -> Transport calls = 0
      test_day13_input_schema.py::TestRequiredCases;
      test_day13_executor.py::test_missing_required_input_is_input_invalid_with_zero_transport_calls
    Timeout                        -> Typed bounded failure
      test_day13_transport_failures.py::TestTimeoutEnforcement
    Cancellation                   -> In-flight fake work stops
      test_day13_transport_failures.py::TestCancellationPropagation;
      test_day13_mcp_gateway.py::test_cancelled_step_normalizes_to_cancelled_category
    Retry then success             -> Only safe/idempotent tool retries
      test_day13_transport_failures.py::TestRetrySafety::
      test_transient_failure_then_success_retries_and_succeeds
    Retry exhaustion                -> Bounded failure
      test_day13_transport_failures.py::TestRetrySafety::test_retry_count_is_bounded_to_max_attempts
    Unsafe retry prevention          -> Disabled/unsafe tool does not retry
      test_day13_transport_failures.py::TestRetrySafety::
      test_unsafe_tool_is_denied_by_policy_before_the_retry_loop_is_ever_reached,
      ::test_dispatch_with_retry_itself_refuses_to_retry_a_non_idempotent_tool,
      ::test_disabled_tool_makes_zero_retry_attempts
    Transport normalization          -> Raw exception normalized
      test_day13_mcp_gateway.py::test_unnormalized_transport_error_is_caught_and_normalized;
      test_day13_transport_failures.py::TestNormalizedErrorTaxonomy::
      test_exotic_unanticipated_transport_exception_is_normalized_not_raised
    Valid output                      -> Typed success
      test_day13_output_schema.py::TestValidOutput
    Invalid output                     -> Never returned as success
      test_day13_output_schema.py::TestInvalidOutputNeverReturnedAsSuccessEndToEnd;
      test_day13_executor.py::test_invalid_output_is_output_invalid_never_returned_as_success
    Direct transport isolation           -> Model/application cannot bypass gateway
      test_day13_no_direct_execution.py (whole file — import-graph layering,
      no `.execute(` call site outside `aico/tools/`, no bare-string
      entrypoint, and the behavioral "model proposes a tool call" proof)
    Telemetry redaction                    -> Raw payload/secrets absent
      test_day13_observability.py::TestNoRawContentLeak
    Correlation propagation                  -> Request context preserved
      test_day13_observability.py::test_request_id_and_correlation_id_link_only_their_own_event
    Day 7 gate                                 -> Permanent evaluation remains green
      this file's own `test_day7_permanent_evaluation_gate_still_passes` below
    Day 8-12 regression                          -> Earlier critical controls remain green
      this file's own `test_day8_isolation_matrix_still_holds` /
      `test_day9_gate_a_and_lane_selector_still_classify_and_route_correctly` /
      `test_day10_gate_b_tenant_and_disclosure_still_behave_correctly` /
      `test_day11_gate_c_evidence_controls_still_behave_correctly` /
      `test_day12_gate_d_still_validates_final_responses_correctly` below

The last six rows are this file's own job: proving Day 13's governed tool
boundary (an entirely new package, `aico.tools`, that no earlier day's
code imports at all — `test_day13_no_direct_execution.py`'s own proof)
has not silently broken any earlier permanent gate, run for real (not
asserted by inference from "the other tests still pass") — the identical
"actual call behavior must prove it" standard `test_day12_regression.py`
already applies, extended one day further to also re-check Day 12's own
Gate-D behavior.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aico.api.identity import TrustedIdentity
from aico.control.disclosure import ProtectedField, apply_disclosure
from aico.control.final_response import parse_final_response_candidate
from aico.control.gate_a import GateA
from aico.control.gate_b import GateB, GateBRequest
from aico.control.gate_c import GateC, GateCRequest, GateCStatus
from aico.control.gate_d import GateCEvidenceRecord, GateD, GateDStatus
from aico.control.lane_selector import LaneSelector
from aico.control.models import GateADecision, GateAStatus, GateBDecision, GateBStatus, LaneDecision
from aico.control.ontology import LaneId
from aico.control.ontology_registry import OntologyRegistry
from aico.control.policy_models import DataClassification, PiiCategory
from aico.control.policy_registry import GateDPolicyRegistry, PolicyRegistry
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
    same entry point every earlier day's own regression file already
    exercises) against the real committed dataset/thresholds/baseline/
    index, in a process that has every one of Day 13's new `aico.tools`
    modules importable and exercised elsewhere in this same test session
    -- proving Day 13's additions have not broken the permanent gate, not
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


_IDENTITY_A = TrustedIdentity(tenant_id="TENANT-D13-REGRESSION", user_id="USER-D13-REGRESSION-1")
_IDENTITY_A_OTHER_USER = TrustedIdentity(tenant_id="TENANT-D13-REGRESSION", user_id="USER-D13-REGRESSION-2")
_IDENTITY_B_SAME_LOOKING_USER = TrustedIdentity(tenant_id="TENANT-D13-REGRESSION-2", user_id="USER-D13-REGRESSION-1")


def test_day8_isolation_matrix_still_holds():
    """The Day 8 Task 3 case matrix (`test_day08_isolation.py`'s own
    proof, already re-run in every intervening day's own regression file),
    re-run again here directly against the real `MemorySessionService`/
    `InMemorySessionStore` in a Day 13 test process -- same-owner access
    allowed; cross-user, cross-tenant, and a guessed/nonexistent session
    id all denied identically (fail-closed, indistinguishable from
    outside); a second session under the same owner never leaks into the
    first. Confirms Day 13's new governed tool boundary (which never
    touches session/memory storage at all) has not weakened session
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
    `test_day09_lane_selector.py`), re-checked directly here against the
    real committed registry -- exact match, synonym match, ambiguous,
    unsupported, and each governed lane outcome -- proving Day 13's new
    tool boundary (which Gate-A/lane selection never imports or calls at
    all) has not altered what either one decides."""
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
    field denied outright. Confirms Day 13's new tool boundary has not
    altered Gate-B's own tenant scoping or disclosure decisions."""
    registry = OntologyRegistry.load()
    policy_registry = PolicyRegistry.load(ontology_registry=registry)
    gate_b = GateB(policy_registry, ontology_registry=registry)
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-D13-REGRESSION", roles=("supplier_reader",))

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
        "request_id": "REQ-D13-REGRESSION",
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
    `insufficient_evidence` with the missing facet named. Confirms Day
    13's new tool boundary has not altered any of Gate-C's own decisions."""
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


# ---------------------------------------------------------------------------
# Day 12 regression -> Gate-D final-response validation remains green
# ---------------------------------------------------------------------------


def _final_candidate(**overrides: object):
    payload: dict = {
        "request_id": "REQ-D13-REGRESSION",
        "correlation_id": "CORR-D13-REGRESSION",
        "candidate_status": "answered",
        "candidate_answer": "Synthetic Supplier Alpha uses net 30 payment terms.",
        "candidate_citations": [],
        "gate_c_validated_evidence_ids": [],
        "gate_b_disclosure_profile": "policy_reader",
        "started_at": "2026-09-15T12:00:00+00:00",
        "elapsed_ms": 1200,
        "model_latency_ms": 800,
        "contract_validation_status": "passed",
        "semantic_validation_status": "passed",
    }
    payload.update(overrides)
    return parse_final_response_candidate(payload)


def test_day12_gate_d_still_validates_final_responses_correctly():
    """Day 12's own `GateD.evaluate()` behavior (`test_day12_gate_d.py`),
    re-checked directly here against the real committed Gate-D/Gate-B
    policy -- a fully valid response allows, and a forged (never
    Gate-C-validated) citation fails closed to `safe_failure`, never
    `allow`. Confirms Day 13's new tool boundary (which Gate-D never
    imports or calls -- final-response validation and tool execution are
    disjoint concerns) has not altered Gate-D's own decisions."""
    gate_b_policy = PolicyRegistry.load()
    gate_d_policy = GateDPolicyRegistry.load(gate_b_policy=gate_b_policy)
    gate_d = GateD(policy_registry=gate_d_policy)

    gate_b_decision = GateBDecision(
        decision=GateBStatus.ALLOW,
        effective_tenant_scope=("TENANT-A",),
        effective_data_classes=(DataClassification.PUBLIC, DataClassification.INTERNAL),
        effective_pii_policy=(PiiCategory.NONE, PiiCategory.CONTACT, PiiCategory.PERSONAL_IDENTIFIER),
        disclosure_profile="policy_reader",
        lane=LaneId.RAG,
        reason_code="test_setup",
        policy_version=gate_b_policy.policy_version,
    )
    disclosure_profile = gate_b_policy.get_disclosure_profile("policy_reader")

    valid_citation = {"evidence_id": "E-101", "chunk_id": "CH-101", "source_id": "SRC-POLICY-A", "source_version": "3"}
    valid_evidence = (GateCEvidenceRecord.model_validate(valid_citation),)

    # Fully valid response -> allow.
    allow_decision = gate_d.evaluate(
        candidate=_final_candidate(candidate_citations=[valid_citation], gate_c_validated_evidence_ids=["E-101"]),
        gate_c_validated_evidence=valid_evidence,
        gate_b_decision=gate_b_decision,
        disclosure_profile=disclosure_profile,
        protected_fields=(),
    )
    assert allow_decision.decision is GateDStatus.ALLOW
    assert allow_decision.validated_citation_ids == ("E-101",)

    # Forged citation (never Gate-C-validated) -> safe_failure, never allow.
    forged_decision = gate_d.evaluate(
        candidate=_final_candidate(
            candidate_citations=[{"evidence_id": "E-999", "chunk_id": "CH-999", "source_id": "SRC-FAKE", "source_version": "1"}],
            gate_c_validated_evidence_ids=[],
        ),
        gate_c_validated_evidence=(),
        gate_b_decision=gate_b_decision,
        disclosure_profile=disclosure_profile,
        protected_fields=(),
    )
    assert forged_decision.decision is GateDStatus.SAFE_FAILURE
    assert forged_decision.decision is not GateDStatus.ALLOW
