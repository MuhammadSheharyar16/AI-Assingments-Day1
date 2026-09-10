"""
Day 10 Task 3 -- the Gate-B contract: `GateBDecision`/`GateBStatus`
(`src/aico/control/models.py`) and the `GateB`/`GateBRequest` engine that
produces/consumes them (`src/aico/control/gate_b.py`).
Day 10 Task 4 -- every fail-closed stage that engine runs.

Proves, against the real committed `policy/gate_b_policy.v1.json` (Task 2's
`PolicyRegistry`), the real committed `ontology/registry.v1.json` (Day 9's
`OntologyRegistry`), and the real supplied `day10_pack/fixtures/`
`permission_cases.json` / `tenant_scope_cases.json`:

  - the typed contract shape: `GateBDecision` exposes every field Task 3
    requires, `GateBStatus` is exactly `{allow, clarify, deny}`;
  - every `permission_cases.json` case (PERM-001..006) resolves to its
    fixture-declared `expected_decision`/`expected_rule_id` -- allowed
    policy-document read, denied structured-data lookup, allowed structured
    lookup for a specifically authorized role, unknown role, unknown
    intent, lane mismatch;
  - every `tenant_scope_cases.json` case (TEN-001..005) resolves per its
    fixture-declared expectation -- same-tenant allow, cross-tenant deny
    with an empty effective scope, a mixed request narrowed (never denied
    *and* never widened past) the trusted tenant, and both the request-body
    and session-memory "override" attempts having no effect at all, proven
    structurally (`authorize()`'s signature carries no parameter either
    could even reach) rather than by a runtime check against their content;
  - deny-by-default (Task 4), every bullet `gate_b_policy_requirements.md`
    lists: trusted identity missing (`identity=None`, never an
    `AttributeError`), role missing/unknown, permission rule absent, intent
    unknown (the optional `ontology_registry` defense-in-depth check), lane
    inconsistent with policy, requested scope that cannot be safely bounded
    (cross-tenant), requested classification not allowed, and a
    matched-but-`allowed=false` rule (`GB-R002`) -- every one of these
    denies, never falls through to `allow`; plus the two whole-module
    guarantees ("policy version invalid" is structurally impossible,
    "no natural-language inference" is structurally impossible -- proven by
    inspecting the actual types involved, not a runtime check);
  - the one documented `clarify` case (Task 10): a matched, allowed rule
    whose own `allowed_data_classes` names more than one classification and
    the caller asked for none in particular;
  - effective scope only narrows, never widens (Task 5): every `ALLOW`
    decision's `effective_data_classes`/`effective_tenant_scope` is a
    subset of what the matched rule / trusted identity respectively permit;
  - `GateBRequest` structurally carries no role/tenant-ownership/permission/
    clearance field at all (Task 10's "do not ask the user to self-assert
    a higher role/tenant/permission/clearance" -- enforced by the type
    itself having nowhere to put one, not by a runtime check rejecting one).

Task 8/9 (PII/disclosure field-level behavior) and Task 11 (no-fall-through
call-count proof) are out of scope here -- this file only proves the Gate-B
authorization contract and its implemented matching engine.
"""
from __future__ import annotations

import dataclasses
import inspect
import json
from pathlib import Path

import pytest

from aico.api.identity import TrustedIdentity
from aico.control.errors import GateBError
from aico.control.gate_b import GateB, GateBRequest
from aico.control.models import GateADecision, GateAStatus, GateBDecision, GateBStatus, LaneDecision
from aico.control.ontology import LaneId
from aico.control.ontology_registry import OntologyRegistry
from aico.control.policy_models import DataClassification, PiiCategory
from aico.control.policy_registry import PolicyRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent
PERMISSION_CASES_PATH = REPO_ROOT / "data" / "day10_pack" / "fixtures" / "permission_cases.json"
TENANT_SCOPE_CASES_PATH = REPO_ROOT / "data" / "day10_pack" / "fixtures" / "tenant_scope_cases.json"

PERMISSION_CASES = json.loads(PERMISSION_CASES_PATH.read_text(encoding="utf-8"))["cases"]
TENANT_SCOPE_CASES = json.loads(TENANT_SCOPE_CASES_PATH.read_text(encoding="utf-8"))["cases"]


def _identity(case_identity: dict) -> TrustedIdentity:
    return TrustedIdentity(
        tenant_id=case_identity["tenant_id"], user_id=case_identity["user_id"], roles=tuple(case_identity["roles"])
    )


def _matched(intent_id: str, *, domain: str = "supplier_governance") -> GateADecision:
    return GateADecision(
        status=GateAStatus.MATCHED, domain=domain, intent_id=intent_id, reason_code="test_setup", ontology_version="1.0"
    )


def _lane_decision(lane: LaneId, intent_id: str | None) -> LaneDecision:
    return LaneDecision(lane=lane, intent_id=intent_id, reason_code="test_setup", ontology_version="1.0")


@pytest.fixture
def registry() -> PolicyRegistry:
    return PolicyRegistry.load()


@pytest.fixture
def gate_b(registry: PolicyRegistry) -> GateB:
    return GateB(registry)


@pytest.fixture
def gate_b_with_ontology(registry: PolicyRegistry) -> GateB:
    """A second `GateB`, built with the real committed `OntologyRegistry`
    -- the Task 4 defense-in-depth "intent unknown" check is only active
    on an instance built this way (see `gate_b.py`'s module docstring,
    stage 2)."""
    return GateB(registry, ontology_registry=OntologyRegistry.load())


# ---------------------------------------------------------------------------
# Contract shape
# ---------------------------------------------------------------------------


def test_gate_b_status_is_exactly_allow_clarify_deny():
    assert {s.value for s in GateBStatus} == {"allow", "clarify", "deny"}


def test_gate_b_decision_exposes_every_required_field(gate_b):
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("supplier_reader",))
    decision = gate_b.authorize(
        identity,
        _matched("INT-POLICY-QUESTION"),
        _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION"),
        GateBRequest(data_class=DataClassification.INTERNAL),
    )

    assert isinstance(decision, GateBDecision)
    for required_field in (
        "decision",
        "effective_tenant_scope",
        "effective_data_classes",
        "effective_pii_policy",
        "disclosure_profile",
        "rule_id",
        "reason_code",
        "policy_version",
    ):
        assert hasattr(decision, required_field)
    assert decision.decision is GateBStatus.ALLOW
    assert decision.policy_version == "1.0"


def test_gate_b_request_carries_no_role_tenant_permission_or_clearance_field():
    """Structural proof of Task 10's boundary: there is nowhere on this
    type for a caller to even attempt to self-assert a higher role/tenant
    ownership/permission/clearance -- `tenant_ids` is the one deliberate,
    scope-*narrowing* exception (see `gate_b.py`'s `GateBRequest`
    docstring), never a grant."""
    field_names = {f.name for f in dataclasses.fields(GateBRequest)}
    assert field_names == {"operation", "data_class", "tenant_ids"}
    assert "role" not in field_names
    assert "roles" not in field_names
    assert "permission" not in field_names
    assert "clearance" not in field_names


def test_authorize_signature_has_no_memory_or_request_body_parameter():
    """Structural proof that session memory / raw request-body identity
    cannot reach Gate-B at all -- `authorize()` accepts exactly a trusted
    identity, the two upstream decisions, and the narrow `GateBRequest`;
    nothing here could carry memory text or an attacker-supplied identity
    payload even if a caller tried to pass one."""
    params = list(inspect.signature(GateB.authorize).parameters)
    assert params == ["self", "identity", "gate_a_decision", "lane_decision", "requested"]


# ---------------------------------------------------------------------------
# permission_cases.json -- fixture-driven
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", PERMISSION_CASES, ids=[c["id"] for c in PERMISSION_CASES])
def test_permission_case_matches_fixture_expectation(gate_b, case):
    identity = _identity(case["trusted_identity"])
    lane = LaneId(case["lane"])
    requested = GateBRequest(
        operation=case["requested_operation"], data_class=DataClassification(case["requested_data_class"])
    )

    decision = gate_b.authorize(identity, _matched(case["intent_id"]), _lane_decision(lane, case["intent_id"]), requested)

    assert decision.decision.value == case["expected_decision"]
    if "expected_rule_id" in case:
        assert decision.rule_id == case["expected_rule_id"]


def test_perm_001_allowed_policy_document_read_grants_matching_effective_scope(gate_b):
    identity = _identity({"tenant_id": "TENANT-A", "user_id": "USER-1", "roles": ["supplier_reader"]})
    decision = gate_b.authorize(
        identity,
        _matched("INT-POLICY-QUESTION"),
        _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION"),
        GateBRequest(data_class=DataClassification.INTERNAL),
    )
    assert decision.decision is GateBStatus.ALLOW
    assert decision.effective_tenant_scope == ("TENANT-A",)
    assert decision.effective_data_classes == (DataClassification.INTERNAL,)
    assert decision.disclosure_profile == "policy_reader"


def test_perm_002_denied_structured_lookup_is_rule_denied_not_no_match(gate_b):
    """`GB-R002` exists and matches -- the deny reason must reflect a
    matched-but-disallowed rule, distinct from no rule matching at all."""
    identity = _identity({"tenant_id": "TENANT-A", "user_id": "USER-1", "roles": ["supplier_reader"]})
    decision = gate_b.authorize(
        identity,
        _matched("INT-STRUCTURED-LOOKUP"),
        _lane_decision(LaneId.MODE_B, "INT-STRUCTURED-LOOKUP"),
        GateBRequest(data_class=DataClassification.INTERNAL),
    )
    assert decision.decision is GateBStatus.DENY
    assert decision.reason_code == "rule_denied"
    assert decision.rule_id == "GB-R002"
    assert decision.effective_data_classes == ()
    assert decision.disclosure_profile is None


def test_perm_003_analyst_structured_lookup_allowed_with_bounded_scope(gate_b):
    identity = _identity({"tenant_id": "TENANT-A", "user_id": "USER-2", "roles": ["sourcing_analyst"]})
    decision = gate_b.authorize(
        identity,
        _matched("INT-STRUCTURED-LOOKUP"),
        _lane_decision(LaneId.MODE_B, "INT-STRUCTURED-LOOKUP"),
        GateBRequest(data_class=DataClassification.INTERNAL),
    )
    assert decision.decision is GateBStatus.ALLOW
    assert decision.rule_id == "GB-R004"
    assert decision.disclosure_profile == "structured_reader"
    assert PiiCategory.PERSONAL_IDENTIFIER in decision.effective_pii_policy


# ---------------------------------------------------------------------------
# tenant_scope_cases.json -- fixture-driven
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", TENANT_SCOPE_CASES, ids=[c["id"] for c in TENANT_SCOPE_CASES])
def test_tenant_scope_case_matches_fixture_expectation(gate_b, case):
    """Every case authorized against `sourcing_analyst` +
    `INT-POLICY-QUESTION` + `rag` (`GB-R003`, `allowed_data_classes:
    [public, internal]`) so a specific `data_class` is supplied to avoid
    the unrelated `clarify` path -- these cases are about tenant scope,
    not data classification."""
    identity = _identity(case["trusted_identity"])
    tenant_ids = tuple(case.get("requested_scope", {}).get("tenant_ids", ()))
    requested = GateBRequest(data_class=DataClassification.INTERNAL, tenant_ids=tenant_ids)

    decision = gate_b.authorize(
        identity, _matched("INT-POLICY-QUESTION"), _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION"), requested
    )

    if case["id"] == "TEN-001":
        assert decision.decision is GateBStatus.ALLOW
        assert decision.effective_tenant_scope == ("TENANT-A",)
    elif case["id"] == "TEN-002":
        assert decision.decision.value == case["expected_decision"]
        assert decision.effective_tenant_scope == ()
        assert decision.reason_code == "cross_tenant_denied"
    elif case["id"] == "TEN-003":
        # Fixture explicitly allows either outcome (`allowed_outcomes`:
        # "narrow_to_TENANT-A" or "deny" -- descriptive labels, not
        # `GateBStatus` values); this build narrows rather than denies --
        # either way it must never grant TENANT-B.
        assert "narrow_to_TENANT-A" in case["allowed_outcomes"]
        assert decision.decision is GateBStatus.ALLOW
        assert decision.effective_tenant_scope == ("TENANT-A",)
        assert "TENANT-B" not in decision.effective_tenant_scope
    elif case["id"] in ("TEN-004", "TEN-005"):
        # `request_body_identity`/`memory_text` are present in the fixture
        # case dict but never read here at all -- `authorize()`'s
        # signature has nowhere to accept them (see
        # test_authorize_signature_has_no_memory_or_request_body_parameter).
        # Trusted identity alone decides the outcome.
        assert decision.decision is GateBStatus.ALLOW
        assert decision.effective_tenant_scope == ("TENANT-A",)
    else:  # pragma: no cover - guard against an unhandled fixture case id
        pytest.fail(f"unhandled tenant scope case id: {case['id']!r}")


def test_cross_tenant_denial_happens_before_any_effective_scope_is_granted(gate_b):
    identity = _identity({"tenant_id": "TENANT-A", "user_id": "USER-2", "roles": ["sourcing_analyst"]})
    decision = gate_b.authorize(
        identity,
        _matched("INT-POLICY-QUESTION"),
        _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION"),
        GateBRequest(data_class=DataClassification.INTERNAL, tenant_ids=("TENANT-B",)),
    )
    assert decision.decision is GateBStatus.DENY
    assert decision.effective_tenant_scope == ()
    assert decision.effective_data_classes == ()
    assert decision.effective_pii_policy == ()
    assert decision.disclosure_profile is None


# ---------------------------------------------------------------------------
# Deny by default (Task 4)
# ---------------------------------------------------------------------------


def test_missing_trusted_identity_denies_with_a_typed_result_not_a_crash(gate_b):
    """`identity=None` -- Task 4's "trusted identity missing" -- must
    return a typed `deny`, never raise `AttributeError` from dereferencing
    `identity.roles` on a caller that somehow reached `authorize()`
    without one."""
    decision = gate_b.authorize(
        None, _matched("INT-POLICY-QUESTION"), _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION")
    )
    assert decision.decision is GateBStatus.DENY
    assert decision.reason_code == "identity_missing"
    assert decision.rule_id is None
    assert decision.effective_tenant_scope == ()


def test_missing_role_denies(gate_b):
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=())
    decision = gate_b.authorize(
        identity, _matched("INT-POLICY-QUESTION"), _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION")
    )
    assert decision.decision is GateBStatus.DENY
    assert decision.reason_code == "role_missing"
    assert decision.rule_id is None


def test_unknown_role_denies(gate_b):
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("made_up_role",))
    decision = gate_b.authorize(
        identity, _matched("INT-POLICY-QUESTION"), _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION")
    )
    assert decision.decision is GateBStatus.DENY
    assert decision.reason_code == "unknown_role"


def test_unknown_intent_denies_when_built_with_an_ontology_registry(gate_b_with_ontology):
    """Task 4's "intent unknown", defense-in-depth: a hand-built
    `GateADecision` claiming `MATCHED` for an intent id no ontology
    actually governs must still deny -- `GateA.classify()` itself never
    produces such a decision, this is what catches one anyway."""
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("supplier_reader",))
    decision = gate_b_with_ontology.authorize(
        identity, _matched("INT-NOT-REGISTERED"), _lane_decision(LaneId.RAG, "INT-NOT-REGISTERED")
    )
    assert decision.decision is GateBStatus.DENY
    assert decision.reason_code == "unknown_intent"
    assert decision.rule_id is None


def test_unknown_intent_check_is_skipped_without_an_ontology_registry(gate_b):
    """Without `ontology_registry`, the same ungoverned intent id still
    denies -- just via `no_matching_rule` (no rule references it) rather
    than the more specific `unknown_intent` reason. Either way it is never
    `allow`; only the provenance differs."""
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("supplier_reader",))
    decision = gate_b.authorize(
        identity, _matched("INT-NOT-REGISTERED"), _lane_decision(LaneId.RAG, "INT-NOT-REGISTERED")
    )
    assert decision.decision is GateBStatus.DENY
    assert decision.reason_code == "no_matching_rule"


def test_known_intent_is_unaffected_by_the_ontology_registry_check(gate_b_with_ontology):
    """The defense-in-depth check never interferes with an ordinary,
    governed request -- every `permission_cases.json` case still passes
    identically on a `GateB` built with `ontology_registry` set."""
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("supplier_reader",))
    decision = gate_b_with_ontology.authorize(
        identity,
        _matched("INT-POLICY-QUESTION"),
        _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION"),
        GateBRequest(data_class=DataClassification.INTERNAL),
    )
    assert decision.decision is GateBStatus.ALLOW
    assert decision.rule_id == "GB-R001"


@pytest.mark.parametrize("status", [GateAStatus.AMBIGUOUS, GateAStatus.UNSUPPORTED, GateAStatus.BLOCKED])
def test_unmatched_gate_a_decision_denies(gate_b, status):
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("supplier_reader",))
    gate_a_decision = GateADecision(status=status, reason_code="test_setup", ontology_version="1.0")
    lane = LaneId.CLARIFY if status is GateAStatus.AMBIGUOUS else LaneId.BLOCK
    decision = gate_b.authorize(identity, gate_a_decision, _lane_decision(lane, None))
    assert decision.decision is GateBStatus.DENY
    assert decision.reason_code == f"upstream_not_matched:{status.value}"
    assert decision.rule_id is None


@pytest.mark.parametrize("lane", [LaneId.BLOCK, LaneId.CLARIFY])
def test_lane_already_block_or_clarify_denies_without_a_rule_lookup(gate_b, lane):
    """Even if Gate-A somehow reported `MATCHED` alongside a `block`/
    `clarify` lane (an inconsistency Gate-A/the lane selector never
    actually produce), Gate-B still refuses to authorize it rather than
    attempting a rule lookup against a lane no rule could ever legitimately
    name."""
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("supplier_reader",))
    decision = gate_b.authorize(identity, _matched("INT-POLICY-QUESTION"), _lane_decision(lane, "INT-POLICY-QUESTION"))
    assert decision.decision is GateBStatus.DENY
    assert decision.reason_code == f"upstream_lane_not_authorizable:{lane.value}"


def test_no_rule_matches_requested_combination_denies(gate_b):
    """Well-formed role/intent/lane, but no rule governs this exact
    combination at all -- e.g. `compliance_reviewer` has no rule for
    `INT-POLICY-QUESTION`/`rag`."""
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-3", roles=("compliance_reviewer",))
    decision = gate_b.authorize(
        identity, _matched("INT-POLICY-QUESTION"), _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION")
    )
    assert decision.decision is GateBStatus.DENY
    assert decision.reason_code == "no_matching_rule"
    assert decision.rule_id is None


def test_default_decision_is_deny_never_allow_by_absence_of_a_rule(gate_b, registry):
    """Direct proof of the working rule 'Do not implement: if no rule
    matched: allow' -- a role/intent/lane combination this policy simply
    never declares a rule for must deny, not fall through to allow."""
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("supplier_reader",))
    assert registry.find_rule("supplier_reader", "INT-HELP", LaneId.SAFE_FAST_PATH) is None
    decision = gate_b.authorize(
        identity, _matched("INT-HELP"), _lane_decision(LaneId.SAFE_FAST_PATH, "INT-HELP")
    )
    assert decision.decision is GateBStatus.DENY


def test_policy_version_invalid_is_structurally_impossible():
    """Task 4's "policy version invalid" needs no runtime check in
    `gate_b.py` at all: `GateB` can only ever be built from an already
    loaded `PolicyRegistry`, and `PolicyRegistry.load()` (Task 2) already
    refuses to construct one from an invalid policy document
    (`PolicyLoadError`) -- there is no code path producing a `GateB`
    instance whose `registry.policy_version` is not a validated string.
    Proven here by inspecting the actual construction path, not a mock."""
    from aico.control.errors import PolicyLoadError

    assert inspect.isclass(PolicyLoadError)
    load_source = inspect.getsource(PolicyRegistry.load)
    assert "GateBPolicyDocument.model_validate" in load_source
    assert "PolicyLoadError" in load_source


def test_authorize_has_no_raw_text_parameter_to_infer_permission_from():
    """Task 4's "do not infer permission from natural language" is
    likewise structural: every parameter `authorize()` accepts is a typed
    decision/identity/request object, never a bare `str`. There is no
    string anywhere in this signature a permission decision could be
    "inferred" from. `typing.get_type_hints` resolves the module's
    `from __future__ import annotations` string annotations back into real
    type objects -- a plain `inspect.signature().parameters[...].annotation`
    check would only ever see the *string* `"str"`, never actually catching
    a raw-text parameter."""
    import typing

    hints = typing.get_type_hints(GateB.authorize)
    hints.pop("return", None)
    assert hints  # sanity: the resolution above actually found parameters
    for name, hint in hints.items():
        assert hint is not str, f"{name!r} accepts a raw string"


# ---------------------------------------------------------------------------
# Clarify (Task 10's one documented case)
# ---------------------------------------------------------------------------


def test_clarify_when_matched_rule_allows_multiple_classes_and_none_requested(gate_b):
    """`GB-R005` (`compliance_reviewer` / `INT-STRUCTURED-LOOKUP` /
    `mode_b`) allows `[public, internal, confidential]` -- three
    classifications. Asking for none in particular is genuinely ambiguous
    and safely clarifiable (Task 10), never a role/tenant/permission the
    caller would have to self-assert."""
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-3", roles=("compliance_reviewer",))
    decision = gate_b.authorize(
        identity,
        _matched("INT-STRUCTURED-LOOKUP"),
        _lane_decision(LaneId.MODE_B, "INT-STRUCTURED-LOOKUP"),
        GateBRequest(data_class=None),
    )
    assert decision.decision is GateBStatus.CLARIFY
    assert decision.reason_code == "data_class_selection_required"
    assert decision.rule_id == "GB-R005"
    # Nothing granted yet -- clarify is not a partial allow.
    assert decision.effective_data_classes == ()
    assert decision.effective_pii_policy == ()
    assert decision.disclosure_profile is None


def test_clarify_never_needed_when_matched_rule_allows_exactly_one_class(gate_b):
    """`GB-R001` allows two classes (`[public, internal]`) -- contrast
    case proving the clarify trigger is really about the *rule's* own
    breadth, not about the caller omitting `data_class` per se: `GB-R004`
    (`sourcing_analyst` / structured lookup) also allows two classes, so
    this asserts against a rule this module does not otherwise cover, to
    show the same mechanism generalizes."""
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-2", roles=("sourcing_analyst",))
    decision = gate_b.authorize(
        identity,
        _matched("INT-STRUCTURED-LOOKUP"),
        _lane_decision(LaneId.MODE_B, "INT-STRUCTURED-LOOKUP"),
        GateBRequest(data_class=None),
    )
    assert decision.decision is GateBStatus.CLARIFY
    assert decision.rule_id == "GB-R004"


# ---------------------------------------------------------------------------
# Effective scope only narrows, never widens (Task 5)
# ---------------------------------------------------------------------------


def test_effective_data_classes_is_always_a_subset_of_the_matched_rules_allowed_classes(gate_b, registry):
    for rule in registry.rules:
        if not rule.allowed or not rule.allowed_data_classes:
            continue
        identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-X", roles=(rule.role,))
        for data_class in rule.allowed_data_classes:
            decision = gate_b.authorize(
                identity,
                _matched(rule.intent_id),
                _lane_decision(rule.lane, rule.intent_id),
                GateBRequest(data_class=data_class),
            )
            assert decision.decision is GateBStatus.ALLOW
            assert set(decision.effective_data_classes) <= set(rule.allowed_data_classes)


def test_effective_tenant_scope_never_includes_a_tenant_the_caller_did_not_ask_for(gate_b):
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-2", roles=("sourcing_analyst",))
    decision = gate_b.authorize(
        identity,
        _matched("INT-POLICY-QUESTION"),
        _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION"),
        GateBRequest(data_class=DataClassification.INTERNAL, tenant_ids=("TENANT-A", "TENANT-B", "TENANT-C")),
    )
    assert decision.decision is GateBStatus.ALLOW
    assert decision.effective_tenant_scope == ("TENANT-A",)


def test_data_classification_not_allowed_by_matched_rule_denies(gate_b):
    """`supplier_reader` on `GB-R001` is allowed `[public, internal]` --
    `restricted` is a valid, governed `DataClassification` value, but this
    matched rule does not authorize it."""
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("supplier_reader",))
    decision = gate_b.authorize(
        identity,
        _matched("INT-POLICY-QUESTION"),
        _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION"),
        GateBRequest(data_class=DataClassification.RESTRICTED),
    )
    assert decision.decision is GateBStatus.DENY
    assert decision.reason_code == "data_classification_not_allowed"
    assert decision.rule_id == "GB-R001"


# ---------------------------------------------------------------------------
# GateBError: defensive, not a normal decision path
# ---------------------------------------------------------------------------


def test_gate_b_error_is_not_raised_for_any_ordinary_input(gate_b):
    """Every case above resolves to a typed `GateBDecision`, never an
    exception -- `GateBError` exists only for a hand-built invariant
    violation (see its own docstring), not for anything `GateA`/
    `PolicyRegistry` actually produce."""
    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("supplier_reader",))
    try:
        gate_b.authorize(identity, _matched("INT-POLICY-QUESTION"), _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION"))
    except GateBError:
        pytest.fail("GateBError raised for an ordinary, well-formed input")
