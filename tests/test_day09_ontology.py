"""
Day 9 Task 1/2 -- the typed Mode-A ontology model
(`src/aico/control/ontology.py`) and the ontology registry that loads it
(`src/aico/control/ontology_registry.py`).

Task 1 section proves the acceptance-relevant behaviors of
`OntologyDocument` / `Domain` / `Concept` / `Intent` / `LaneId` directly
against Pydantic:

  - the committed `ontology/registry.v1.json` (Day 9's real Mode-A
    registry, copied verbatim from `data/day09_pack/fixtures/
    ontology_registry_v1.json`) loads into nested typed objects, not
    dicts;
  - every "Required validation" bullet from
    `data/day09_pack/ontology_requirements.md` is rejected when
    violated: missing version, duplicate concept/intent ids, an
    unknown relationship target, an unknown/ungoverned lane
    reference, an invalid status enum;
  - the two extensions this module documents beyond that minimum list
    (duplicate domain id, an intent referencing an undeclared domain)
    are rejected too.

Task 2 section proves `OntologyRegistry` end to end: loading the real
committed file (success and every documented failure mode), that its
collection/version accessors are read-only, and that `get_domain` /
`get_concept` / `get_intent` / `resolve_concepts` correctly resolve or
reject against the real registry's ids.

Gate-A classification (Task 3/4) and lane selection (Task 5) are not
implemented yet and are out of scope here -- this file only proves the
typed model and registry boundary itself.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aico.control.errors import OntologyLoadError, OntologyLookupError
from aico.control.ontology import (
    Concept,
    Domain,
    Intent,
    LaneId,
    LifecycleStatus,
    OntologyDocument,
)
from aico.control.ontology_registry import DEFAULT_REGISTRY_PATH, OntologyRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_REGISTRY_PATH = REPO_ROOT / "ontology" / "registry.v1.json"
PACK_FIXTURE_PATH = REPO_ROOT / "data" / "day09_pack" / "fixtures" / "ontology_registry_v1.json"


def _load_committed_registry_dict() -> dict:
    return json.loads(COMMITTED_REGISTRY_PATH.read_text(encoding="utf-8"))


def _minimal_valid_document() -> dict:
    """A small, hand-built valid document for isolated negative-path
    mutation tests, distinct from the full committed registry so each
    test only ever changes the one thing it is proving is rejected."""
    return {
        "ontology_version": "1.0",
        "lanes": ["rag", "mode_b", "clarify", "block", "safe_fast_path"],
        "domains": [
            {
                "domain_id": "supplier_governance",
                "name": "Supplier Governance",
                "owner": "AICO Engineering Team",
                "status": "active",
            }
        ],
        "concepts": [
            {
                "concept_id": "CON-SUPPLIER",
                "name": "supplier",
                "description": "A synthetic supplier/vendor entity.",
                "synonyms": ["vendor"],
                "relationships": [],
                "owner": "AICO Engineering Team",
                "status": "active",
            },
            {
                "concept_id": "CON-PAYMENT-TERMS",
                "name": "payment terms",
                "description": "Documented payment-term policy.",
                "synonyms": ["payment window"],
                "relationships": ["CON-SUPPLIER"],
                "owner": "AICO Engineering Team",
                "status": "active",
            },
        ],
        "intents": [
            {
                "intent_id": "INT-POLICY-QUESTION",
                "domain": "supplier_governance",
                "description": "Ask a factual question answered from supplier policy documents.",
                "phrases": ["what are the payment terms"],
                "allowed_lanes": ["rag"],
                "clarification_required_when_ambiguous": True,
                "status": "active",
            }
        ],
    }


# ---------------------------------------------------------------------------
# Registry loads into typed objects
# ---------------------------------------------------------------------------


def test_committed_registry_matches_pack_fixture_verbatim():
    """`ontology/registry.v1.json` must be the same governed document the
    pack shipped -- Task 1 does not get to quietly edit fixture data to
    make the model happy (`day09_pack/README.md`: "Do not edit fixed
    fixtures to improve results")."""
    assert _load_committed_registry_dict() == json.loads(PACK_FIXTURE_PATH.read_text(encoding="utf-8"))


def test_registry_loads_into_typed_objects():
    document = OntologyDocument.model_validate(_load_committed_registry_dict())

    assert isinstance(document, OntologyDocument)
    assert document.ontology_version == "1.0"

    assert len(document.domains) == 1
    assert all(isinstance(d, Domain) for d in document.domains)
    assert document.domains[0].domain_id == "supplier_governance"
    assert document.domains[0].status is LifecycleStatus.ACTIVE

    assert len(document.concepts) == 4
    assert all(isinstance(c, Concept) for c in document.concepts)
    supplier = next(c for c in document.concepts if c.concept_id == "CON-SUPPLIER")
    assert supplier.synonyms == ["vendor"]
    payment_terms = next(c for c in document.concepts if c.concept_id == "CON-PAYMENT-TERMS")
    assert payment_terms.relationships == ["CON-SUPPLIER"]

    assert len(document.intents) == 3
    assert all(isinstance(i, Intent) for i in document.intents)
    policy_question = next(i for i in document.intents if i.intent_id == "INT-POLICY-QUESTION")
    assert policy_question.allowed_lanes == [LaneId.RAG]
    assert policy_question.clarification_required_when_ambiguous is True

    assert document.lanes == list(LaneId)


def test_minimal_valid_document_is_accepted():
    """Sanity check on the isolated fixture the negative-path tests below
    mutate -- if this ever stops validating, every test that copies it is
    testing something other than what it claims."""
    document = OntologyDocument.model_validate(_minimal_valid_document())
    assert document.ontology_version == "1.0"


# ---------------------------------------------------------------------------
# Required validation: ontology version required
# ---------------------------------------------------------------------------


def test_missing_ontology_version_rejected():
    data = _minimal_valid_document()
    del data["ontology_version"]
    with pytest.raises(ValidationError):
        OntologyDocument.model_validate(data)


def test_empty_ontology_version_rejected():
    data = _minimal_valid_document()
    data["ontology_version"] = ""
    with pytest.raises(ValidationError):
        OntologyDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Required validation: duplicate concept IDs rejected
# ---------------------------------------------------------------------------


def test_duplicate_concept_id_rejected():
    data = _minimal_valid_document()
    duplicate = copy.deepcopy(data["concepts"][0])
    data["concepts"].append(duplicate)
    with pytest.raises(ValidationError, match="duplicate concept_id"):
        OntologyDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Required validation: duplicate intent IDs rejected
# ---------------------------------------------------------------------------


def test_duplicate_intent_id_rejected():
    data = _minimal_valid_document()
    duplicate = copy.deepcopy(data["intents"][0])
    data["intents"].append(duplicate)
    with pytest.raises(ValidationError, match="duplicate intent_id"):
        OntologyDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Extension: duplicate domain IDs rejected (same reasoning as concept/intent)
# ---------------------------------------------------------------------------


def test_duplicate_domain_id_rejected():
    data = _minimal_valid_document()
    duplicate = copy.deepcopy(data["domains"][0])
    data["domains"].append(duplicate)
    with pytest.raises(ValidationError, match="duplicate domain_id"):
        OntologyDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Required validation: unknown relationship targets rejected
# ---------------------------------------------------------------------------


def test_unknown_relationship_target_rejected():
    data = _minimal_valid_document()
    data["concepts"][1]["relationships"] = ["CON-DOES-NOT-EXIST"]
    with pytest.raises(ValidationError, match="unknown relationship target"):
        OntologyDocument.model_validate(data)


def test_concept_self_relationship_rejected():
    data = _minimal_valid_document()
    data["concepts"][0]["relationships"] = ["CON-SUPPLIER"]
    with pytest.raises(ValidationError, match="cannot relate to itself"):
        OntologyDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Required validation: unknown lane references rejected
# ---------------------------------------------------------------------------


def test_intent_with_ungoverned_lane_string_rejected():
    """A lane id that is not one of the five governed `LaneId` values at
    all -- rejected by the enum before any registry-level cross-check."""
    data = _minimal_valid_document()
    data["intents"][0]["allowed_lanes"] = ["made_up_lane"]
    with pytest.raises(ValidationError):
        OntologyDocument.model_validate(data)


def test_intent_with_lane_not_enabled_by_registry_rejected():
    """A syntactically valid `LaneId` that this ontology *version* does
    not itself declare enabled in `lanes` -- rejected by the
    `OntologyDocument` cross-check, not the enum."""
    data = _minimal_valid_document()
    data["lanes"] = ["mode_b", "clarify", "block", "safe_fast_path"]  # "rag" not enabled
    with pytest.raises(ValidationError, match="not enabled for this ontology version"):
        OntologyDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Required validation: invalid enum/status rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("record_key, index", [("domains", 0), ("concepts", 0), ("intents", 0)])
def test_invalid_status_enum_rejected(record_key, index):
    data = _minimal_valid_document()
    data[record_key][index]["status"] = "not_a_real_status"
    with pytest.raises(ValidationError):
        OntologyDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Extension: an intent must reference a domain that actually exists
# ---------------------------------------------------------------------------


def test_intent_with_unknown_domain_rejected():
    data = _minimal_valid_document()
    data["intents"][0]["domain"] = "unknown_domain"
    with pytest.raises(ValidationError, match="unknown domain"):
        OntologyDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Unchecked dictionaries are not acceptable: extra="forbid" everywhere
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("record_key, index", [("domains", 0), ("concepts", 0), ("intents", 0)])
def test_unknown_field_on_a_record_rejected(record_key, index):
    data = _minimal_valid_document()
    data[record_key][index]["not_a_governed_field"] = "should be rejected"
    with pytest.raises(ValidationError):
        OntologyDocument.model_validate(data)


def test_unknown_top_level_field_rejected():
    data = _minimal_valid_document()
    data["not_a_governed_field"] = "should be rejected"
    with pytest.raises(ValidationError):
        OntologyDocument.model_validate(data)


# ===========================================================================
# Task 2 -- OntologyRegistry
# ===========================================================================

# ---------------------------------------------------------------------------
# Load committed registry / validate it
# ---------------------------------------------------------------------------


def test_default_registry_path_points_at_committed_file():
    assert DEFAULT_REGISTRY_PATH == Path("ontology/registry.v1.json")


def test_load_reads_the_real_committed_registry():
    registry = OntologyRegistry.load()

    assert registry.ontology_version == "1.0"
    assert len(registry.domains) == 1
    assert len(registry.concepts) == 4
    assert len(registry.intents) == 3
    assert set(registry.lanes) == set(LaneId)


def test_load_accepts_an_explicit_path(tmp_path):
    explicit_path = tmp_path / "registry.v1.json"
    explicit_path.write_text(COMMITTED_REGISTRY_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    registry = OntologyRegistry.load(explicit_path)

    assert registry.ontology_version == "1.0"


def test_load_missing_file_raises_ontology_load_error(tmp_path):
    missing_path = tmp_path / "does_not_exist.json"
    with pytest.raises(OntologyLoadError, match="not found"):
        OntologyRegistry.load(missing_path)


def test_load_malformed_json_raises_ontology_load_error(tmp_path):
    bad_path = tmp_path / "bad.json"
    bad_path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(OntologyLoadError, match="not valid JSON"):
        OntologyRegistry.load(bad_path)


def test_load_registry_failing_typed_validation_raises_ontology_load_error(tmp_path):
    invalid_data = _minimal_valid_document()
    del invalid_data["ontology_version"]
    bad_path = tmp_path / "invalid.json"
    bad_path.write_text(json.dumps(invalid_data), encoding="utf-8")

    with pytest.raises(OntologyLoadError, match="failed validation"):
        OntologyRegistry.load(bad_path)


# ---------------------------------------------------------------------------
# Expose active ontology version / read-only lookups
# ---------------------------------------------------------------------------


@pytest.fixture
def registry() -> OntologyRegistry:
    return OntologyRegistry(OntologyDocument.model_validate(_load_committed_registry_dict()))


def test_ontology_version_property(registry):
    assert registry.ontology_version == "1.0"


def test_collection_accessors_return_tuples_not_lists(registry):
    assert isinstance(registry.domains, tuple)
    assert isinstance(registry.concepts, tuple)
    assert isinstance(registry.intents, tuple)
    assert isinstance(registry.lanes, tuple)


def test_collection_accessor_returns_a_fresh_immutable_tuple_each_call(registry):
    """A caller gets a `tuple` (no `.append`/`.remove`/item assignment at
    all) and a *fresh* one on every access -- not a cached reference to a
    mutable list living inside the registry that a caller could reach
    through and mutate for everyone else (Day 9 working rule: "Runtime
    request/model output must not mutate the registry")."""
    first_read = registry.concepts
    assert not hasattr(first_read, "append")
    with pytest.raises(TypeError):
        first_read[0] = first_read[0]  # tuples reject item assignment

    second_read = registry.concepts
    assert first_read == second_read
    assert first_read is not second_read


def test_registry_has_no_public_mutator_methods(registry):
    forbidden_prefixes = ("add_", "set_", "update_", "delete_", "remove_", "mutate_")
    public_methods = [name for name in dir(registry) if not name.startswith("_")]
    offending = [name for name in public_methods if name.startswith(forbidden_prefixes)]
    assert offending == []


# ---------------------------------------------------------------------------
# Resolve concepts/intents (and domains) by id
# ---------------------------------------------------------------------------


def test_get_domain_resolves_known_domain(registry):
    domain = registry.get_domain("supplier_governance")
    assert isinstance(domain, Domain)
    assert domain.name == "Supplier Governance"


def test_get_domain_unknown_id_raises_ontology_lookup_error(registry):
    with pytest.raises(OntologyLookupError) as exc_info:
        registry.get_domain("does_not_exist")
    assert exc_info.value.kind == "domain"
    assert exc_info.value.identifier == "does_not_exist"


def test_get_concept_resolves_known_concept(registry):
    concept = registry.get_concept("CON-SUPPLIER")
    assert isinstance(concept, Concept)
    assert concept.name == "supplier"


def test_get_concept_unknown_id_raises_ontology_lookup_error(registry):
    with pytest.raises(OntologyLookupError) as exc_info:
        registry.get_concept("CON-DOES-NOT-EXIST")
    assert exc_info.value.kind == "concept"


def test_get_intent_resolves_known_intent(registry):
    intent = registry.get_intent("INT-POLICY-QUESTION")
    assert isinstance(intent, Intent)
    assert intent.allowed_lanes == [LaneId.RAG]


def test_get_intent_unknown_id_raises_ontology_lookup_error(registry):
    with pytest.raises(OntologyLookupError) as exc_info:
        registry.get_intent("INT-DOES-NOT-EXIST")
    assert exc_info.value.kind == "intent"


def test_has_domain_concept_intent_membership_checks(registry):
    assert registry.has_domain("supplier_governance") is True
    assert registry.has_domain("nope") is False
    assert registry.has_concept("CON-SUPPLIER") is True
    assert registry.has_concept("nope") is False
    assert registry.has_intent("INT-HELP") is True
    assert registry.has_intent("nope") is False


def test_resolve_concepts_bulk_resolves_in_order(registry):
    resolved = registry.resolve_concepts(["CON-CONTRACT", "CON-SUPPLIER"])
    assert [c.concept_id for c in resolved] == ["CON-CONTRACT", "CON-SUPPLIER"]


def test_resolve_concepts_empty_input_returns_empty_tuple(registry):
    assert registry.resolve_concepts([]) == ()


def test_resolve_concepts_rejects_unknown_candidate_id(registry):
    """Exactly the check Task 4's model-assisted interpreter needs: a
    proposed candidate id that is not actually governed is rejected, not
    silently dropped or substituted."""
    with pytest.raises(OntologyLookupError) as exc_info:
        registry.resolve_concepts(["CON-SUPPLIER", "CON-INVENTED-BY-MODEL"])
    assert exc_info.value.identifier == "CON-INVENTED-BY-MODEL"


def test_relationship_targets_resolve_to_real_concepts(registry):
    """`Concept.relationships` is validated at parse time (Task 1) to only
    ever name existing concept_ids -- `resolve_concepts` turns that id
    list into the actual governed `Concept` objects for every concept in
    the real registry that declares any relationships."""
    for concept in registry.concepts:
        if not concept.relationships:
            continue
        resolved = registry.resolve_concepts(concept.relationships)
        assert [c.concept_id for c in resolved] == concept.relationships
