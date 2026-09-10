"""
Day 10 Task 1 -- typed Gate-B policy model.

`gate_b_policy_requirements.md` / `disclosure_rules.md` (`day10_pack/`) are
the governed spec this module gives shape to: a typed, self-validating
`GateBPolicyDocument` (`policy_version` / `status` / `roles` / `permissions`
/ `data_classifications` / `pii_categories` / `disclosure_profiles` /
`rules`), matching this module's own top-level field list one-for-one with
`gate_b_policy_v1.json`. Task 2's `policy_registry.py` will load the
committed `policy/gate_b_policy.v1.json` through this document and wrap it
for read-only runtime access -- exactly how `ontology.py` (Day 9 Task 1)
relates to `ontology_registry.py` (Day 9 Task 2); the split and the
validation style deliberately mirror that module.

Every model here sets `extra="forbid"` (same Day 4/8/9 contract
convention): an unknown field in the committed policy is a policy defect,
not something silently dropped.

"Unchecked dictionaries are not acceptable at the policy boundary" (Day 10
Task 1) is read literally, the same way Day 9 Task 1 read the equivalent
ontology rule -- every "Required validation" bullet from
`gate_b_policy_requirements.md` is enforced *inside* this module's types:

    - policy version required          -> `GateBPolicyDocument.policy_version`
                                           is a required, non-empty field.
    - duplicate rule IDs rejected       -> `GateBPolicyDocument` model_validator.
    - unknown ontology intent rejected  -> `GateBPolicyDocument` model_validator,
                                           checked against `known_intent_ids`
                                           supplied via `model_validate(...,
                                           context=...)` -- see "Ontology
                                           cross-reference" below.
    - unknown lane rejected             -> `PermissionRule.lane` is typed as
                                           `aico.control.ontology.LaneId`, the
                                           same closed governed lane enum Day 9
                                           already defines; an unrecognized
                                           lane string cannot even parse.
    - unknown role/permission reference
      rejected                         -> `GateBPolicyDocument` model_validator
                                           checks `PermissionRule.role` against
                                           `roles`, and `Role.permissions` /
                                           `PermissionRule.required_permission`
                                           against the document's own closed
                                           `permissions` list.
    - unknown data classification
      rejected                        -> `PermissionRule.allowed_data_classes`
                                           is typed as `DataClassification`
                                           (closed enum) *and* cross-checked
                                           against the document's own declared
                                           `data_classifications` subset --
                                           the same two-layer check
                                           `Intent.allowed_lanes` gets against
                                           `OntologyDocument.lanes`.
    - unknown PII category rejected     -> same two-layer check, against
                                           `PiiCategory` / `pii_categories`.
    - unknown disclosure profile
      rejected                        -> `GateBPolicyDocument` model_validator
                                           checks `PermissionRule.disclosure_profile`
                                           against `disclosure_profiles`.
    - invalid status rejected           -> `LifecycleStatus` enum (reused from
                                           `ontology.py`) on the document, every
                                           `Role`, and every `PermissionRule`;
                                           `DisclosureAction` (closed enum) on
                                           every `DisclosureProfile.field_actions`
                                           value.
    - policy is read-only at runtime    -> these are plain immutable-by-convention
                                           Pydantic values; Task 2's registry is
                                           what guarantees no runtime code path
                                           can mutate a loaded policy (this
                                           module only defines the shape).

## Ontology cross-reference

`PermissionRule.intent_id` must reference a real governed Mode-A intent
(Day 9's `OntologyRegistry`), not merely a non-empty string -- but this
module intentionally does not import or depend on a specific
`OntologyRegistry` instance; that would make `policy_models.py` reach
across the Day 9/Day 10 boundary for something only a *loader* needs.
Instead `GateBPolicyDocument`'s validator reads an optional
`known_intent_ids: set[str]` out of Pydantic's own validation context
(`GateBPolicyDocument.model_validate(raw, context={"known_intent_ids":
{...}}))`. When the caller supplies it, every `rules[].intent_id` is
checked against it and an unrecognized one is rejected exactly like every
other unknown reference in this module. When no context is supplied (e.g.
a test building a throwaway document in isolation, the same allowance
`ontology.py`'s own docstring notes for `OntologyDocument`), that one check
is skipped -- everything else here validates unconditionally.

Task 2's `policy_registry.py` is expected to always load the committed
`OntologyRegistry` first and pass its governed intent ids as this context
when loading the committed `gate_b_policy.v1.json`, making the check
mandatory in practice for the one document that is ever actually served to
the rest of the control plane -- the same way `OntologyDocument`'s own
lane cross-check only *has* to hold for the committed registry file, even
though the type itself doesn't force every hand-built test document to
supply a `lanes` list that agrees with every intent's `allowed_lanes`.

## Not covered here

This module does not decide whether a given caller/role/intent/lane
combination is actually authorized for one request (that is Gate-B itself,
Task 3), and it does not perform redaction or build a disclosed view of a
protected record (Task 9's `disclosure.py`/`redaction.py`). It only
defines -- and self-validates -- what a governed Gate-B policy document is
allowed to look like.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from aico.control.ontology import LaneId, LifecycleStatus


class DataClassification(str, Enum):
    """The closed set of governed data classifications `disclosure_rules.md`
    names. A caller's matched rule declaring `internal` in
    `allowed_data_classes` never implies `restricted` is also allowed
    (Day 10 Task 7) -- each value here is independent, there is no implied
    ordering/hierarchy at the type level."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class PiiCategory(str, Enum):
    """The closed set of governed PII categories `disclosure_rules.md`
    names. `NONE` is itself a governed category (a field carrying no PII at
    all), not the absence of a value -- every governed field is expected to
    carry an explicit category, never an implicit default."""

    NONE = "none"
    CONTACT = "contact"
    PERSONAL_IDENTIFIER = "personal_identifier"
    FINANCIAL = "financial"
    SENSITIVE_PERSONAL = "sensitive_personal"


class DisclosureAction(str, Enum):
    """The closed set of disclosure actions a `DisclosureProfile` may
    assign to a field (`disclosure_rules.md`). The policy -- never a model
    -- decides which of these applies to a given field (Day 10 Task 8)."""

    ALLOW = "allow"
    REDACT = "redact"
    DENY = "deny"


class TenantScopeKind(str, Enum):
    """The closed set of governed tenant-scope kinds a `Role` may carry.
    `OWN_TENANT` is the only value the supplied `gate_b_policy_v1.json`
    fixture uses -- every governed role in this lab policy is scoped to
    the caller's own trusted tenant, never to another tenant or to every
    tenant (Day 10 Task 5: "Do not create hidden super-admin behavior").
    A future policy version that deliberately wants a governed
    multi-tenant/admin scope must add an explicit new value here (and a
    matching fixture role/rule + tests) -- this module never infers one
    that is not both typed and present in the committed policy."""

    OWN_TENANT = "own_tenant"


class Role(BaseModel):
    """A governed Gate-B role (`gate_b_policy_requirements.md`; fixture:
    `role_id` / `permissions` / `tenant_scope` / `status`). `permissions`
    is this role's own closed set of permission ids -- every entry is
    cross-checked against the document's top-level `permissions` list by
    `GateBPolicyDocument`'s validator below, the same "unknown permission
    reference rejected" rule a `PermissionRule.required_permission` gets.

    A role carrying a permission in this list does not by itself grant
    anything: `GateB` (Task 3) still requires a matching, `allowed=True`
    `PermissionRule` for the specific role/intent/lane combination -- see
    `permission_cases.json` PERM-002, where `supplier_reader` lacks
    `read_structured_supplier` entirely and a rule referencing it still
    exists with `allowed=false`, precisely to be deny-by-default even for
    a permission the role was never granted."""

    model_config = ConfigDict(extra="forbid")

    role_id: str = Field(min_length=1, description="Non-empty, unique role identifier.")
    permissions: list[str] = Field(
        default_factory=list,
        description="permission ids granted to this role; each must exist in the document's own `permissions`.",
    )
    tenant_scope: TenantScopeKind
    status: LifecycleStatus


class DisclosureProfile(BaseModel):
    """A governed disclosure profile (`disclosure_rules.md`; fixture:
    `profile_id` / `field_actions`). `field_actions` maps a protected
    record's own field name to the `DisclosureAction` a caller matched to
    this profile receives for that field -- the mechanism Task 9's safe
    disclosure/redaction layer is built against; this module only defines
    its governed shape."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(min_length=1, description="Non-empty, unique disclosure profile identifier.")
    field_actions: dict[str, DisclosureAction] = Field(
        default_factory=dict,
        description="Protected field name -> disclosure action for callers matched to this profile.",
    )


class PermissionRule(BaseModel):
    """A single governed Gate-B permission rule (`gate_b_policy_requirements.md`
    Task 1 field list; fixture: `rule_id` / `role` / `intent_id` / `lane` /
    `required_permission` / `allowed` / `allowed_data_classes` /
    `allowed_pii_categories` / `disclosure_profile` / `status`). This is
    the record `GateB` (Task 3) matches one trusted
    role/intent/lane/operation combination against to decide allow/deny
    and, when allowed, the bounded effective scope and disclosure profile
    to apply.

    `allowed=False` is itself a governed, first-class outcome (fixture
    `GB-R002`), not merely the absence of a matching rule -- Gate-B's
    deny-by-default behavior (Task 4) still applies when *no* rule matches
    at all; a matched-but-`allowed=False` rule is a distinct, equally
    deny, provenance-bearing case ("denied permission" in Task 16's test
    table)."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1, description="Non-empty, unique rule identifier.")
    role: str = Field(min_length=1, description="role_id this rule applies to; must exist in `roles`.")
    intent_id: str = Field(
        min_length=1,
        description="Governed Mode-A intent_id this rule applies to (Day 9 `OntologyRegistry`).",
    )
    lane: LaneId = Field(description="Governed lane this rule applies to.")
    required_permission: str = Field(
        min_length=1,
        description="permission id this rule is evaluated against; must exist in the document's own `permissions`.",
    )
    allowed: bool = Field(description="Whether this rule, once matched, authorizes the request.")
    allowed_data_classes: list[DataClassification] = Field(
        default_factory=list,
        description="Data classifications this rule authorizes; each must be declared in `data_classifications`.",
    )
    allowed_pii_categories: list[PiiCategory] = Field(
        default_factory=list,
        description="PII categories this rule authorizes; each must be declared in `pii_categories`.",
    )
    disclosure_profile: str = Field(
        min_length=1,
        description="profile_id applied when this rule allows the request; must exist in `disclosure_profiles`.",
    )
    status: LifecycleStatus


def _reject_duplicate_ids(kind: str, ids: list[str]) -> None:
    """Shared duplicate-id guard for `GateBPolicyDocument`'s validator --
    raises on the first repeat rather than accumulating a set silently and
    losing which kind of record (`role`/`permission`/`disclosure_profile`/
    `rule`) it was. Mirrors `ontology.py`'s identically named helper; kept
    as a separate copy so this module stays self-contained the way
    `ontology.py` is (Task 1 does not import Day 9 Task 1 internals)."""
    seen: set[str] = set()
    for identifier in ids:
        if identifier in seen:
            raise ValueError(f"duplicate {kind}_id: {identifier!r}")
        seen.add(identifier)


class GateBPolicyDocument(BaseModel):
    """The full versioned, typed Gate-B policy document
    (`gate_b_policy_requirements.md`; Task 1's top-level field list).
    Loaded and exposed read-only by Task 2's `policy_registry.py`; nothing
    at runtime is permitted to construct or mutate one from request or
    model output (Day 10 working rules: "Session memory cannot grant a
    permission" / "Permission scope is never repaired/widened by an
    LLM").

    `data_classifications` and `pii_categories` are this policy version's
    own *declared/enabled* subsets of the closed `DataClassification` /
    `PiiCategory` enums -- every rule's `allowed_data_classes` /
    `allowed_pii_categories` must be drawn from these declared subsets,
    exactly the two-layer check `OntologyDocument.lanes` /
    `Intent.allowed_lanes` already establishes in `ontology.py`."""

    model_config = ConfigDict(extra="forbid")

    policy_version: str = Field(min_length=1, description="Required governed policy version identifier.")
    status: LifecycleStatus
    roles: list[Role] = Field(default_factory=list)
    permissions: list[str] = Field(
        default_factory=list, description="Closed set of permission ids this policy version governs."
    )
    data_classifications: list[DataClassification] = Field(
        default_factory=list, description="Data classifications enabled for this policy version."
    )
    pii_categories: list[PiiCategory] = Field(
        default_factory=list, description="PII categories enabled for this policy version."
    )
    disclosure_profiles: list[DisclosureProfile] = Field(default_factory=list)
    rules: list[PermissionRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_policy_integrity(self, info: ValidationInfo) -> GateBPolicyDocument:
        _reject_duplicate_ids("role", [r.role_id for r in self.roles])
        _reject_duplicate_ids("permission", list(self.permissions))
        _reject_duplicate_ids("disclosure_profile", [p.profile_id for p in self.disclosure_profiles])
        _reject_duplicate_ids("rule", [r.rule_id for r in self.rules])

        role_ids = {r.role_id for r in self.roles}
        permission_ids = set(self.permissions)
        profile_ids = {p.profile_id for p in self.disclosure_profiles}
        enabled_data_classes = set(self.data_classifications)
        enabled_pii_categories = set(self.pii_categories)

        for role in self.roles:
            unknown_permissions = [p for p in role.permissions if p not in permission_ids]
            if unknown_permissions:
                raise ValueError(
                    f"role {role.role_id!r} references unknown permission(s): {unknown_permissions}"
                )

        for rule in self.rules:
            if rule.role not in role_ids:
                raise ValueError(f"rule {rule.rule_id!r} references unknown role {rule.role!r}")
            if rule.required_permission not in permission_ids:
                raise ValueError(
                    f"rule {rule.rule_id!r} references unknown permission {rule.required_permission!r}"
                )
            if rule.disclosure_profile not in profile_ids:
                raise ValueError(
                    f"rule {rule.rule_id!r} references unknown disclosure_profile {rule.disclosure_profile!r}"
                )
            unknown_classes = [c.value for c in rule.allowed_data_classes if c not in enabled_data_classes]
            if unknown_classes:
                raise ValueError(
                    f"rule {rule.rule_id!r} references data classification(s) not enabled for this "
                    f"policy version: {unknown_classes}"
                )
            unknown_pii = [c.value for c in rule.allowed_pii_categories if c not in enabled_pii_categories]
            if unknown_pii:
                raise ValueError(
                    f"rule {rule.rule_id!r} references PII categor(ies) not enabled for this "
                    f"policy version: {unknown_pii}"
                )

        context = info.context or {}
        known_intent_ids = context.get("known_intent_ids")
        if known_intent_ids is not None:
            for rule in self.rules:
                if rule.intent_id not in known_intent_ids:
                    raise ValueError(
                        f"rule {rule.rule_id!r} references unknown ontology intent {rule.intent_id!r}"
                    )

        return self
