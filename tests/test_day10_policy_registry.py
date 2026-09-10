"""
Day 10 Task 1 -- the typed Gate-B policy model
(`src/aico/control/policy_models.py`).

Task 2's `policy_registry.py` (loading/read-only exposure of the committed
`policy/gate_b_policy.v1.json`) does not exist yet -- this file proves the
acceptance-relevant behaviors of `GateBPolicyDocument` / `Role` /
`PermissionRule` / `DisclosureProfile` directly against Pydantic, the same
way `test_day09_ontology.py`'s Task 1 section proves `OntologyDocument`
directly before `OntologyRegistry` (Task 2) is exercised. This file is
expected to grow a Task 2 section once `policy_registry.py` lands, mirroring
`test_day09_ontology.py`'s two-section shape.

Proves, against the real supplied `day10_pack/fixtures/gate_b_policy_v1.json`
(copied verbatim into `data/day10_pack/fixtures/`) and a minimal hand-built
document for isolated negative-path mutations:

  - the supplied fixture loads into nested typed objects, not dicts;
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

Gate-B itself (Task 3+) is not implemented yet and is out of scope here --
this file only proves the typed policy model boundary.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aico.control.ontology import LaneId, LifecycleStatus
from aico.control.policy_models import (
    DataClassification,
    DisclosureAction,
    DisclosureProfile,
    GateBPolicyDocument,
    PermissionRule,
    PiiCategory,
    Role,
    TenantScopeKind,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PACK_FIXTURE_PATH = REPO_ROOT / "data" / "day10_pack" / "fixtures" / "gate_b_policy_v1.json"

# The real Mode-A ontology intents this fixture's rules reference
# (`ontology/registry.v1.json`) -- passed as validation context exactly the
# way Task 2's `policy_registry.py` is documented to, proving the
# "unknown ontology intent rejected" mechanism without importing
# `OntologyRegistry` itself into this test module's fixture-building.
KNOWN_ONTOLOGY_INTENT_IDS = {"INT-POLICY-QUESTION", "INT-STRUCTURED-LOOKUP", "INT-HELP"}


def _load_pack_fixture_dict() -> dict:
    return json.loads(PACK_FIXTURE_PATH.read_text(encoding="utf-8"))


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
