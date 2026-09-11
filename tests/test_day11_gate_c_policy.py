"""
Day 11 Task 3 -- Gate-C policy (`src/aico/evidence/policy.py`).

Not one of Task 16's named test files (`test_day11_source_registry.py`
onward all cover later tasks; Task 3's own governed policy has no other
home) -- added because the assignment explicitly allows a documented
filename addition, the same way `test_day11_evidence_envelope.py` is
documented for Task 1. Mirrors `test_day10_policy_registry.py`'s
two-section structure: the first section proves the acceptance-relevant
behaviors of `GateCPolicyDocument`/`IntentEvidenceRequirement`/
`FreshnessPolicy` directly against Pydantic; the second proves
`GateCPolicyRegistry` end to end -- loading the real committed file
(success and every documented failure mode, including both cross-
reference directions and the registry's own ambiguous-rule-combination
check), that its collection/version accessors are read-only, that
`get_freshness_policy`/`get_rule` correctly resolve or reject against the
real policy's ids, and that `find_rule` deterministically matches (or
fails to match) one `(intent_id, request_kind)` combination.

Gate-C itself (Task 9) is not implemented yet and is out of scope here --
this file only proves the typed Gate-C policy model and loader/lookup
boundary.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aico.control.ontology import LifecycleStatus, OntologyDocument
from aico.control.ontology_registry import OntologyRegistry
from aico.evidence.errors import GateCPolicyLoadError, GateCPolicyLookupError
from aico.evidence.policy import (
    DEFAULT_GATE_C_POLICY_PATH,
    ConflictPolicy,
    FreshnessPolicy,
    GateCPolicyDocument,
    GateCPolicyRegistry,
    IntentEvidenceRequirement,
)
from aico.evidence.source_registry import SourceRegistry, SourceRegistryDocument

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_POLICY_PATH = REPO_ROOT / "policy" / "gate_c_policy.v1.json"
COMMITTED_ONTOLOGY_PATH = REPO_ROOT / "ontology" / "registry.v1.json"
COMMITTED_SOURCE_REGISTRY_PATH = REPO_ROOT / "evidence" / "source_registry.v1.json"
PACK_FIXTURE_PATH = REPO_ROOT / "data" / "day11_pack" / "fixtures" / "evidence_policy_v1.json"

# The real Mode-A ontology intents / governed source types the fixture's
# rules reference (`ontology/registry.v1.json`, `evidence/
# source_registry.v1.json`) -- passed as validation context exactly the
# way `GateCPolicyRegistry.load()` is documented to.
KNOWN_ONTOLOGY_INTENT_IDS = {"INT-POLICY-QUESTION", "INT-STRUCTURED-LOOKUP", "INT-HELP"}
KNOWN_SOURCE_TYPES = {"policy_document", "contract_record", "archived_policy", "reference_record"}


def _load_pack_fixture_dict() -> dict:
    return json.loads(PACK_FIXTURE_PATH.read_text(encoding="utf-8"))


def _load_committed_policy_dict() -> dict:
    return json.loads(COMMITTED_POLICY_PATH.read_text(encoding="utf-8"))


def _load_committed_ontology_registry() -> OntologyRegistry:
    return OntologyRegistry.load(COMMITTED_ONTOLOGY_PATH)


def _load_committed_source_registry() -> SourceRegistry:
    return SourceRegistry.load(COMMITTED_SOURCE_REGISTRY_PATH, ontology_registry=_load_committed_ontology_registry())


def _minimal_valid_policy() -> dict:
    """A small, hand-built valid Gate-C policy document for isolated
    negative-path mutation tests, distinct from the full pack fixture so
    each test only ever changes the one thing it is proving is rejected."""
    return {
        "policy_version": "1.0",
        "status": "active",
        "freshness_policies": [{"policy_id": "FRESH-A", "max_age_hours": 24}],
        "intent_requirements": [
            {
                "rule_id": "GC-R001",
                "intent_id": "INT-POLICY-QUESTION",
                "request_kind": "payment_terms_only",
                "required_facets": ["payment_terms"],
                "minimum_valid_items": 1,
                "allowed_source_types": ["policy_document"],
                "conflict_policy": "authority_then_reject_tie",
            }
        ],
    }


# ---------------------------------------------------------------------------
# Fixture loads into typed objects
# ---------------------------------------------------------------------------


def test_committed_policy_matches_pack_fixture_verbatim():
    """`policy/gate_c_policy.v1.json` must be the same governed document
    the pack shipped (just renamed to this directory's `<name>.vN.json`
    convention) -- Task 3 does not get to quietly edit fixture data to
    make the model happy (`day11_pack/README.md`: "Do not edit fixed
    fixtures to improve results")."""
    assert _load_committed_policy_dict() == _load_pack_fixture_dict()


def test_pack_fixture_loads_into_typed_objects():
    document = GateCPolicyDocument.model_validate(_load_pack_fixture_dict())

    assert isinstance(document, GateCPolicyDocument)
    assert document.policy_version == "1.0"
    assert document.status is LifecycleStatus.ACTIVE

    assert len(document.freshness_policies) == 2
    assert all(isinstance(p, FreshnessPolicy) for p in document.freshness_policies)
    policy_30d = next(p for p in document.freshness_policies if p.policy_id == "FRESH-POLICY-30D")
    assert policy_30d.max_age_hours == 720

    assert len(document.intent_requirements) == 3
    assert all(isinstance(r, IntentEvidenceRequirement) for r in document.intent_requirements)
    gc_r002 = next(r for r in document.intent_requirements if r.rule_id == "GC-R002")
    assert gc_r002.intent_id == "INT-POLICY-QUESTION"
    assert gc_r002.request_kind == "payment_and_invoice"
    assert set(gc_r002.required_facets) == {"supplier_identity", "payment_terms", "invoice_window"}
    assert gc_r002.minimum_valid_items == 1
    assert set(gc_r002.allowed_source_types) == {"policy_document", "contract_record", "reference_record"}
    assert gc_r002.conflict_policy is ConflictPolicy.AUTHORITY_THEN_REJECT_TIE


def test_minimal_valid_policy_is_accepted():
    """Sanity check on the isolated fixture the negative-path tests below
    mutate -- if this ever stops validating, every test that copies it is
    testing something other than what it claims."""
    document = GateCPolicyDocument.model_validate(_minimal_valid_policy())
    assert document.policy_version == "1.0"


# ---------------------------------------------------------------------------
# Required validation: policy version required
# ---------------------------------------------------------------------------


def test_missing_policy_version_rejected():
    data = _minimal_valid_policy()
    del data["policy_version"]
    with pytest.raises(ValidationError):
        GateCPolicyDocument.model_validate(data)


def test_empty_policy_version_rejected():
    data = _minimal_valid_policy()
    data["policy_version"] = ""
    with pytest.raises(ValidationError):
        GateCPolicyDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Required validation: duplicate policy IDs/rules rejected
# ---------------------------------------------------------------------------


def test_duplicate_rule_id_rejected():
    data = _minimal_valid_policy()
    data["intent_requirements"].append(copy.deepcopy(data["intent_requirements"][0]))
    with pytest.raises(ValidationError, match="duplicate rule_id"):
        GateCPolicyDocument.model_validate(data)


def test_duplicate_freshness_policy_id_rejected():
    data = _minimal_valid_policy()
    data["freshness_policies"].append(copy.deepcopy(data["freshness_policies"][0]))
    with pytest.raises(ValidationError, match="duplicate freshness policy_id"):
        GateCPolicyDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Required validation: unknown intent rejected (via context)
# ---------------------------------------------------------------------------


def test_rule_referencing_known_ontology_intent_accepted_with_context():
    data = _minimal_valid_policy()
    document = GateCPolicyDocument.model_validate(data, context={"known_intent_ids": KNOWN_ONTOLOGY_INTENT_IDS})
    assert document.intent_requirements[0].intent_id == "INT-POLICY-QUESTION"


def test_rule_referencing_unknown_ontology_intent_rejected_with_context():
    data = _minimal_valid_policy()
    data["intent_requirements"][0]["intent_id"] = "INT-NOT-REGISTERED"
    with pytest.raises(ValidationError, match="unknown ontology intent"):
        GateCPolicyDocument.model_validate(data, context={"known_intent_ids": KNOWN_ONTOLOGY_INTENT_IDS})


def test_rule_referencing_unknown_ontology_intent_accepted_without_context():
    """Without a supplied `known_intent_ids` context, the ontology
    cross-check is skipped -- documented, deliberate behavior (see
    `policy.py`'s "Cross-references" docstring section), not silently
    permissive by accident. `GateCPolicyRegistry.load()` is what makes
    this check mandatory in practice for the one document that is ever
    actually served."""
    data = _minimal_valid_policy()
    data["intent_requirements"][0]["intent_id"] = "INT-NOT-REGISTERED"
    document = GateCPolicyDocument.model_validate(data)
    assert document.intent_requirements[0].intent_id == "INT-NOT-REGISTERED"


def test_pack_fixture_rules_all_reference_real_ontology_intents():
    document = GateCPolicyDocument.model_validate(
        _load_pack_fixture_dict(), context={"known_intent_ids": KNOWN_ONTOLOGY_INTENT_IDS}
    )
    assert {r.intent_id for r in document.intent_requirements} <= KNOWN_ONTOLOGY_INTENT_IDS


# ---------------------------------------------------------------------------
# Required validation: unknown source reference rejected (via context)
# ---------------------------------------------------------------------------


def test_rule_referencing_known_source_type_accepted_with_context():
    data = _minimal_valid_policy()
    document = GateCPolicyDocument.model_validate(data, context={"known_source_types": KNOWN_SOURCE_TYPES})
    assert document.intent_requirements[0].allowed_source_types == ("policy_document",)


def test_rule_referencing_unknown_source_type_rejected_with_context():
    data = _minimal_valid_policy()
    data["intent_requirements"][0]["allowed_source_types"] = ["not_a_real_source_type"]
    with pytest.raises(ValidationError, match="unknown source type"):
        GateCPolicyDocument.model_validate(data, context={"known_source_types": KNOWN_SOURCE_TYPES})


def test_rule_referencing_unknown_source_type_accepted_without_context():
    data = _minimal_valid_policy()
    data["intent_requirements"][0]["allowed_source_types"] = ["not_a_real_source_type"]
    document = GateCPolicyDocument.model_validate(data)
    assert document.intent_requirements[0].allowed_source_types == ("not_a_real_source_type",)


def test_pack_fixture_rules_all_reference_real_source_types():
    document = GateCPolicyDocument.model_validate(
        _load_pack_fixture_dict(), context={"known_source_types": KNOWN_SOURCE_TYPES}
    )
    for rule in document.intent_requirements:
        assert set(rule.allowed_source_types) <= KNOWN_SOURCE_TYPES


# ---------------------------------------------------------------------------
# Required validation: invalid status rejected
# ---------------------------------------------------------------------------


def test_invalid_top_level_status_rejected():
    data = _minimal_valid_policy()
    data["status"] = "not_a_real_status"
    with pytest.raises(ValidationError):
        GateCPolicyDocument.model_validate(data)


def test_invalid_conflict_policy_rejected():
    data = _minimal_valid_policy()
    data["intent_requirements"][0]["conflict_policy"] = "pick_whichever_looks_better"
    with pytest.raises(ValidationError):
        GateCPolicyDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Required-but-not-explicitly-named field constraints
# ---------------------------------------------------------------------------


def test_zero_max_age_hours_rejected():
    data = _minimal_valid_policy()
    data["freshness_policies"][0]["max_age_hours"] = 0
    with pytest.raises(ValidationError):
        GateCPolicyDocument.model_validate(data)


def test_zero_minimum_valid_items_rejected():
    data = _minimal_valid_policy()
    data["intent_requirements"][0]["minimum_valid_items"] = 0
    with pytest.raises(ValidationError):
        GateCPolicyDocument.model_validate(data)


def test_empty_required_facets_rejected():
    data = _minimal_valid_policy()
    data["intent_requirements"][0]["required_facets"] = []
    with pytest.raises(ValidationError):
        GateCPolicyDocument.model_validate(data)


def test_empty_allowed_source_types_rejected():
    data = _minimal_valid_policy()
    data["intent_requirements"][0]["allowed_source_types"] = []
    with pytest.raises(ValidationError):
        GateCPolicyDocument.model_validate(data)


def test_blank_required_facet_entry_rejected():
    data = _minimal_valid_policy()
    data["intent_requirements"][0]["required_facets"] = ["  "]
    with pytest.raises(ValidationError):
        GateCPolicyDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Unchecked dictionaries are not acceptable: extra="forbid" everywhere
# ---------------------------------------------------------------------------


def test_unknown_top_level_field_rejected():
    data = _minimal_valid_policy()
    data["not_a_governed_field"] = "should be rejected"
    with pytest.raises(ValidationError):
        GateCPolicyDocument.model_validate(data)


@pytest.mark.parametrize("record_key, index", [("freshness_policies", 0), ("intent_requirements", 0)])
def test_unknown_field_on_a_record_rejected(record_key, index):
    data = _minimal_valid_policy()
    data[record_key][index]["not_a_governed_field"] = "should be rejected"
    with pytest.raises(ValidationError):
        GateCPolicyDocument.model_validate(data)


# ===========================================================================
# Task 3 -- GateCPolicyRegistry
# ===========================================================================

# ---------------------------------------------------------------------------
# Load committed policy / validate it
# ---------------------------------------------------------------------------


def test_default_gate_c_policy_path_points_at_committed_file():
    assert DEFAULT_GATE_C_POLICY_PATH == Path("policy/gate_c_policy.v1.json")


def test_load_reads_the_real_committed_policy():
    registry = GateCPolicyRegistry.load()

    assert registry.policy_version == "1.0"
    assert registry.status is LifecycleStatus.ACTIVE
    assert len(registry.freshness_policies) == 2
    assert len(registry.intent_requirements) == 3


def test_load_accepts_an_explicit_path(tmp_path):
    explicit_path = tmp_path / "gate_c_policy.v1.json"
    explicit_path.write_text(COMMITTED_POLICY_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    registry = GateCPolicyRegistry.load(explicit_path)

    assert registry.policy_version == "1.0"


def test_load_missing_file_raises_gate_c_policy_load_error(tmp_path):
    missing_path = tmp_path / "does_not_exist.json"
    with pytest.raises(GateCPolicyLoadError, match="not found"):
        GateCPolicyRegistry.load(missing_path)


def test_load_malformed_json_raises_gate_c_policy_load_error(tmp_path):
    bad_path = tmp_path / "bad.json"
    bad_path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(GateCPolicyLoadError, match="not valid JSON"):
        GateCPolicyRegistry.load(bad_path)


def test_load_policy_failing_typed_validation_raises_gate_c_policy_load_error(tmp_path):
    invalid_data = _minimal_valid_policy()
    del invalid_data["policy_version"]
    bad_path = tmp_path / "invalid.json"
    bad_path.write_text(json.dumps(invalid_data), encoding="utf-8")

    with pytest.raises(GateCPolicyLoadError, match="failed validation"):
        GateCPolicyRegistry.load(bad_path)


# ---------------------------------------------------------------------------
# Cross-references are mandatory by default
# ---------------------------------------------------------------------------


def test_load_defaults_to_the_real_committed_ontology_registry(tmp_path):
    data = _minimal_valid_policy()
    data["intent_requirements"][0]["intent_id"] = "INT-NOT-REGISTERED"
    bad_path = tmp_path / "unknown_intent.json"
    bad_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(GateCPolicyLoadError, match="unknown ontology intent"):
        GateCPolicyRegistry.load(bad_path)


def test_load_defaults_to_the_real_committed_source_registry(tmp_path):
    data = _minimal_valid_policy()
    data["intent_requirements"][0]["allowed_source_types"] = ["not_a_real_source_type"]
    bad_path = tmp_path / "unknown_source_type.json"
    bad_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(GateCPolicyLoadError, match="unknown source type"):
        GateCPolicyRegistry.load(bad_path)


def test_load_accepts_explicit_registries(tmp_path):
    """Explicitly supplied `OntologyRegistry`/`SourceRegistry` instances
    are honored instead of the default committed ones -- a rule
    referencing an intent/source type only *those* registries govern (even
    one absent from the real committed registries) is accepted."""
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
    throwaway_source_registry_document = {
        "registry_version": "throwaway",
        "sources": [
            {
                "source_id": "SRC-THROWAWAY",
                "source_type": "throwaway_type",
                "authority_level": 1,
                "owner": "team",
                "status": "active",
                "allowed_intents": ["INT-THROWAWAY"],
                "supported_facets": ["payment_terms"],
                "freshness_policy_id": "FRESH-A",
            }
        ],
    }
    ontology_registry = OntologyRegistry(throwaway_ontology)
    source_registry = SourceRegistry(
        SourceRegistryDocument.model_validate(
            throwaway_source_registry_document, context={"known_intent_ids": {"INT-THROWAWAY"}}
        )
    )

    data = _minimal_valid_policy()
    data["intent_requirements"][0]["intent_id"] = "INT-THROWAWAY"
    data["intent_requirements"][0]["allowed_source_types"] = ["throwaway_type"]
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(data), encoding="utf-8")

    registry = GateCPolicyRegistry.load(
        policy_path, ontology_registry=ontology_registry, source_registry=source_registry
    )

    assert registry.intent_requirements[0].intent_id == "INT-THROWAWAY"
    assert registry.intent_requirements[0].allowed_source_types == ("throwaway_type",)


def test_pack_fixture_reference_the_real_committed_ontology_and_source_registry():
    """The real committed policy loads cleanly against the real committed
    ontology and source registry by default -- proves all three committed
    Day 9/Day 11 resources actually agree end to end, not just that the
    mechanism exists."""
    registry = GateCPolicyRegistry.load()
    assert registry.policy_version == "1.0"


# ---------------------------------------------------------------------------
# Required validation: unknown freshness policy rejected (reverse direction)
# ---------------------------------------------------------------------------


def test_load_rejects_a_source_referencing_an_undeclared_freshness_policy(tmp_path):
    """The reverse-direction half of the cross-reference: a governed
    source's (Task 2) `freshness_policy_id` must name a freshness policy
    *this* policy document declares -- see `policy.py`'s "Cross-
    references" docstring section for why this direction cannot use the
    `known_*` context mechanism."""
    ontology_registry = _load_committed_ontology_registry()
    source_registry_document = {
        "registry_version": "1.0",
        "sources": [
            {
                "source_id": "SRC-ORPHAN-FRESHNESS",
                "source_type": "policy_document",
                "authority_level": 50,
                "owner": "team",
                "status": "active",
                "allowed_intents": ["INT-POLICY-QUESTION"],
                "supported_facets": ["payment_terms"],
                "freshness_policy_id": "FRESH-DOES-NOT-EXIST",
            }
        ],
    }
    source_registry = SourceRegistry(
        SourceRegistryDocument.model_validate(
            source_registry_document,
            context={"known_intent_ids": {intent.intent_id for intent in ontology_registry.intents}},
        )
    )

    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(_minimal_valid_policy()), encoding="utf-8")

    with pytest.raises(GateCPolicyLoadError, match="unknown freshness policy"):
        GateCPolicyRegistry.load(policy_path, ontology_registry=ontology_registry, source_registry=source_registry)


def test_committed_source_registry_freshness_references_all_resolve():
    """Every governed source's `freshness_policy_id` in the real committed
    source registry must resolve against the real committed Gate-C policy
    -- `GateCPolicyRegistry.load()` succeeding at all already proves it,
    this just names the property explicitly."""
    registry = GateCPolicyRegistry.load()
    known_freshness_ids = {p.policy_id for p in registry.freshness_policies}
    source_registry = _load_committed_source_registry()
    for source in source_registry.sources:
        assert source.freshness_policy_id in known_freshness_ids


# ---------------------------------------------------------------------------
# Ambiguous rule combinations rejected (registry-level integrity)
# ---------------------------------------------------------------------------


def test_load_rejects_two_rules_governing_the_same_intent_request_kind(tmp_path):
    data = _minimal_valid_policy()
    duplicate_rule = copy.deepcopy(data["intent_requirements"][0])
    duplicate_rule["rule_id"] = "GC-R999"  # distinct rule_id -- passes the document's own duplicate check
    data["intent_requirements"].append(duplicate_rule)
    bad_path = tmp_path / "ambiguous.json"
    bad_path.write_text(json.dumps(data), encoding="utf-8")

    ontology_registry = _load_committed_ontology_registry()
    # A throwaway source registry consistent with `_minimal_valid_policy()`'s
    # own `FRESH-A` freshness policy -- the real committed source registry
    # references `FRESH-POLICY-30D`/`FRESH-CONTRACT-7D` instead, which would
    # trip the (unrelated) freshness cross-reference check before this
    # test's own ambiguous-combination check is even reached.
    source_registry = SourceRegistry(
        SourceRegistryDocument.model_validate(
            {
                "registry_version": "1.0",
                "sources": [
                    {
                        "source_id": "SRC-A",
                        "source_type": "policy_document",
                        "authority_level": 90,
                        "owner": "team",
                        "status": "active",
                        "allowed_intents": ["INT-POLICY-QUESTION"],
                        "supported_facets": ["payment_terms"],
                        "freshness_policy_id": "FRESH-A",
                    }
                ],
            },
            context={"known_intent_ids": {intent.intent_id for intent in ontology_registry.intents}},
        )
    )

    with pytest.raises(GateCPolicyLoadError, match="ambiguous policy"):
        GateCPolicyRegistry.load(bad_path, ontology_registry=ontology_registry, source_registry=source_registry)


def test_committed_policy_has_no_ambiguous_rule_combinations():
    registry = GateCPolicyRegistry.load()
    assert len(registry.intent_requirements) == len(
        {(r.intent_id, r.request_kind) for r in registry.intent_requirements}
    )


# ---------------------------------------------------------------------------
# Expose active policy version / status / read-only lookups
# ---------------------------------------------------------------------------


@pytest.fixture
def registry() -> GateCPolicyRegistry:
    return GateCPolicyRegistry.load()


def test_policy_version_property(registry):
    assert registry.policy_version == "1.0"


def test_status_property(registry):
    assert registry.status is LifecycleStatus.ACTIVE


def test_collection_accessors_return_tuples(registry):
    assert isinstance(registry.freshness_policies, tuple)
    assert isinstance(registry.intent_requirements, tuple)


def test_collection_accessor_returns_an_immutable_tuple(registry):
    first_read = registry.intent_requirements
    assert not hasattr(first_read, "append")
    with pytest.raises(TypeError):
        first_read[0] = first_read[0]  # tuples reject item assignment
    assert first_read == registry.intent_requirements


def test_registry_has_no_public_mutator_methods(registry):
    forbidden_prefixes = ("add_", "set_", "update_", "delete_", "remove_", "mutate_")
    public_methods = [name for name in dir(registry) if not name.startswith("_")]
    offending = [name for name in public_methods if name.startswith(forbidden_prefixes)]
    assert offending == []


# ---------------------------------------------------------------------------
# Resolve freshness policies/rules by id
# ---------------------------------------------------------------------------


def test_get_freshness_policy_resolves_known_policy(registry):
    policy = registry.get_freshness_policy("FRESH-CONTRACT-7D")
    assert isinstance(policy, FreshnessPolicy)
    assert policy.max_age_hours == 168


def test_get_freshness_policy_unknown_id_raises_lookup_error(registry):
    with pytest.raises(GateCPolicyLookupError) as exc_info:
        registry.get_freshness_policy("does_not_exist")
    assert exc_info.value.kind == "freshness_policy"
    assert exc_info.value.identifier == "does_not_exist"


def test_get_rule_resolves_known_rule(registry):
    rule = registry.get_rule("GC-R003")
    assert isinstance(rule, IntentEvidenceRequirement)
    assert rule.intent_id == "INT-STRUCTURED-LOOKUP"


def test_get_rule_unknown_id_raises_lookup_error(registry):
    with pytest.raises(GateCPolicyLookupError) as exc_info:
        registry.get_rule("GC-R999")
    assert exc_info.value.kind == "rule"


def test_has_freshness_policy_and_rule_membership_checks(registry):
    assert registry.has_freshness_policy("FRESH-POLICY-30D") is True
    assert registry.has_freshness_policy("nope") is False
    assert registry.has_rule("GC-R001") is True
    assert registry.has_rule("nope") is False


# ---------------------------------------------------------------------------
# find_rule: deterministic (intent_id, request_kind) matching
# ---------------------------------------------------------------------------


def test_find_rule_matches_every_governed_combination_in_the_real_policy(registry):
    for rule in registry.intent_requirements:
        found = registry.find_rule(rule.intent_id, rule.request_kind)
        assert found is not None
        assert found.rule_id == rule.rule_id


def test_find_rule_returns_none_for_request_kind_mismatch(registry):
    """`INT-POLICY-QUESTION` is governed for `payment_terms_only` and
    `payment_and_invoice` only -- no rule governs a third, unrelated
    request kind for that same intent."""
    found = registry.find_rule("INT-POLICY-QUESTION", "totally_unrelated_kind")
    assert found is None


def test_find_rule_returns_none_for_unknown_intent(registry):
    assert registry.find_rule("INT-NOT-REGISTERED", "payment_terms_only") is None


def test_find_rule_result_surfaces_conflict_policy_and_thresholds(registry):
    found = registry.find_rule("INT-STRUCTURED-LOOKUP", "contract_status")
    assert found is not None
    assert found.rule_id == "GC-R003"
    assert found.conflict_policy is ConflictPolicy.AUTHORITY_THEN_REJECT_TIE
    assert found.minimum_valid_items == 1
