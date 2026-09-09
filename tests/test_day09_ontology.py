"""
Day 9 Task 1 -- typed Mode-A ontology model (`src/aico/control/ontology.py`).

Proves the acceptance-relevant behaviors of `OntologyDocument` /
`Domain` / `Concept` / `Intent` / `LaneId` directly against Pydantic:

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

Gate-A classification (Task 3/4) and lane selection (Task 5) are not
implemented yet and are out of scope here -- this file only proves the
typed model boundary itself.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aico.control.ontology import (
    Concept,
    Domain,
    Intent,
    LaneId,
    LifecycleStatus,
    OntologyDocument,
)

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
