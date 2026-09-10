"""
Day 10 Task 3 -- Gate-B: the deterministic authorization/disclosure
boundary that decides whether a trusted caller may proceed, under which
tenant/data scope, and what may be disclosed -- run *after* Gate-A/lane
selection (Day 9) and *before* any protected evidence access (Day 10 Task
11's "no fall-through" rule).
Day 10 Task 4 -- every stage below fails closed; see each stage's own
"deny" outcome and the module docstring's final paragraph for the two
whole-module guarantees (no fall-through-to-allow, no natural-language
inference) that are not any single stage's job.

"Day 9 decided: what does this request mean, which governed lane should it
use. Day 10 answers: is this trusted caller allowed to do that, for which
tenant/data scope, what information is safe to disclose" (Day 10
assignment). `GateB.authorize()` never guesses and never invents: every
non-`DENY` outcome is derived only from a matched, `allowed=True`
`PermissionRule` a loaded `PolicyRegistry` (Task 2) actually governs, and
every scope value it grants is a narrowed intersection of what the
trusted caller, the matched policy rule, and (optionally) the caller's own
request each independently permit -- never a union, never repaired/widened
from any single one of those three (Day 10 working rule: "Gate-B may narrow
scope; it may not widen scope").

Authorization runs in ordered, fail-closed stages -- the first stage that
cannot positively resolve denies immediately, exactly like `gate_a.py`'s
own tiered classification (cheapest/most-certain checks first) and equally
"do not guess" (Day 10 Task 4: "Do not implement: if no rule matched: allow"):

  0. Trusted identity present.  `identity` itself must not be missing
     (Task 4's "trusted identity missing" -- `identity: TrustedIdentity |
     None` is typed nullable specifically so a caller that somehow reaches
     `authorize()` without one gets a typed `deny` here, never an
     `AttributeError` three stages later; Day 6's own dependency boundary
     already rejects an unauthenticated HTTP request with 401 before this
     is ever called for real, so this is defense-in-depth, not the primary
     enforcement point).

  1. Upstream must have actually resolved something to authorize.  A
     `GateADecision` that is not `MATCHED` (no single governed intent was
     resolved), or a `LaneDecision` already routed to `block`/`clarify`,
     has nothing for Gate-B to authorize -- deny. Gate-B does not re-decide
     Gate-A's own ambiguity/unsupported/blocked outcome; it only refuses to
     authorize what was never resolved to a governed intent+lane pair.

  2. Governed intent (Task 4's "intent unknown", defense-in-depth).  When
     this `GateB` was built with an `ontology_registry`, the `MATCHED`
     decision's own `intent_id` must actually be one that registry governs
     - `deny` ("unknown_intent") otherwise. `GateA.classify()` never
     produces a `MATCHED` decision naming an ungoverned intent (Day 9 Task
     1/2's own registry guarantees), so this never actually fires against
     the real pipeline; it exists so a hand-built or otherwise malformed
     `GateADecision` cannot be authorized just because its `status` field
     happens to read `MATCHED`. Optional (`None` skips this one check)
     precisely so `GateB(registry)` alone, without also threading through
     an `OntologyRegistry`, remains a valid, useful construction for tests
     that build their own throwaway `GateADecision`s.

  3. Trusted role.  `identity.roles` (Day 10 Task 3's extension to Day 6's
     `TrustedIdentity`, `aico.api.identity`) must carry at least one
     governed role, and that role must exist in the loaded policy - `deny`
     ("role missing"/"unknown role", Task 4) otherwise. This module
     evaluates exactly one role per decision - the first `identity.roles`
     entry, the same deterministic "first-declared, not request-dependent"
     tie-break `lane_selector.py`'s `_choose_lane()` already documents for
     an analogous case; every supplied fixture identity carries exactly one
     role, so this is never actually a tie-break in practice today.

  4. Matching rule.  `PolicyRegistry.find_rule(role_id, intent_id, lane)`
     (Task 2) must resolve exactly one governed `PermissionRule` - `deny`
     ("no matching rule", Task 4's "permission rule absent", also covering
     "lane inconsistent with policy"/Task 6's "lane mismatch" - a lane this
     specific role+intent combination has no rule for at all) if it does
     not, and `deny` ("rule denied") if it does but the rule's own
     `allowed` is `False` (fixture `GB-R002` - a matched-but-denied rule is
     a distinct, equally-deny outcome from no match at all, Task 6's own
     "denied permission" case). Neither case ever falls through to allow
     (Task 4: "Do not implement: if no rule matched: allow" -- see
     `test_default_decision_is_deny_never_allow_by_absence_of_a_rule`).
     Defense-in-depth, one more check even once a rule matches and
     `allowed` is `True`: `rule.required_permission` must actually be one
     of the matched `role.permissions` - `deny` ("permission not
     granted") otherwise. Every committed fixture role/rule pair is
     authored consistently (`find_rule` already ties a rule to one exact
     `role_id`, so this never actually fires against the real committed
     policy today), but `Role.permissions`/`PermissionRule.
     required_permission` are both real, governed policy fields (Task 1's
     required rule/role shape) - a policy that ever authored them
     inconsistently must still fail closed on `rule.allowed` alone, not
     silently authorize a permission the matched role was never granted.

  5. Tenant scope (Task 5).  The trusted caller's own tenant
     (`identity.tenant_id` - the only tenant a role scoped `own_tenant`
     ever trusts, see `policy_models.py`'s `TenantScopeKind`) intersected
     with whatever tenant ids the caller's own `GateBRequest.tenant_ids`
     asked for (defaulting to "just my own tenant" when the caller asked
     for nothing in particular). An empty intersection - the caller asked
     for a tenant that is not their own, i.e. Task 4's "requested scope
     cannot be safely bounded" - denies ("cross_tenant_denied", Task 5's
     required behavior) before any protected data access, never filtered
     after the fact.

  6. Data classification (Task 7).  When the caller declared a specific
     `GateBRequest.data_class`, `policy_models.is_data_classification_permitted()`
     must find it in the matched rule's own `allowed_data_classes` - `deny`
     ("data_classification_not_allowed", Task 4's "requested classification
     is not allowed") otherwise; a caller authorized for `internal` is
     never automatically authorized for `restricted` just because they
     asked (`DataClassification` values carry no implied hierarchy - see
     that enum's own docstring). `is_data_classification_permitted()` is
     the one place this membership check is made at all (Task 7: "Do not
     hardcode behavior in multiple unrelated files") - Task 9's
     `disclosure.py` deliberately does *not* re-run this check per
     protected field (see `policy_models.py`'s "Task 7/8" docstring
     section for why: a disclosure profile's own per-field
     `DisclosureAction` is authoritative and can legitimately `redact`
     rather than `deny` a field whose classification exceeds what this
     stage authorized, `pii_disclosure_cases.json` PII-002).
     When the caller declared none at all and the rule authorizes more
     than one classification, Gate-B does not guess which one to grant -
     Task 10's `clarify` ("request references two allowed resource types
     and policy needs one selected") applies: this is exactly the kind of
     missing-but-*safe*-to-ask information Task 10 permits, never a
     role/tenant/permission the caller would have to self-assert to get
     past it.

  7. Allow.  Every stage resolved -- the trusted role, matched rule, and
     narrowed tenant/classification scope become the decision's
     `effective_*` fields (Task 5's own intersection), together with the
     matched rule's own `disclosure_profile` (Task 8/9's input, applied
     downstream, never rebuilt here) and `PolicyRegistry.policy_version`
     (Task 2's provenance).

Two guarantees are not any single stage above, because they are true of
the *whole* module rather than one decision point in it (Task 4):

  - "policy version invalid" never needs its own stage here: `GateB` can
    only ever be built from an already-loaded `PolicyRegistry`, and
    `PolicyRegistry.load()` (Task 2) already refuses to construct one from
    an invalid policy document at all (`PolicyLoadError`) - there is no
    code path in which a `GateB` instance's own `registry.policy_version`
    is anything other than a validated version string.

  - "do not infer permission from natural language" is likewise structural
    rather than a check: `authorize()`'s signature (`identity`,
    `gate_a_decision`, `lane_decision`, `requested: GateBRequest`) has no
    parameter that ever carries raw request text - every input is already
    a typed decision or a narrow, closed-shape request object by the time
    it reaches this module. There is no string anywhere in this file for a
    permission decision to be "inferred" from.

What this module deliberately does NOT do: it does not perform PII
detection or redaction itself (Task 8/9's `disclosure.py`/`redaction.py`
apply the `disclosure_profile` this module only names), it does not call
the Model Gateway or any protected retrieval/Mode-B path (Day 10 working
rule: "A model is not the authority for permission or disclosure
decisions" -- `authorize()` is pure, synchronous, typed-in/typed-out, with
no I/O of its own), and it does not read session memory at all -- its
signature has no parameter for it, which is what actually proves "session
memory cannot grant a permission" (Day 10 working rule) for this module,
not a runtime check against memory content.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from aico.api.identity import TrustedIdentity
from aico.control.errors import GateBError
from aico.control.models import GateADecision, GateAStatus, GateBDecision, GateBStatus, LaneDecision
from aico.control.ontology import LaneId
from aico.control.ontology_registry import OntologyRegistry
from aico.control.policy_models import DataClassification, TenantScopeKind, is_data_classification_permitted
from aico.control.policy_registry import PolicyRegistry


@dataclass(frozen=True)
class GateBRequest:
    """The caller-declared, untrusted "what am I asking to do" shape Gate-B
    narrows against trusted/governed scope -- never a source of
    authorization by itself (Day 10 working rule: "Tenant/user/role/
    permission values from request body are not trusted authorization
    input"). Notably absent: role, tenant *ownership*, permission,
    clearance -- there is no field here a caller could fill in to claim a
    wider identity than `TrustedIdentity` already establishes (Day 10 Task
    10: "Do not ask the user to provide: a role / a tenant ID to gain
    access / a permission name / a clearance level"). `tenant_ids` is
    deliberately the one *scope-narrowing* exception: a caller may ask to
    be bounded to a subset of tenants they might already trust into, never
    to be granted one they do not.

    `operation` records what the caller says they are trying to do (the
    supplied fixtures use `"read"` throughout); the committed lab policy's
    `PermissionRule` (Task 1) carries no separate "operation" field of its
    own to check this against, so it is not yet an enforcement dimension --
    kept only as declared, provenance-worthy context for Task 14's
    telemetry and a future policy version that does add operation-scoped
    rules, never silently authorized-against today."""

    operation: str = "read"
    data_class: DataClassification | None = None
    tenant_ids: tuple[str, ...] = ()


def _deny(
    *, reason_code: str, policy_version: str, role_id: str | None = None, intent_id: str | None = None,
    lane: LaneId | None = None, rule_id: str | None = None,
) -> GateBDecision:
    """Every deny path funnels through here so "nothing is granted on
    deny" (`GateBStatus`'s own docstring) is enforced in exactly one place
    -- every `effective_*`/`disclosure_profile` field left at its typed
    default (empty/`None`), never partially populated by a caller-visible
    accident of which stage rejected the request."""
    return GateBDecision(
        decision=GateBStatus.DENY,
        role_id=role_id,
        intent_id=intent_id,
        lane=lane,
        rule_id=rule_id,
        reason_code=reason_code,
        policy_version=policy_version,
    )


@dataclass
class GateB:
    """Gate-B: built once against a loaded `PolicyRegistry` (Task 2) and
    reused for every request. `authorize()` is the only public entry
    point -- see the module docstring for the fail-closed stages it runs.

    `ontology_registry` (Task 4, optional) enables the one defense-in-depth
    check `PolicyRegistry` alone cannot make at request time: that a
    `MATCHED` `GateADecision`'s own `intent_id` is still a real governed
    Mode-A intent. `None` (the default) skips that specific check without
    affecting any other stage -- see stage 2 in the module docstring."""

    registry: PolicyRegistry
    ontology_registry: OntologyRegistry | None = None
    _roles_by_id: dict = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Read once at construction, not per-call -- `PolicyRegistry.roles`
        # already returns a read-only tuple (Task 2); indexing it here is
        # purely a lookup convenience, it creates no new source of truth.
        self._roles_by_id = {role.role_id: role for role in self.registry.roles}

    def authorize(
        self,
        identity: TrustedIdentity | None,
        gate_a_decision: GateADecision,
        lane_decision: LaneDecision,
        requested: GateBRequest | None = None,
    ) -> GateBDecision:
        """Authorize one trusted request. Never raises for an ordinary
        input, well-formed or not (Task 4: `identity=None` included) --
        every outcome, including every deny reason and the one documented
        `clarify` case, is a normal, typed `GateBDecision` result (Day 10
        rule: fail closed with a typed result, never a fall-through). Only
        raises `GateBError` for an input that violates an invariant
        Gate-A/the policy registry already guarantee never actually
        happens (see `GateBError`'s own docstring) -- defensive, not a
        normal decision path."""
        requested = requested or GateBRequest()
        version = self.registry.policy_version

        # Stage 0 -- trusted identity present (Task 4).
        if identity is None:
            return _deny(reason_code="identity_missing", policy_version=version)

        # Stage 1 -- upstream must have actually resolved something.
        if gate_a_decision.status is not GateAStatus.MATCHED or gate_a_decision.intent_id is None:
            return _deny(
                reason_code=f"upstream_not_matched:{gate_a_decision.status.value}",
                policy_version=version,
            )
        if lane_decision.lane in (LaneId.BLOCK, LaneId.CLARIFY):
            return _deny(
                reason_code=f"upstream_lane_not_authorizable:{lane_decision.lane.value}",
                policy_version=version,
                intent_id=gate_a_decision.intent_id,
                lane=lane_decision.lane,
            )

        intent_id = gate_a_decision.intent_id
        lane = lane_decision.lane

        # Stage 2 -- governed intent (Task 4, defense-in-depth; optional).
        if self.ontology_registry is not None and not self.ontology_registry.has_intent(intent_id):
            return _deny(reason_code="unknown_intent", policy_version=version, lane=lane)

        # Stage 3 -- trusted role.
        if not identity.roles:
            return _deny(reason_code="role_missing", policy_version=version, intent_id=intent_id, lane=lane)
        role_id = identity.roles[0]
        role = self._roles_by_id.get(role_id)
        if role is None:
            return _deny(reason_code="unknown_role", policy_version=version, intent_id=intent_id, lane=lane)

        # Stage 4 -- matching rule.
        rule = self.registry.find_rule(role_id, intent_id, lane)
        if rule is None:
            return _deny(
                reason_code="no_matching_rule", policy_version=version, role_id=role_id, intent_id=intent_id, lane=lane
            )
        if not rule.allowed:
            return _deny(
                reason_code="rule_denied",
                policy_version=version,
                role_id=role_id,
                intent_id=intent_id,
                lane=lane,
                rule_id=rule.rule_id,
            )
        if rule.required_permission not in role.permissions:
            # Defense-in-depth: `find_rule` already matched this rule to
            # this exact `role_id`, so a committed policy authored
            # consistently (every fixture role/rule pair today) never
            # reaches this - but `Role.permissions` and
            # `PermissionRule.required_permission` are both real, governed
            # policy fields (Task 1's own required rule/role shape), and a
            # rule whose own declared `required_permission` its matched
            # role was never actually granted must still fail closed, not
            # be authorized on `rule.allowed` alone.
            return _deny(
                reason_code="permission_not_granted",
                policy_version=version,
                role_id=role_id,
                intent_id=intent_id,
                lane=lane,
                rule_id=rule.rule_id,
            )

        # Stage 5 -- tenant scope (Task 5): requested INTERSECT trusted INTERSECT policy.
        if role.tenant_scope is not TenantScopeKind.OWN_TENANT:  # pragma: no cover - no governed value exists yet
            raise GateBError(f"role {role_id!r} carries an unhandled tenant_scope {role.tenant_scope!r}")
        trusted_tenant_scope = frozenset({identity.tenant_id})
        requested_tenant_scope = frozenset(requested.tenant_ids) if requested.tenant_ids else trusted_tenant_scope
        effective_tenant_scope = trusted_tenant_scope & requested_tenant_scope
        if not effective_tenant_scope:
            return _deny(
                reason_code="cross_tenant_denied",
                policy_version=version,
                role_id=role_id,
                intent_id=intent_id,
                lane=lane,
                rule_id=rule.rule_id,
            )

        # Stage 6 -- data classification (Task 7).
        if requested.data_class is not None:
            if not is_data_classification_permitted(requested.data_class, rule.allowed_data_classes):
                return _deny(
                    reason_code="data_classification_not_allowed",
                    policy_version=version,
                    role_id=role_id,
                    intent_id=intent_id,
                    lane=lane,
                    rule_id=rule.rule_id,
                )
            effective_data_classes = (requested.data_class,)
        elif len(rule.allowed_data_classes) > 1:
            # Task 10: safe to ask "which of these did you mean" - the
            # bound (`rule.allowed_data_classes`) is already fixed by
            # policy either way, this never lets the caller claim more.
            return GateBDecision(
                decision=GateBStatus.CLARIFY,
                effective_tenant_scope=tuple(sorted(effective_tenant_scope)),
                role_id=role_id,
                intent_id=intent_id,
                lane=lane,
                rule_id=rule.rule_id,
                reason_code="data_class_selection_required",
                policy_version=version,
            )
        else:
            effective_data_classes = tuple(rule.allowed_data_classes)

        # Stage 7 -- allow.
        return GateBDecision(
            decision=GateBStatus.ALLOW,
            effective_tenant_scope=tuple(sorted(effective_tenant_scope)),
            effective_data_classes=effective_data_classes,
            effective_pii_policy=tuple(rule.allowed_pii_categories),
            disclosure_profile=rule.disclosure_profile,
            role_id=role_id,
            intent_id=intent_id,
            lane=lane,
            rule_id=rule.rule_id,
            reason_code="rule_allowed",
            policy_version=version,
        )
