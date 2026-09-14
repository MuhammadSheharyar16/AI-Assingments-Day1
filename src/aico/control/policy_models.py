"""
Day 10 Task 1 -- typed Gate-B policy model.
Day 12 Task 2 -- typed Gate-D policy model (`GateDPolicyDocument` and its
sub-policies, appended near the end of this file). Kept in this
generically-named module rather than a new `gate_d_policy_models.py` --
Day 12's own required structure names no separate file for it (unlike
Gate-C, which got its own `evidence/policy.py` because it governs a
different concern, evidence quality, living under `src/aico/evidence/`);
Gate-D's policy governs the same kind of thing this module already owns,
the final release/disclosure boundary's own governed configuration, so it
belongs beside `GateBPolicyDocument`, not in a third file. See that
class's own docstring for its full "Required validation" mapping.

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

## Task 7/8 -- shared policy-decision primitives

Three small, pure functions live here rather than in `gate_b.py` or
`disclosure.py`, precisely so none of them ever gets re-implemented
elsewhere (Task 7: "Do not hardcode behavior in multiple unrelated
files"):

    - `is_data_classification_permitted()` (Task 7) -- is a
      `DataClassification` a member of an authorized set. `GateB.authorize()`
      (Task 3) is this function's only caller: the *requested*
      classification, checked against one matched
      `PermissionRule.allowed_data_classes`, before a `GateBDecision`
      exists at all.
    - `is_pii_category_permitted()` (Task 8) -- the identical membership
      check for `PiiCategory`. Used both by policy validation and, a
      second time, by Task 9's `disclosure.py` as a defense-in-depth
      safety net: each protected field's own governed PII category,
      checked against an already-decided `GateBDecision.effective_pii_policy`
      (which -- unlike `effective_data_classes` -- is never narrower than
      the matched rule's own `allowed_pii_categories`, so this check can
      only ever catch a genuinely disallowed category, never a false
      positive against a field the rule does authorize).
    - `resolve_disclosure_action()` (Task 8) -- the one deterministic
      `(DisclosureProfile, field_name) -> DisclosureAction` lookup, fail
      closed (`DENY`) for any field the matched profile does not declare.
      This -- not a fresh `is_data_classification_permitted()` check per
      field -- is `disclosure.py`'s primary, authoritative decision for a
      *known*, declared field: the committed profiles are deliberately
      authored to sometimes `redact` (not `deny`) a field whose own
      classification exceeds what the matched rule's `allowed_data_classes`
      would otherwise suggest (`pii_disclosure_cases.json` PII-002:
      `contact_email` is tagged `confidential` while `GB-R001` only
      authorizes `public`/`internal`, yet `policy_reader` still redacts
      rather than denies it) -- re-deriving that decision from
      `allowed_data_classes` in `disclosure.py` would silently override
      the policy author's own, deliberate per-field choice. Data
      classification is authorized once, at Gate-B time (Task 7); it is
      not re-checked per field at disclosure time.

## Not covered here

This module does not decide whether a given caller/role/intent/lane
combination is actually authorized for one request (that is Gate-B itself,
Task 3), and it does not perform redaction (the actual masked-value
transformation) or build a disclosed view of a protected record (Task 9's
`disclosure.py`/`redaction.py`) -- `resolve_disclosure_action()` decides
*which* action applies to a field, it does not apply that action to a
value. This module only defines -- and self-validates -- what a governed
Gate-B policy document is allowed to look like, plus the small set of
pure decision primitives built directly on that governed shape.
"""
from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from aico.contracts.models import AnswerStatus
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


def is_data_classification_permitted(
    data_class: DataClassification, allowed_data_classes: Sequence[DataClassification]
) -> bool:
    """The one place a data classification is ever checked against an
    authorized set (Day 10 Task 7: "Do not hardcode behavior in multiple
    unrelated files"). Deliberately just membership -- `DataClassification`
    values carry no implied ordering/hierarchy (see the enum's own
    docstring), so `internal` being permitted never implies `restricted`
    is too, and this function does not invent one.

    `gate_b.py`'s `GateB.authorize()` is this function's caller: the
    *requested* classification, checked against one matched
    `PermissionRule.allowed_data_classes`, before a `GateBDecision` exists
    at all. Task 9's `disclosure.py` does *not* call this a second time
    per field -- see this module's "Task 7/8" docstring section for why a
    field-level re-check against classification would silently override a
    disclosure profile's own deliberate per-field choice
    (`pii_disclosure_cases.json` PII-002)."""
    return data_class in allowed_data_classes


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


def is_pii_category_permitted(pii_category: PiiCategory, allowed_pii_categories: Sequence[PiiCategory]) -> bool:
    """Day 10 Task 8's PII-category analog of Task 7's
    `is_data_classification_permitted()` -- the one place a PII category is
    ever checked against an authorized set. Deliberately just membership,
    for the identical reason: `PiiCategory` values carry no implied
    ordering (`sensitive_personal` being disallowed says nothing about
    `contact`, and vice versa).

    Unlike `is_data_classification_permitted()`, this one genuinely is
    called twice across the pipeline: `GateBPolicyDocument`'s own
    validation reasons about a matched rule's `allowed_pii_categories`
    indirectly (Task 1), and Task 9's `disclosure.py` calls this function
    a second time, per protected field, as a defense-in-depth check
    against an already-decided `GateBDecision.effective_pii_policy` --
    safe to do for PII specifically (unlike data classification) because
    `effective_pii_policy` is always the matched rule's complete
    `allowed_pii_categories`, never narrowed to a single caller-requested
    category the way `effective_data_classes` sometimes is; a category
    genuinely absent from it is always safe to deny, never a false
    positive against a field the matched rule does authorize."""
    return pii_category in allowed_pii_categories


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
    its governed shape and, via `resolve_disclosure_action()` below, its
    one deterministic lookup rule."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(min_length=1, description="Non-empty, unique disclosure profile identifier.")
    field_actions: dict[str, DisclosureAction] = Field(
        default_factory=dict,
        description="Protected field name -> disclosure action for callers matched to this profile.",
    )


def resolve_disclosure_action(profile: DisclosureProfile, field_name: str) -> DisclosureAction:
    """Day 10 Task 8 -- the one place a protected field name is ever
    resolved to a `DisclosureAction`. "The policy, not the model, decides
    the action": this is a pure, deterministic lookup into the matched
    decision's own governed `disclosure_profile.field_actions` -- nothing
    here reads a model's output, a caller's request, or session memory,
    and there is no parameter through which any of those three could ever
    reach it (`gate_b_policy_requirements.md` / Day 10 working rule:
    "Redaction performed by LLM" is a listed failure mode this function
    structurally cannot exhibit).

    Fail closed for a field the profile never declares an action for at
    all -- `DENY`, never `ALLOW`. `field_actions` is an exhaustive-by-
    declaration map: this lab's committed profiles do declare `deny` for
    every protected field they know is sensitive (`tax_identifier`,
    `bank_account`, `personal_notes`), but an *undeclared* field (a typo,
    a new column a future data source adds, or a field name a model
    "asks" this function to disclose that was never a real governed field
    at all) is exactly `gate_b_policy_requirements.md`'s "restricted
    sensitive field cannot be exposed because the model asked for it" --
    the default here can only ever narrow exposure, never grant it.

    Deterministic by construction: the same `(profile, field_name)` input
    always resolves to the same `DisclosureAction`, and produces no side
    effect and performs no I/O -- `resolve_disclosure_action` cannot ever
    disagree with itself between two calls (Task 8's "Redaction must be
    deterministic" / "allowed PII category follows policy" reduce
    directly to this function returning the one value `field_actions`
    (or its `DENY` default) already fixes)."""
    return profile.field_actions.get(field_name, DisclosureAction.DENY)


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


# ══════════════════════════════════════════════════════════════════════
# Day 12 Task 2 -- the Gate-D policy.
# ══════════════════════════════════════════════════════════════════════
#
# `gate_d_policy_requirements.md` (`data/day12_pack/`) names six governed
# concepts: `policy_version` / `allowed_response_statuses` /
# `citation_policy` / `quality_policy` / `disclosure_policy` /
# `latency_budgets`, plus one final-release behavior, `safe_failure` --
# the committed `policy/gate_d_policy.v1.json` (unmodified from
# `data/day12_pack/fixtures/gate_d_policy_v1.json`, the same
# "fixture copied in verbatim, never edited to make the implementation
# pass" convention `policy/gate_c_policy.v1.json` already follows) matches
# this field list one-for-one. `GateDPolicyDocument` below is that
# document's typed shape; `GateDPolicyRegistry` (`policy_registry.py`)
# loads it read-only for Gate-D (Day 12 Task 10).
#
# Unlike Gate-B's/Gate-C's own policies, Gate-D's v1 schema is
# deliberately *flat*: one `citation_policy`/`quality_policy`/
# `disclosure_policy`/`latency_budgets`/`safe_failure` object apiece,
# never a *list* of named, independently-versioned sub-rules the way
# Gate-B's `rules`/Gate-C's `intent_requirements` are. That shape is the
# committed fixture's, not a simplification made here -- every "Required
# validation" bullet from `gate_d_policy_requirements.md` /
# `Day 12 Task.pdf` (Task 2) is still enforced, just against the concrete
# fields this flatter schema actually has:
#
#     - policy version required          -> `GateDPolicyDocument.
#                                            policy_version` is a
#                                            required, non-empty field.
#     - duplicate rule IDs rejected       -> this policy version declares
#                                            no separate list of named,
#                                            independently-identified
#                                            rules (contrast Gate-B's
#                                            `rules[].rule_id`/Gate-C's
#                                            `intent_requirements[].
#                                            rule_id`) -- the one place a
#                                            duplicate *identifier* could
#                                            appear here is a repeated
#                                            entry in
#                                            `allowed_response_statuses`
#                                            itself (each entry is, in
#                                            effect, the id of one
#                                            governed "this status may be
#                                            released" rule); rejected by
#                                            `GateDPolicyDocument`'s own
#                                            `model_validator` below. A
#                                            future policy version that
#                                            introduces named per-category
#                                            sub-rules would extend this
#                                            check the same way Gate-C's
#                                            own duplicate-rule-id check
#                                            was added once its policy
#                                            actually grew a `rule_id`
#                                            field.
#     - invalid response status rejected  -> `allowed_response_statuses`
#                                            is typed
#                                            `tuple[AnswerStatus, ...]` --
#                                            the identical closed,
#                                            governed enum
#                                            `FinalResponseCandidate.
#                                            candidate_status`
#                                            (`final_response.py`) already
#                                            uses; an entry outside
#                                            `{answered,
#                                            insufficient_evidence}`
#                                            cannot even parse.
#     - invalid/negative budget rejected  -> `LatencyBudgets.
#                                            max_total_latency_ms`/
#                                            `max_model_latency_ms` are
#                                            each `Field(gt=0)`.
#     - unknown disclosure profile/rule
#       reference rejected               -> v1's `disclosure_policy` is a
#                                            fixed set of boolean
#                                            enforcement flags -- it names
#                                            no specific `profile_id`
#                                            itself for this document to
#                                            cross-check at load time (a
#                                            *candidate*'s own
#                                            `gate_b_disclosure_profile`,
#                                            Task 1, is what later gets
#                                            checked against a real
#                                            profile id, at Gate-D
#                                            *decision* time, not against
#                                            this static document). What
#                                            this document's registry
#                                            *does* cross-reference, the
#                                            same layering Gate-C's own
#                                            registry draws against the
#                                            source/ontology registries it
#                                            loads alongside its policy,
#                                            is Gate-B's real governed
#                                            `disclosure_profiles` list --
#                                            see `GateDPolicyRegistry.
#                                            load()`'s `gate_b_policy`
#                                            parameter and
#                                            `has_disclosure_profile()`
#                                            (`policy_registry.py`): Gate-D
#                                            (Task 6/10) is expected to
#                                            reject a candidate naming a
#                                            `gate_b_disclosure_profile`
#                                            that registry does not
#                                            recognize as an "unknown
#                                            disclosure profile reference"
#                                            exactly this bullet names.
#     - invalid quality policy rejected   -> `QualityPolicy.
#                                            max_answer_chars` is
#                                            `Field(gt=0)`; every other
#                                            `quality_policy`/
#                                            `citation_policy` field is a
#                                            plain, unambiguous `bool`.
#     - policy is read-only at runtime    -> the identical convention
#                                            every other governed registry
#                                            in this codebase already
#                                            gives -- see
#                                            `GateDPolicyRegistry`'s own
#                                            docstring.


class CitationPolicy(BaseModel):
    """Gate-D's final citation-reconciliation policy (Day 12 Task 3):
    whether an `answered` candidate must carry at least one citation,
    whether every citation must resolve to Gate-C-approved evidence, and
    whether a candidate carrying even one invalid citation alongside valid
    ones must fail closed rather than have the invalid one silently
    dropped (working rule: "Do not silently delete an invalid citation and
    return the remaining answer as trusted")."""

    model_config = ConfigDict(extra="forbid")

    answered_requires_citation: bool = Field(
        description="Whether an `answered` candidate must carry at least one citation."
    )
    citations_must_be_gate_c_approved: bool = Field(
        description="Whether every final citation must reconcile with Gate-C's validated_evidence_ids."
    )
    reject_mixed_valid_invalid: bool = Field(
        description="Whether one invalid citation fails the whole candidate, even alongside valid ones."
    )


class QualityPolicy(BaseModel):
    """Gate-D's final deterministic quality policy (Day 12 Task 5):
    `max_answer_chars` is the one named/configurable output-size ceiling
    the assignment requires ("The Gate-D policy defines named/configurable
    output limits. Do not invent one combined opaque 'quality score'.") --
    `gt=0`, Task 2's own "invalid quality policy rejected" case for a
    zero/negative ceiling. The remaining three flags gate whether each of
    Task 5's other deterministic checks (nonempty `answered` text, an
    already-passed typed contract, an already-passed semantic validation)
    is actually enforced for this policy version."""

    model_config = ConfigDict(extra="forbid")

    max_answer_chars: int = Field(gt=0, description="Maximum candidate_answer length Gate-D will release.")
    answered_must_be_nonempty: bool = Field(description="Whether an `answered` candidate must be nonempty.")
    contract_must_pass: bool = Field(description="Whether Gate-D requires contract_validation_status == passed.")
    semantic_validation_must_pass: bool = Field(
        description="Whether Gate-D requires semantic_validation_status == passed."
    )


class GateDDisclosurePolicy(BaseModel):
    """Gate-D's final disclosure-enforcement policy (Day 12 Task 6/7).
    Named `GateDDisclosurePolicy`, not `DisclosurePolicy`, to avoid any
    confusion with Gate-B's own `DisclosureProfile` (a *profile*, a named
    per-field action map) -- this is a flat set of enforcement toggles for
    Gate-D's own final-text checks, a different governed shape entirely."""

    model_config = ConfigDict(extra="forbid")

    enforce_gate_b_profile: bool = Field(
        description="Whether Gate-D re-validates the final text against the candidate's own Gate-B disclosure profile."
    )
    block_secret_patterns: bool = Field(
        description="Whether Gate-D's deterministic secret/protected-value detector (Task 7) runs."
    )
    block_hidden_prompt_markers: bool = Field(
        description="Whether Gate-D blocks release of hidden/system-prompt marker content."
    )


class LatencyBudgets(BaseModel):
    """Gate-D's deterministic latency-budget policy (Day 12 Task 8).
    `threshold_is_inclusive` decides Task 8's own "exactly at threshold"
    case: `true` (the committed v1 value) means a candidate measured at
    exactly `max_total_latency_ms`/`max_model_latency_ms` still passes --
    `latency_budget_cases.json` LAT12-002 (`total_latency_ms=2500`,
    `model_latency_ms=1400`, both exactly at budget) expects `allow`, not
    `safe_failure`."""

    model_config = ConfigDict(extra="forbid")

    max_total_latency_ms: int = Field(gt=0, description="Hard ceiling on FinalResponseCandidate.elapsed_ms.")
    max_model_latency_ms: int = Field(gt=0, description="Hard ceiling on FinalResponseCandidate.model_latency_ms.")
    threshold_is_inclusive: bool = Field(
        description="Whether a candidate measured at exactly the ceiling still passes (>=/<=) or must be strictly under (>/<)."
    )


class SafeFailureSpec(BaseModel):
    """The one controlled, fixed safe-failure response Gate-D returns when
    a candidate cannot be released (Day 12 Task 9). `status` is pinned to
    the literal `"safe_failure"` -- the same closed value Task 10's own
    `GateDDecision.decision` enum will carry for this outcome -- so a
    policy document could never declare a safe-failure status this
    codebase's own decision contract does not recognize. `code`/`message`
    are both non-empty, fixed/controlled text (working rule: "Safe failure
    text itself must be fixed/controlled and must not echo unsafe
    generated content") -- never text derived from a candidate answer."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["safe_failure"] = Field(description="Pinned to Gate-D's own safe-failure decision value.")
    code: str = Field(min_length=1, description="Stable, sanitized safe-failure error code (e.g. FINAL_RESPONSE_REJECTED).")
    message: str = Field(min_length=1, description="Fixed, controlled caller-facing safe-failure message.")


class GateDPolicyDocument(BaseModel):
    """The full versioned, typed Gate-D policy document (Day 12 Task 2's
    field list). Loaded and exposed read-only by `GateDPolicyRegistry`
    (`policy_registry.py`); nothing at runtime is permitted to construct
    or mutate one from a request, a model response, or session memory --
    the identical guarantee `GateBPolicyDocument`/`GateCPolicyDocument`
    give their own governed data. See this module's own "Day 12 Task 2"
    section header above for the full Required-validation-bullet mapping."""

    model_config = ConfigDict(extra="forbid")

    policy_version: str = Field(min_length=1, description="Required governed Gate-D policy version identifier.")
    status: LifecycleStatus
    allowed_response_statuses: tuple[AnswerStatus, ...] = Field(
        min_length=1, description="The closed set of candidate_status values Gate-D may ever release."
    )
    citation_policy: CitationPolicy
    quality_policy: QualityPolicy
    disclosure_policy: GateDDisclosurePolicy
    latency_budgets: LatencyBudgets
    safe_failure: SafeFailureSpec

    @model_validator(mode="after")
    def _validate_no_duplicate_response_statuses(self) -> GateDPolicyDocument:
        seen: set[AnswerStatus] = set()
        for status in self.allowed_response_statuses:
            if status in seen:
                raise ValueError(f"duplicate allowed_response_statuses entry: {status.value!r}")
            seen.add(status)
        return self
