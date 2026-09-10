"""
Day 10 Task 1/2 -- the typed Gate-B policy model
(`src/aico/control/policy_models.py`) and the policy registry that loads it
(`src/aico/control/policy_registry.py`).
Day 10 Task 7/8 -- the shared, pure policy-decision primitives built
directly on that typed model: `is_data_classification_permitted()`,
`is_pii_category_permitted()`, `resolve_disclosure_action()`.

Task 1 section proves the acceptance-relevant behaviors of
`GateBPolicyDocument` / `Role` / `PermissionRule` / `DisclosureProfile`
directly against Pydantic, the same way `test_day09_ontology.py`'s Task 1
section proves `OntologyDocument` directly before `OntologyRegistry`
(Task 2) is exercised:

  - the supplied fixture (`day10_pack/fixtures/gate_b_policy_v1.json`,
    copied verbatim into `data/day10_pack/fixtures/`) loads into nested
    typed objects, not dicts;
  - every "Required validation" bullet from
    `data/day10_pack/gate_b_policy_requirements.md` is rejected when
    violated: missing/empty policy version, duplicate rule id, unknown
    ontology intent, unknown lane, unknown role/permission reference,
    unknown data classification, unknown PII category, unknown disclosure
    profile, invalid status;
  - the extensions this module documents beyond that minimum list
    (duplicate role id, duplicate permission id, duplicate disclosure
    profile id, a role referencing an unknown permission) are rejected
    too;
  - `extra="forbid"` rejects an unchecked/unknown field anywhere in the
    document.

Task 2 section proves `PolicyRegistry` end to end: loading the real
committed file (success and every documented failure mode, including the
registry's own ambiguous-rule-combination check), that its collection/
version accessors are read-only, that `get_role` / `get_disclosure_profile`
/ `get_rule` correctly resolve or reject against the real policy's ids, and
that `find_rule` deterministically matches (or fails to match) one
role/intent/lane combination.

Task 7 section proves `is_data_classification_permitted()` as a unit
(membership, no implied hierarchy, empty-set behavior) and that
`gate_b.py` actually calls it rather than re-implementing an inline
membership check -- see `test_day10_gate_b.py` for the integration-level
allow/deny behavior this function drives through `GateB.authorize()`.

Task 8 section proves `resolve_disclosure_action()` against every real
`pii_disclosure_cases.json` case (PII-001..006) plus every "Required
behavior" bullet `gate_b_policy_requirements.md`'s Task 8 section names --
allowed non-PII passes, allowed PII follows policy, deterministic
redaction, deny-by-default for an undeclared/"model-requested" field -- and
`is_pii_category_permitted()` as a unit, the PII analog of Task 7's data-
classification helper. The actual masked-*value* transformation and the
full disclosed-view builder are Task 9's `disclosure.py`/`redaction.py`,
not implemented here.

Gate-B itself (Task 3+) is not implemented yet and is out of scope here --
this file only proves the typed policy model and registry boundary.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aico.control.errors import PolicyLoadError, PolicyLookupError
from aico.control.ontology import LaneId, LifecycleStatus, OntologyDocument
from aico.control.ontology_registry import OntologyRegistry
from aico.control.policy_models import (
    DataClassification,
    DisclosureAction,
    DisclosureProfile,
    GateBPolicyDocument,
    PermissionRule,
    PiiCategory,
    Role,
    TenantScopeKind,
    is_data_classification_permitted,
    is_pii_category_permitted,
    resolve_disclosure_action,
)
from aico.control.policy_registry import DEFAULT_POLICY_PATH, PolicyRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_POLICY_PATH = REPO_ROOT / "policy" / "gate_b_policy.v1.json"
COMMITTED_ONTOLOGY_PATH = REPO_ROOT / "ontology" / "registry.v1.json"
PACK_FIXTURE_PATH = REPO_ROOT / "data" / "day10_pack" / "fixtures" / "gate_b_policy_v1.json"
PII_DISCLOSURE_CASES_PATH = REPO_ROOT / "data" / "day10_pack" / "fixtures" / "pii_disclosure_cases.json"

PII_DISCLOSURE_CASES = json.loads(PII_DISCLOSURE_CASES_PATH.read_text(encoding="utf-8"))["cases"]

# The real Mode-A ontology intents this fixture's rules reference
# (`ontology/registry.v1.json`) -- passed as validation context exactly the
# way Task 2's `policy_registry.py` is documented to, proving the
# "unknown ontology intent rejected" mechanism without importing
# `OntologyRegistry` itself into this test module's fixture-building.
KNOWN_ONTOLOGY_INTENT_IDS = {"INT-POLICY-QUESTION", "INT-STRUCTURED-LOOKUP", "INT-HELP"}


def _load_pack_fixture_dict() -> dict:
    return json.loads(PACK_FIXTURE_PATH.read_text(encoding="utf-8"))


def _load_committed_policy_dict() -> dict:
    return json.loads(COMMITTED_POLICY_PATH.read_text(encoding="utf-8"))


def _load_committed_ontology_registry() -> OntologyRegistry:
    return OntologyRegistry.load(COMMITTED_ONTOLOGY_PATH)


def _minimal_valid_policy() -> dict:
    """A small, hand-built valid Gate-B policy document for isolated
    negative-path mutation tests, distinct from the full pack fixture so
    each test only ever changes the one thing it is proving is rejected."""
    return {
        "policy_version": "1.0",
        "status": "active",
        "roles": [
            {
                "role_id": "supplier_reader",
                "permissions": ["read_policy"],
                "tenant_scope": "own_tenant",
                "status": "active",
            }
        ],
        "permissions": ["read_policy", "read_structured_supplier"],
        "data_classifications": ["public", "internal"],
        "pii_categories": ["none", "contact"],
        "disclosure_profiles": [
            {
                "profile_id": "policy_reader",
                "field_actions": {
                    "supplier_name": "allow",
                    "contact_email": "redact",
                    "tax_identifier": "deny",
                },
            }
        ],
        "rules": [
            {
                "rule_id": "GB-R001",
                "role": "supplier_reader",
                "intent_id": "INT-POLICY-QUESTION",
                "lane": "rag",
                "required_permission": "read_policy",
                "allowed": True,
                "allowed_data_classes": ["public", "internal"],
                "allowed_pii_categories": ["none", "contact"],
                "disclosure_profile": "policy_reader",
                "status": "active",
            }
        ],
    }


# ---------------------------------------------------------------------------
# Fixture loads into typed objects
# ---------------------------------------------------------------------------


def test_committed_policy_matches_pack_fixture_verbatim():
    """`policy/gate_b_policy.v1.json` must be the same governed document
    the pack shipped -- Task 1/2 do not get to quietly edit fixture data
    to make the model happy (`day10_pack/README.md`: "Do not edit a
    failing fixture to make the implementation pass")."""
    committed = json.loads(COMMITTED_POLICY_PATH.read_text(encoding="utf-8"))
    assert committed == _load_pack_fixture_dict()


def test_pack_fixture_loads_into_typed_objects():
    document = GateBPolicyDocument.model_validate(_load_pack_fixture_dict())

    assert isinstance(document, GateBPolicyDocument)
    assert document.policy_version == "1.0"
    assert document.status is LifecycleStatus.ACTIVE

    assert len(document.roles) == 3
    assert all(isinstance(r, Role) for r in document.roles)
    supplier_reader = next(r for r in document.roles if r.role_id == "supplier_reader")
    assert supplier_reader.permissions == ["read_policy"]
    assert supplier_reader.tenant_scope is TenantScopeKind.OWN_TENANT

    assert set(document.permissions) == {"read_policy", "read_structured_supplier", "read_confidential"}
    assert set(document.data_classifications) == set(DataClassification)
    assert set(document.pii_categories) == set(PiiCategory)

    assert len(document.disclosure_profiles) == 3
    assert all(isinstance(p, DisclosureProfile) for p in document.disclosure_profiles)
    policy_reader = next(p for p in document.disclosure_profiles if p.profile_id == "policy_reader")
    assert policy_reader.field_actions["contact_email"] is DisclosureAction.REDACT
    assert policy_reader.field_actions["tax_identifier"] is DisclosureAction.DENY

    assert len(document.rules) == 5
    assert all(isinstance(r, PermissionRule) for r in document.rules)
    gb_r002 = next(r for r in document.rules if r.rule_id == "GB-R002")
    assert gb_r002.allowed is False
    assert gb_r002.lane is LaneId.MODE_B
    gb_r005 = next(r for r in document.rules if r.rule_id == "GB-R005")
    assert gb_r005.allowed is True
    assert DataClassification.CONFIDENTIAL in gb_r005.allowed_data_classes
    assert DataClassification.RESTRICTED not in gb_r005.allowed_data_classes


def test_minimal_valid_policy_is_accepted():
    """Sanity check on the isolated fixture the negative-path tests below
    mutate -- if this ever stops validating, every test that copies it is
    testing something other than what it claims."""
    document = GateBPolicyDocument.model_validate(_minimal_valid_policy())
    assert document.policy_version == "1.0"


# ---------------------------------------------------------------------------
# Required validation: policy version required
# ---------------------------------------------------------------------------


def test_missing_policy_version_rejected():
    data = _minimal_valid_policy()
    del data["policy_version"]
    with pytest.raises(ValidationError):
        GateBPolicyDocument.model_validate(data)


def test_empty_policy_version_rejected():
    data = _minimal_valid_policy()
    data["policy_version"] = ""
    with pytest.raises(ValidationError):
        GateBPolicyDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Required validation: duplicate rule IDs rejected
# ---------------------------------------------------------------------------


def test_duplicate_rule_id_rejected():
    data = _minimal_valid_policy()
    data["rules"].append(copy.deepcopy(data["rules"][0]))
    with pytest.raises(ValidationError, match="duplicate rule_id"):
        GateBPolicyDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Required validation: unknown ontology intent rejected (via context)
# ---------------------------------------------------------------------------


def test_rule_referencing_known_ontology_intent_accepted_with_context():
    data = _minimal_valid_policy()
    document = GateBPolicyDocument.model_validate(data, context={"known_intent_ids": KNOWN_ONTOLOGY_INTENT_IDS})
    assert document.rules[0].intent_id == "INT-POLICY-QUESTION"


def test_rule_referencing_unknown_ontology_intent_rejected_with_context():
    data = _minimal_valid_policy()
    data["rules"][0]["intent_id"] = "INT-NOT-REGISTERED"
    with pytest.raises(ValidationError, match="unknown ontology intent"):
        GateBPolicyDocument.model_validate(data, context={"known_intent_ids": KNOWN_ONTOLOGY_INTENT_IDS})


def test_rule_referencing_unknown_ontology_intent_accepted_without_context():
    """Without a supplied `known_intent_ids` context, the ontology
    cross-check is skipped -- documented, deliberate behavior (see
    `policy_models.py`'s "Ontology cross-reference" docstring section),
    not silently permissive by accident. Task 2's `policy_registry.py` is
    what makes this check mandatory in practice for the one document that
    is ever actually served to the rest of the control plane."""
    data = _minimal_valid_policy()
    data["rules"][0]["intent_id"] = "INT-NOT-REGISTERED"
    document = GateBPolicyDocument.model_validate(data)
    assert document.rules[0].intent_id == "INT-NOT-REGISTERED"


def test_pack_fixture_rules_all_reference_real_ontology_intents():
    """The real, committed fixture must pass the ontology cross-check
    against the real committed Mode-A registry's intent ids -- proves the
    two Day 9/Day 10 resources actually agree, not just that the mechanism
    exists in isolation."""
    document = GateBPolicyDocument.model_validate(
        _load_pack_fixture_dict(), context={"known_intent_ids": KNOWN_ONTOLOGY_INTENT_IDS}
    )
    assert {r.intent_id for r in document.rules} <= KNOWN_ONTOLOGY_INTENT_IDS


# ---------------------------------------------------------------------------
# Required validation: unknown lane rejected
# ---------------------------------------------------------------------------


def test_rule_with_ungoverned_lane_string_rejected():
    data = _minimal_valid_policy()
    data["rules"][0]["lane"] = "made_up_lane"
    with pytest.raises(ValidationError):
        GateBPolicyDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Required validation: unknown role/permission reference rejected
# ---------------------------------------------------------------------------


def test_rule_referencing_unknown_role_rejected():
    data = _minimal_valid_policy()
    data["rules"][0]["role"] = "ghost_role"
    with pytest.raises(ValidationError, match="unknown role"):
        GateBPolicyDocument.model_validate(data)


def test_rule_referencing_unknown_required_permission_rejected():
    data = _minimal_valid_policy()
    data["rules"][0]["required_permission"] = "ghost_permission"
    with pytest.raises(ValidationError, match="unknown permission"):
        GateBPolicyDocument.model_validate(data)


def test_role_referencing_unknown_permission_rejected():
    data = _minimal_valid_policy()
    data["roles"][0]["permissions"].append("ghost_permission")
    with pytest.raises(ValidationError, match="unknown permission"):
        GateBPolicyDocument.model_validate(data)


def test_rule_may_reference_a_permission_its_own_role_lacks():
    """Deliberately legal (fixture `GB-R002`): a rule can require a
    permission the referenced role does not carry, paired with
    `allowed=False` -- policy still governs the pairing even though it can
    never actually be satisfied, this is not the same failure as
    referencing a permission id the document never declared at all."""
    data = _minimal_valid_policy()
    data["rules"][0]["required_permission"] = "read_structured_supplier"
    data["rules"][0]["allowed"] = False
    document = GateBPolicyDocument.model_validate(data)
    assert document.rules[0].allowed is False


# ---------------------------------------------------------------------------
# Required validation: unknown data classification rejected
# ---------------------------------------------------------------------------


def test_rule_with_ungoverned_data_classification_string_rejected():
    """Not one of the four closed `DataClassification` values at all --
    rejected by the enum before any document-level cross-check."""
    data = _minimal_valid_policy()
    data["rules"][0]["allowed_data_classes"] = ["top_secret"]
    with pytest.raises(ValidationError):
        GateBPolicyDocument.model_validate(data)


def test_rule_with_data_classification_not_enabled_by_policy_rejected():
    """A syntactically valid `DataClassification` this policy version does
    not itself declare enabled in `data_classifications` -- rejected by
    the `GateBPolicyDocument` cross-check, not the enum."""
    data = _minimal_valid_policy()
    data["data_classifications"] = ["public"]  # "internal" no longer enabled
    with pytest.raises(ValidationError, match="not enabled for this policy version"):
        GateBPolicyDocument.model_validate(data)


def test_restricted_classification_not_declared_enabled_by_pack_fixture_rules():
    """No rule in the real fixture is allowed to disclose `restricted`
    data -- an authorized-for-`internal` caller is not automatically
    authorized for `restricted` (Day 10 Task 7)."""
    document = GateBPolicyDocument.model_validate(_load_pack_fixture_dict())
    assert all(DataClassification.RESTRICTED not in r.allowed_data_classes for r in document.rules)


# ---------------------------------------------------------------------------
# Required validation: unknown PII category rejected
# ---------------------------------------------------------------------------


def test_rule_with_ungoverned_pii_category_string_rejected():
    data = _minimal_valid_policy()
    data["rules"][0]["allowed_pii_categories"] = ["ssn"]
    with pytest.raises(ValidationError):
        GateBPolicyDocument.model_validate(data)


def test_rule_with_pii_category_not_enabled_by_policy_rejected():
    data = _minimal_valid_policy()
    data["pii_categories"] = ["none"]  # "contact" no longer enabled
    with pytest.raises(ValidationError, match="not enabled for this policy version"):
        GateBPolicyDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Required validation: unknown disclosure profile rejected
# ---------------------------------------------------------------------------


def test_rule_referencing_unknown_disclosure_profile_rejected():
    data = _minimal_valid_policy()
    data["rules"][0]["disclosure_profile"] = "ghost_profile"
    with pytest.raises(ValidationError, match="unknown disclosure_profile"):
        GateBPolicyDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Required validation: invalid status rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [("status",), ("roles", 0, "status"), ("rules", 0, "status")])
def test_invalid_status_enum_rejected(path):
    data = _minimal_valid_policy()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = "not_a_real_status"
    with pytest.raises(ValidationError):
        GateBPolicyDocument.model_validate(data)


def test_invalid_disclosure_action_rejected():
    data = _minimal_valid_policy()
    data["disclosure_profiles"][0]["field_actions"]["supplier_name"] = "maybe"
    with pytest.raises(ValidationError):
        GateBPolicyDocument.model_validate(data)


def test_invalid_tenant_scope_rejected():
    data = _minimal_valid_policy()
    data["roles"][0]["tenant_scope"] = "all_tenants"
    with pytest.raises(ValidationError):
        GateBPolicyDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Extensions: duplicate role / permission / disclosure profile IDs rejected
# ---------------------------------------------------------------------------


def test_duplicate_role_id_rejected():
    data = _minimal_valid_policy()
    data["roles"].append(copy.deepcopy(data["roles"][0]))
    with pytest.raises(ValidationError, match="duplicate role_id"):
        GateBPolicyDocument.model_validate(data)


def test_duplicate_permission_id_rejected():
    data = _minimal_valid_policy()
    data["permissions"].append(data["permissions"][0])
    with pytest.raises(ValidationError, match="duplicate permission"):
        GateBPolicyDocument.model_validate(data)


def test_duplicate_disclosure_profile_id_rejected():
    data = _minimal_valid_policy()
    data["disclosure_profiles"].append(copy.deepcopy(data["disclosure_profiles"][0]))
    with pytest.raises(ValidationError, match="duplicate disclosure_profile"):
        GateBPolicyDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Unchecked dictionaries are not acceptable: extra="forbid" everywhere
# ---------------------------------------------------------------------------


def test_unknown_top_level_field_rejected():
    data = _minimal_valid_policy()
    data["not_a_governed_field"] = "should be rejected"
    with pytest.raises(ValidationError):
        GateBPolicyDocument.model_validate(data)


@pytest.mark.parametrize(
    "record_key, index",
    [("roles", 0), ("disclosure_profiles", 0), ("rules", 0)],
)
def test_unknown_field_on_a_record_rejected(record_key, index):
    data = _minimal_valid_policy()
    data[record_key][index]["not_a_governed_field"] = "should be rejected"
    with pytest.raises(ValidationError):
        GateBPolicyDocument.model_validate(data)


# ===========================================================================
# Task 2 -- PolicyRegistry
# ===========================================================================

# ---------------------------------------------------------------------------
# Load committed policy / validate it
# ---------------------------------------------------------------------------


def test_default_policy_path_points_at_committed_file():
    assert DEFAULT_POLICY_PATH == Path("policy/gate_b_policy.v1.json")


def test_load_reads_the_real_committed_policy():
    registry = PolicyRegistry.load()

    assert registry.policy_version == "1.0"
    assert len(registry.roles) == 3
    assert len(registry.rules) == 5
    assert set(registry.permissions) == {"read_policy", "read_structured_supplier", "read_confidential"}
    assert len(registry.disclosure_profiles) == 3


def test_load_accepts_an_explicit_path(tmp_path):
    explicit_path = tmp_path / "gate_b_policy.v1.json"
    explicit_path.write_text(COMMITTED_POLICY_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    registry = PolicyRegistry.load(explicit_path)

    assert registry.policy_version == "1.0"


def test_load_missing_file_raises_policy_load_error(tmp_path):
    missing_path = tmp_path / "does_not_exist.json"
    with pytest.raises(PolicyLoadError, match="not found"):
        PolicyRegistry.load(missing_path)


def test_load_malformed_json_raises_policy_load_error(tmp_path):
    bad_path = tmp_path / "bad.json"
    bad_path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(PolicyLoadError, match="not valid JSON"):
        PolicyRegistry.load(bad_path)


def test_load_policy_failing_typed_validation_raises_policy_load_error(tmp_path):
    invalid_data = _minimal_valid_policy()
    del invalid_data["policy_version"]
    bad_path = tmp_path / "invalid.json"
    bad_path.write_text(json.dumps(invalid_data), encoding="utf-8")

    with pytest.raises(PolicyLoadError, match="failed validation"):
        PolicyRegistry.load(bad_path)


# ---------------------------------------------------------------------------
# Ontology cross-reference is mandatory by default
# ---------------------------------------------------------------------------


def test_load_defaults_to_the_real_committed_ontology_registry(tmp_path):
    """No `ontology_registry` passed -- `load()` resolves the real
    committed `ontology/registry.v1.json` itself and rejects a rule
    referencing an intent that registry does not govern, exactly as
    `policy_models.py`'s docstring documents."""
    data = _minimal_valid_policy()
    data["rules"][0]["intent_id"] = "INT-NOT-REGISTERED"
    bad_path = tmp_path / "unknown_intent.json"
    bad_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(PolicyLoadError, match="unknown ontology intent"):
        PolicyRegistry.load(bad_path)


def test_load_accepts_an_explicit_ontology_registry(tmp_path):
    """An explicitly supplied `OntologyRegistry` is honored instead of the
    default committed one -- a policy rule referencing an intent that
    registry *does* govern (even one absent from the real committed
    registry) is accepted."""
    throwaway_ontology = OntologyDocument.model_validate(
        {
            "ontology_version": "throwaway",
            "lanes": ["rag"],
            "domains": [{"domain_id": "d1", "name": "D1", "owner": "team", "status": "active"}],
            "concepts": [],
            "intents": [
                {
                    "intent_id": "INT-THROWAWAY",
                    "domain": "d1",
                    "description": "Throwaway intent for this test only.",
                    "allowed_lanes": ["rag"],
                    "clarification_required_when_ambiguous": False,
                    "status": "active",
                }
            ],
        }
    )
    data = _minimal_valid_policy()
    data["rules"][0]["intent_id"] = "INT-THROWAWAY"
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(data), encoding="utf-8")

    registry = PolicyRegistry.load(policy_path, ontology_registry=OntologyRegistry(throwaway_ontology))

    assert registry.rules[0].intent_id == "INT-THROWAWAY"


def test_pack_fixture_rules_all_reference_real_committed_ontology_intents():
    """The real committed policy loads cleanly against the real committed
    ontology by default -- proves the two Day 9/Day 10 committed resources
    actually agree end to end, not just that the mechanism exists."""
    registry = PolicyRegistry.load()
    assert registry.policy_version == "1.0"


# ---------------------------------------------------------------------------
# Ambiguous rule combinations rejected (registry-level integrity)
# ---------------------------------------------------------------------------


def test_load_rejects_two_rules_governing_the_same_role_intent_lane(tmp_path):
    data = _minimal_valid_policy()
    duplicate_rule = copy.deepcopy(data["rules"][0])
    duplicate_rule["rule_id"] = "GB-R999"  # distinct rule_id -- passes Task 1's own duplicate check
    data["rules"].append(duplicate_rule)
    bad_path = tmp_path / "ambiguous.json"
    bad_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(PolicyLoadError, match="ambiguous policy"):
        PolicyRegistry.load(bad_path, ontology_registry=_load_committed_ontology_registry())


def test_committed_policy_has_no_ambiguous_rule_combinations():
    """The real committed policy itself must load without tripping this
    check -- `PolicyRegistry.load()` succeeding at all already proves it,
    this just names the property explicitly."""
    registry = PolicyRegistry.load()
    assert len(registry.rules) == len({(r.role, r.intent_id, r.lane) for r in registry.rules})


# ---------------------------------------------------------------------------
# Expose active policy version / read-only lookups
# ---------------------------------------------------------------------------


@pytest.fixture
def registry() -> PolicyRegistry:
    ontology_registry = _load_committed_ontology_registry()
    known_intent_ids = {intent.intent_id for intent in ontology_registry.intents}
    document = GateBPolicyDocument.model_validate(
        _load_committed_policy_dict(), context={"known_intent_ids": known_intent_ids}
    )
    return PolicyRegistry(document)


def test_policy_version_property(registry):
    assert registry.policy_version == "1.0"


def test_collection_accessors_return_tuples_not_lists(registry):
    assert isinstance(registry.roles, tuple)
    assert isinstance(registry.permissions, tuple)
    assert isinstance(registry.data_classifications, tuple)
    assert isinstance(registry.pii_categories, tuple)
    assert isinstance(registry.disclosure_profiles, tuple)
    assert isinstance(registry.rules, tuple)


def test_collection_accessor_returns_a_fresh_immutable_tuple_each_call(registry):
    """A caller gets a `tuple` (no `.append`/`.remove`/item assignment at
    all) and a *fresh* one on every access -- not a cached reference to a
    mutable list living inside the registry that a caller could reach
    through and mutate for everyone else (Day 10 working rule: "Runtime
    callers, model output and session memory may not mutate policy")."""
    first_read = registry.rules
    assert not hasattr(first_read, "append")
    with pytest.raises(TypeError):
        first_read[0] = first_read[0]  # tuples reject item assignment

    second_read = registry.rules
    assert first_read == second_read
    assert first_read is not second_read


def test_registry_has_no_public_mutator_methods(registry):
    forbidden_prefixes = ("add_", "set_", "update_", "delete_", "remove_", "mutate_")
    public_methods = [name for name in dir(registry) if not name.startswith("_")]
    offending = [name for name in public_methods if name.startswith(forbidden_prefixes)]
    assert offending == []


# ---------------------------------------------------------------------------
# Resolve roles/disclosure profiles/rules by id
# ---------------------------------------------------------------------------


def test_get_role_resolves_known_role(registry):
    role = registry.get_role("sourcing_analyst")
    assert isinstance(role, Role)
    assert role.tenant_scope is TenantScopeKind.OWN_TENANT


def test_get_role_unknown_id_raises_policy_lookup_error(registry):
    with pytest.raises(PolicyLookupError) as exc_info:
        registry.get_role("does_not_exist")
    assert exc_info.value.kind == "role"
    assert exc_info.value.identifier == "does_not_exist"


def test_get_disclosure_profile_resolves_known_profile(registry):
    profile = registry.get_disclosure_profile("compliance_view")
    assert isinstance(profile, DisclosureProfile)
    assert profile.field_actions["contact_email"] is DisclosureAction.ALLOW


def test_get_disclosure_profile_unknown_id_raises_policy_lookup_error(registry):
    with pytest.raises(PolicyLookupError) as exc_info:
        registry.get_disclosure_profile("does_not_exist")
    assert exc_info.value.kind == "disclosure_profile"


def test_get_rule_resolves_known_rule(registry):
    rule = registry.get_rule("GB-R004")
    assert isinstance(rule, PermissionRule)
    assert rule.allowed is True


def test_get_rule_unknown_id_raises_policy_lookup_error(registry):
    with pytest.raises(PolicyLookupError) as exc_info:
        registry.get_rule("GB-R999")
    assert exc_info.value.kind == "rule"


def test_has_role_disclosure_profile_rule_membership_checks(registry):
    assert registry.has_role("supplier_reader") is True
    assert registry.has_role("nope") is False
    assert registry.has_disclosure_profile("structured_reader") is True
    assert registry.has_disclosure_profile("nope") is False
    assert registry.has_rule("GB-R005") is True
    assert registry.has_rule("nope") is False


# ---------------------------------------------------------------------------
# find_rule: deterministic role/intent/lane matching
# ---------------------------------------------------------------------------


def test_find_rule_matches_every_governed_combination_in_the_real_policy(registry):
    """Every rule in the real committed policy must be findable by its own
    (role, intent_id, lane) -- the exact primitive Gate-B (Task 3) will
    match a trusted request against."""
    for rule in registry.rules:
        found = registry.find_rule(rule.role, rule.intent_id, rule.lane)
        assert found is not None
        assert found.rule_id == rule.rule_id


def test_find_rule_returns_none_for_lane_mismatch(registry):
    """`permission_cases.json` PERM-006: `sourcing_analyst` is governed for
    `INT-POLICY-QUESTION` only on lane `rag` (`GB-R003`) -- no rule governs
    the same role/intent pair on `mode_b`."""
    found = registry.find_rule("sourcing_analyst", "INT-POLICY-QUESTION", LaneId.MODE_B)
    assert found is None


def test_find_rule_returns_none_for_unknown_role(registry):
    assert registry.find_rule("unknown_role", "INT-POLICY-QUESTION", LaneId.RAG) is None


def test_find_rule_returns_none_for_unknown_intent(registry):
    assert registry.find_rule("supplier_reader", "INT-NOT-REGISTERED", LaneId.RAG) is None


def test_find_rule_result_for_denied_rule_still_surfaces_allowed_false(registry):
    """`permission_cases.json` PERM-002: a matched rule can itself be
    `allowed=false` (`GB-R002`) -- `find_rule` still returns it (a matched
    rule, whatever its `allowed` value, is not the same as "no rule
    matched"); it is Gate-B's (Task 3) job to read `.allowed`, not the
    registry's job to hide the rule."""
    found = registry.find_rule("supplier_reader", "INT-STRUCTURED-LOOKUP", LaneId.MODE_B)
    assert found is not None
    assert found.rule_id == "GB-R002"
    assert found.allowed is False


# ===========================================================================
# Task 7 -- is_data_classification_permitted()
# ===========================================================================


def test_is_data_classification_permitted_true_for_a_member():
    allowed = [DataClassification.PUBLIC, DataClassification.INTERNAL]
    assert is_data_classification_permitted(DataClassification.INTERNAL, allowed) is True


def test_is_data_classification_permitted_false_for_a_non_member():
    """`internal` being permitted never implies `restricted` is too --
    `DataClassification` carries no implied hierarchy (see the enum's own
    docstring)."""
    allowed = [DataClassification.PUBLIC, DataClassification.INTERNAL]
    assert is_data_classification_permitted(DataClassification.RESTRICTED, allowed) is False


def test_is_data_classification_permitted_false_for_an_empty_allowed_set():
    assert is_data_classification_permitted(DataClassification.PUBLIC, []) is False


@pytest.mark.parametrize("data_class", list(DataClassification))
def test_is_data_classification_permitted_true_when_allowed_set_is_every_classification(data_class):
    assert is_data_classification_permitted(data_class, list(DataClassification)) is True


def test_is_data_classification_permitted_is_the_only_classification_check_gate_b_uses():
    """Task 7: "Do not hardcode behavior in multiple unrelated files."
    `gate_b.py`'s own requested-classification check calls this function
    rather than re-implementing an inline membership test -- proven by
    inspecting its source, not by re-deriving the same behavior twice and
    hoping they stay in sync."""
    import inspect

    from aico.control import gate_b as gate_b_module

    source = inspect.getsource(gate_b_module.GateB.authorize)
    assert "is_data_classification_permitted(" in source
    assert "requested.data_class not in rule.allowed_data_classes" not in source


# ===========================================================================
# Task 8 -- is_pii_category_permitted() / resolve_disclosure_action()
# ===========================================================================


@pytest.fixture
def registry_for_disclosure() -> PolicyRegistry:
    return PolicyRegistry.load()


# ---------------------------------------------------------------------------
# resolve_disclosure_action() -- pii_disclosure_cases.json, fixture-driven
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", PII_DISCLOSURE_CASES, ids=[c["id"] for c in PII_DISCLOSURE_CASES])
def test_pii_disclosure_case_matches_fixture_expectation(registry_for_disclosure, case):
    profile = registry_for_disclosure.get_disclosure_profile(case["profile"])
    action = resolve_disclosure_action(profile, case["field"])
    assert action.value == case["expected_action"]


def test_allowed_non_pii_field_may_pass(registry_for_disclosure):
    """Task 8: "allowed non-PII field may pass" -- `supplier_name` carries
    `pii_category: none` in the fixture's own `field_metadata` and every
    profile's `field_actions` allows it."""
    profile = registry_for_disclosure.get_disclosure_profile("policy_reader")
    assert resolve_disclosure_action(profile, "supplier_name") is DisclosureAction.ALLOW


def test_allowed_pii_category_follows_policy(registry_for_disclosure):
    """Task 8: "allowed PII category follows policy" -- `contact_email`
    (PII category `contact`) resolves differently across profiles purely
    from the committed policy's own declared `field_actions`, never a
    hardcoded per-category rule: `redact` under `policy_reader`, `allow`
    under `compliance_view`."""
    policy_reader = registry_for_disclosure.get_disclosure_profile("policy_reader")
    compliance_view = registry_for_disclosure.get_disclosure_profile("compliance_view")
    assert resolve_disclosure_action(policy_reader, "contact_email") is DisclosureAction.REDACT
    assert resolve_disclosure_action(compliance_view, "contact_email") is DisclosureAction.ALLOW


def test_redactable_pii_action_is_deterministic_across_repeated_calls(registry_for_disclosure):
    """Task 8: "Redaction must be deterministic." Repeated resolution of
    the identical (profile, field) always returns the identical action --
    the actual masked-*value* transformation is Task 9's `redaction.py`;
    this proves the *action* `resolve_disclosure_action` commits to is
    itself stable, which that later masking step depends on."""
    profile = registry_for_disclosure.get_disclosure_profile("structured_reader")
    results = [resolve_disclosure_action(profile, "tax_identifier") for _ in range(5)]
    assert all(action is DisclosureAction.REDACT for action in results)


def test_disallowed_pii_denies_by_default_for_an_undeclared_field(registry_for_disclosure):
    """Task 8: "disallowed PII causes the configured deny/redact
    behavior." A field the profile never declares at all is not silently
    allowed -- fail closed to `DENY`, the same "unknown -> deny" rule
    every other Gate-B boundary already applies."""
    profile = registry_for_disclosure.get_disclosure_profile("policy_reader")
    assert resolve_disclosure_action(profile, "totally_undeclared_field") is DisclosureAction.DENY


def test_restricted_sensitive_field_cannot_be_exposed_by_asking_for_it(registry_for_disclosure):
    """Task 8: "restricted sensitive field cannot be exposed because the
    model asked for it." `resolve_disclosure_action`'s signature has no
    parameter for a model's request/suggestion at all -- passing any
    arbitrary, "model-invented" field name still resolves through the
    identical fail-closed default, never an override."""
    profile = registry_for_disclosure.get_disclosure_profile("compliance_view")
    for model_requested_field in ("bank_account", "ssn", "secret_notes", "anything_a_model_might_ask_for"):
        action = resolve_disclosure_action(profile, model_requested_field)
        assert action in (DisclosureAction.DENY, DisclosureAction.REDACT)
        assert action is not DisclosureAction.ALLOW


def test_resolve_disclosure_action_signature_has_no_model_or_override_parameter():
    """Structural proof of "the policy, not the model, decides the
    action": the function accepts exactly a `DisclosureProfile` and a
    `field_name` string -- nowhere for a model's suggested action, a
    caller's requested override, or session memory to be threaded
    through."""
    import inspect

    params = list(inspect.signature(resolve_disclosure_action).parameters)
    assert params == ["profile", "field_name"]


def test_committed_disclosure_profiles_never_default_to_allow_for_declared_sensitive_fields(registry_for_disclosure):
    """Sanity check on the real committed policy data itself (not just the
    function): every disclosure profile that mentions a known-sensitive
    field (`tax_identifier` / `bank_account` / `personal_notes`) declares
    `redact` or `deny` for it, never `allow` -- proving the fixture data
    agrees with the deny-by-default philosophy, not just the code."""
    sensitive_fields = {"tax_identifier", "bank_account", "personal_notes"}
    for profile in registry_for_disclosure.disclosure_profiles:
        for field_name in sensitive_fields & profile.field_actions.keys():
            assert profile.field_actions[field_name] is not DisclosureAction.ALLOW


# ---------------------------------------------------------------------------
# is_pii_category_permitted()
# ---------------------------------------------------------------------------


def test_is_pii_category_permitted_true_for_a_member():
    allowed = [PiiCategory.NONE, PiiCategory.CONTACT]
    assert is_pii_category_permitted(PiiCategory.CONTACT, allowed) is True


def test_is_pii_category_permitted_false_for_a_non_member():
    """A caller authorized for `contact` is not automatically authorized
    for `sensitive_personal` -- no implied ordering between PII
    categories, same as `DataClassification` (Task 7)."""
    allowed = [PiiCategory.NONE, PiiCategory.CONTACT]
    assert is_pii_category_permitted(PiiCategory.SENSITIVE_PERSONAL, allowed) is False


def test_is_pii_category_permitted_false_for_an_empty_allowed_set():
    assert is_pii_category_permitted(PiiCategory.NONE, []) is False


@pytest.mark.parametrize("pii_category", list(PiiCategory))
def test_is_pii_category_permitted_true_when_allowed_set_is_every_category(pii_category):
    assert is_pii_category_permitted(pii_category, list(PiiCategory)) is True
