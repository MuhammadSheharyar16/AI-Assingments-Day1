"""
Day 10 Task 5 -- tenant-scope enforcement.

Proves, against the real committed `policy/gate_b_policy.v1.json` (Task 2's
`PolicyRegistry`) and the real supplied
`day10_pack/fixtures/tenant_scope_cases.json`, every required behavior
`gate_b_policy_requirements.md` names:

  - trusted tenant = requested tenant -> may continue if remaining policy
    allows (the assignment's own first required-behavior example);
  - trusted tenant != requested tenant -> deny before protected data access
    (the second required-behavior example), with an empty effective scope
    and zero `disclosure_profile`/`effective_*` leakage on the deny path;
  - every `tenant_scope_cases.json` case (TEN-001..005) resolves per its
    fixture-declared expectation -- same-tenant allow, cross-tenant deny, a
    mixed request narrowed (never denied *and* never widened past) the
    trusted tenant, and both the request-body and session-memory "override"
    attempts having no effect at all, proven structurally
    (`GateB.authorize()`'s signature carries no parameter either could even
    reach) rather than by a runtime check against their content;
  - effective tenant scope is always `requested INTERSECT trusted INTERSECT
    policy rule scope` -- never a union, never wider than any one of the
    three inputs, regardless of how large/adversarial a requested tenant
    list is (including wildcard-shaped strings, which carry no special
    meaning at all -- they are just ordinary, ungranted tenant ids);
  - "if policy supports a governed multi-tenant/admin scope, it must be
    explicit in the fixed policy and covered by tests" -- proven moot here:
    the committed policy declares no such scope at all (`TenantScopeKind`
    has exactly one governed value, `own_tenant`, and every committed role
    uses it), so "do not create hidden super-admin behavior" holds
    structurally, not by a runtime check against a role name.

Permission matching (Task 6), data classification (Task 7), and PII/
disclosure (Task 8/9) are out of scope here -- every case below uses a
fixed, already-authorized-if-tenant-allows role/intent/lane/data_class
combination (`sourcing_analyst` / `INT-POLICY-QUESTION` / `rag`, `GB-R003`)
so the only variable under test is tenant scope.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aico.api.identity import TrustedIdentity
from aico.control.gate_b import GateB, GateBRequest
from aico.control.models import GateADecision, GateAStatus, GateBStatus, LaneDecision
from aico.control.ontology import LaneId
from aico.control.policy_models import DataClassification, Role, TenantScopeKind
from aico.control.policy_registry import PolicyRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent
TENANT_SCOPE_CASES_PATH = REPO_ROOT / "data" / "day10_pack" / "fixtures" / "tenant_scope_cases.json"

TENANT_SCOPE_CASES = json.loads(TENANT_SCOPE_CASES_PATH.read_text(encoding="utf-8"))["cases"]

# Fixed, already-governed combination every case below authorizes against
# (`GB-R003`: `sourcing_analyst` / `INT-POLICY-QUESTION` / `rag`, allows
# `[public, internal]`) -- a specific `data_class` is always supplied so
# the unrelated Task 10 `clarify` path never fires; these tests are about
# tenant scope, not data classification.
_INTENT_ID = "INT-POLICY-QUESTION"
_LANE = LaneId.RAG
_DATA_CLASS = DataClassification.INTERNAL


def _identity(tenant_id: str, user_id: str = "USER-2", roles: tuple[str, ...] = ("sourcing_analyst",)) -> TrustedIdentity:
    return TrustedIdentity(tenant_id=tenant_id, user_id=user_id, roles=roles)


def _matched() -> GateADecision:
    return GateADecision(
        status=GateAStatus.MATCHED,
        domain="supplier_governance",
        intent_id=_INTENT_ID,
        reason_code="test_setup",
        ontology_version="1.0",
    )


def _lane_decision() -> LaneDecision:
    return LaneDecision(lane=_LANE, intent_id=_INTENT_ID, reason_code="test_setup", ontology_version="1.0")


@pytest.fixture
def registry() -> PolicyRegistry:
    return PolicyRegistry.load()


@pytest.fixture
def gate_b(registry: PolicyRegistry) -> GateB:
    return GateB(registry)


def _authorize(gate_b: GateB, identity: TrustedIdentity, tenant_ids: tuple[str, ...] = ()) -> GateADecision:
    return gate_b.authorize(identity, _matched(), _lane_decision(), GateBRequest(data_class=_DATA_CLASS, tenant_ids=tenant_ids))


# ---------------------------------------------------------------------------
# The assignment's own two required-behavior examples
# ---------------------------------------------------------------------------


def test_same_trusted_and_requested_tenant_may_continue(gate_b):
    """trusted tenant = TENANT-A, requested tenant = TENANT-A -> may
    continue if remaining policy allows."""
    decision = _authorize(gate_b, _identity("TENANT-A"), tenant_ids=("TENANT-A",))
    assert decision.decision is GateBStatus.ALLOW
    assert decision.effective_tenant_scope == ("TENANT-A",)


def test_cross_tenant_denies_before_protected_data_access(gate_b):
    """trusted tenant = TENANT-A, requested tenant = TENANT-B -> deny
    before protected data access -- proven by an empty effective scope and
    no disclosure profile, not merely a `deny` label."""
    decision = _authorize(gate_b, _identity("TENANT-A"), tenant_ids=("TENANT-B",))
    assert decision.decision is GateBStatus.DENY
    assert decision.reason_code == "cross_tenant_denied"
    assert decision.effective_tenant_scope == ()
    assert decision.effective_data_classes == ()
    assert decision.effective_pii_policy == ()
    assert decision.disclosure_profile is None


def test_no_requested_tenant_defaults_to_the_callers_own_trusted_tenant(gate_b):
    """A caller who asks for no tenant in particular is bounded to their
    own trusted tenant, never to nothing and never to everything."""
    decision = _authorize(gate_b, _identity("TENANT-A"))
    assert decision.decision is GateBStatus.ALLOW
    assert decision.effective_tenant_scope == ("TENANT-A",)


# ---------------------------------------------------------------------------
# tenant_scope_cases.json -- fixture-driven
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", TENANT_SCOPE_CASES, ids=[c["id"] for c in TENANT_SCOPE_CASES])
def test_tenant_scope_case_matches_fixture_expectation(gate_b, case):
    trusted = case["trusted_identity"]
    identity = _identity(trusted["tenant_id"], trusted["user_id"], tuple(trusted["roles"]))
    tenant_ids = tuple(case.get("requested_scope", {}).get("tenant_ids", ()))

    decision = _authorize(gate_b, identity, tenant_ids)

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
        # case dict but never read here at all -- `GateB.authorize()`'s
        # signature has nowhere to accept them (see
        # test_authorize_signature_has_no_body_or_memory_parameter below).
        # Trusted identity alone decides the outcome.
        assert decision.decision is GateBStatus.ALLOW
        assert decision.effective_tenant_scope == ("TENANT-A",)
    else:  # pragma: no cover - guard against an unhandled fixture case id
        pytest.fail(f"unhandled tenant scope case id: {case['id']!r}")


def test_authorize_signature_has_no_body_or_memory_parameter():
    """Structural backbone of TEN-004/TEN-005: `authorize()` has no
    parameter a caller-supplied request-body identity or session-memory
    text could even be passed through -- `identity` (trusted, Day 6),
    `gate_a_decision`/`lane_decision` (typed upstream decisions), and
    `requested` (the narrow `GateBRequest`) are the whole signature."""
    import inspect

    params = list(inspect.signature(GateB.authorize).parameters)
    assert params == ["self", "identity", "gate_a_decision", "lane_decision", "requested"]


# ---------------------------------------------------------------------------
# Effective scope = requested INTERSECT trusted INTERSECT policy -- never a union
# ---------------------------------------------------------------------------


def test_effective_tenant_scope_never_includes_a_tenant_the_caller_did_not_ask_for(gate_b):
    decision = _authorize(gate_b, _identity("TENANT-A"), tenant_ids=("TENANT-A", "TENANT-B", "TENANT-C"))
    assert decision.decision is GateBStatus.ALLOW
    assert decision.effective_tenant_scope == ("TENANT-A",)


def test_effective_tenant_scope_never_includes_a_tenant_the_caller_is_not_trusted_for(gate_b):
    """The mirror image of the case above: even a requested list that
    contains the caller's own tenant among many others they are not
    trusted for still narrows to exactly the one they are trusted for --
    intersection, not "any listed tenant the caller happens to name"."""
    decision = _authorize(
        gate_b, _identity("TENANT-A"), tenant_ids=("TENANT-X", "TENANT-A", "TENANT-Y", "TENANT-Z")
    )
    assert decision.decision is GateBStatus.ALLOW
    assert decision.effective_tenant_scope == ("TENANT-A",)


@pytest.mark.parametrize("wildcard_like", ["*", "ALL", "TENANT-*", "%", "TENANT-A OR 1=1"])
def test_wildcard_shaped_requested_tenant_ids_carry_no_special_meaning(gate_b, wildcard_like):
    """A caller cannot widen scope by naming something that merely *looks*
    like a wildcard/admin-all sentinel -- `tenant_ids` is intersected as
    plain strings, there is no special-cased value anywhere in `gate_b.py`
    that means "every tenant"."""
    decision = _authorize(gate_b, _identity("TENANT-A"), tenant_ids=(wildcard_like,))
    assert decision.decision is GateBStatus.DENY
    assert decision.reason_code == "cross_tenant_denied"
    assert decision.effective_tenant_scope == ()


def test_effective_scope_never_exceeds_a_single_trusted_tenant_regardless_of_role(gate_b, registry):
    """Sweep every governed role in the committed policy: no matter which
    role/rule combination is exercised, `effective_tenant_scope` is never
    anything other than exactly the caller's own trusted tenant."""
    for rule in registry.rules:
        if not rule.allowed:
            continue
        identity = TrustedIdentity(tenant_id="TENANT-SWEEP", user_id="USER-SWEEP", roles=(rule.role,))
        gate_a_decision = GateADecision(
            status=GateAStatus.MATCHED,
            domain="supplier_governance",
            intent_id=rule.intent_id,
            reason_code="test_setup",
            ontology_version="1.0",
        )
        lane_decision = LaneDecision(
            lane=rule.lane, intent_id=rule.intent_id, reason_code="test_setup", ontology_version="1.0"
        )
        data_class = rule.allowed_data_classes[0] if rule.allowed_data_classes else None
        decision = gate_b.authorize(
            identity,
            gate_a_decision,
            lane_decision,
            GateBRequest(data_class=data_class, tenant_ids=("TENANT-SWEEP", "TENANT-OTHER")),
        )
        # Every rule iterated is `allowed=true` with a `data_class` drawn
        # straight from its own `allowed_data_classes`, so tenant scope is
        # the only thing under test here -- this always resolves to ALLOW.
        assert decision.decision is GateBStatus.ALLOW
        assert decision.effective_tenant_scope == ("TENANT-SWEEP",)


# ---------------------------------------------------------------------------
# No governed multi-tenant/admin scope; no hidden super-admin behavior
# ---------------------------------------------------------------------------


def test_no_governed_multi_tenant_or_admin_tenant_scope_exists():
    """`gate_b_policy_requirements.md`: "If policy supports a governed
    multi-tenant/admin scope, it must be explicit in the fixed policy and
    covered by tests." The committed policy declares none -- `own_tenant`
    is the entire closed `TenantScopeKind` set, so there is no governed
    value this module could even route "admin" behavior through."""
    assert list(TenantScopeKind) == [TenantScopeKind.OWN_TENANT]


def test_every_committed_role_is_scoped_to_its_own_tenant_only(registry):
    """Every role in the real committed policy uses `own_tenant` -- not
    merely that the type system permits nothing else, but that the actual
    shipped policy data doesn't either."""
    assert registry.roles  # sanity: the committed policy actually has roles
    for role in registry.roles:
        assert isinstance(role, Role)
        assert role.tenant_scope is TenantScopeKind.OWN_TENANT


def test_no_hidden_admin_role_grants_cross_tenant_access(gate_b, registry):
    """Sweep every governed role against a tenant it is not trusted for --
    none of them, including `compliance_reviewer` (the most broadly
    permissioned role in this policy, `restricted`-adjacent
    `confidential` access included), can cross into a tenant its own
    caller is not trusted for. Broader *data* permission never implies
    broader *tenant* permission."""
    for rule in registry.rules:
        if not rule.allowed:
            continue
        identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=(rule.role,))
        gate_a_decision = GateADecision(
            status=GateAStatus.MATCHED,
            domain="supplier_governance",
            intent_id=rule.intent_id,
            reason_code="test_setup",
            ontology_version="1.0",
        )
        lane_decision = LaneDecision(
            lane=rule.lane, intent_id=rule.intent_id, reason_code="test_setup", ontology_version="1.0"
        )
        data_class = rule.allowed_data_classes[0] if rule.allowed_data_classes else None
        decision = gate_b.authorize(
            identity, gate_a_decision, lane_decision, GateBRequest(data_class=data_class, tenant_ids=("TENANT-B",))
        )
        assert decision.decision is GateBStatus.DENY
        assert decision.reason_code == "cross_tenant_denied"
