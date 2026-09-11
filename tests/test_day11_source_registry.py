"""
Day 11 Task 2 -- the governed source registry
(`src/aico/evidence/source_registry.py`).

Mirrors `test_day10_policy_registry.py`'s two-section structure: the first
section proves the acceptance-relevant behaviors of
`SourceRegistryDocument`/`SourceRecord` directly against Pydantic (the same
way `test_day09_ontology.py` proves `OntologyDocument` before its own
registry, Task 2, is exercised); the second proves `SourceRegistry` end to
end -- loading the real committed file (success and every documented
failure mode), that its collection/version accessors are read-only, that
`get_source` correctly resolves or rejects against the real registry's
ids, and that `is_source_active`/`supports_intent`/`supports_facet` give
Task 2's three named "Required behavior" bullets ("unknown/disabled source
cannot pass Gate-C", "source intent compatibility is enforced", "source
supported facets are enforced") as directly testable, fixture-driven
outcomes.

Gate-C itself (Task 9) is not implemented yet and is out of scope here --
this file only proves the typed source-registry model and loader/lookup
boundary.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aico.control.ontology import OntologyDocument
from aico.control.ontology_registry import OntologyRegistry
from aico.evidence.errors import SourceRegistryLoadError, SourceRegistryLookupError
from aico.evidence.source_registry import (
    DEFAULT_SOURCE_REGISTRY_PATH,
    SourceRecord,
    SourceRegistry,
    SourceRegistryDocument,
    SourceStatus,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_REGISTRY_PATH = REPO_ROOT / "evidence" / "source_registry.v1.json"
COMMITTED_ONTOLOGY_PATH = REPO_ROOT / "ontology" / "registry.v1.json"
PACK_FIXTURE_PATH = REPO_ROOT / "data" / "day11_pack" / "fixtures" / "source_registry_v1.json"

# The real Mode-A ontology intents the fixture's sources reference
# (`ontology/registry.v1.json`) -- passed as validation context exactly the
# way `SourceRegistry.load()` is documented to, proving the "unknown
# ontology intent rejected" mechanism without importing `OntologyRegistry`
# itself into every test's fixture-building.
KNOWN_ONTOLOGY_INTENT_IDS = {"INT-POLICY-QUESTION", "INT-STRUCTURED-LOOKUP", "INT-HELP"}


def _load_pack_fixture_dict() -> dict:
    return json.loads(PACK_FIXTURE_PATH.read_text(encoding="utf-8"))


def _load_committed_registry_dict() -> dict:
    return json.loads(COMMITTED_REGISTRY_PATH.read_text(encoding="utf-8"))


def _load_committed_ontology_registry() -> OntologyRegistry:
    return OntologyRegistry.load(COMMITTED_ONTOLOGY_PATH)


def _minimal_valid_registry() -> dict:
    """A small, hand-built valid source registry document for isolated
    negative-path mutation tests, distinct from the full pack fixture so
    each test only ever changes the one thing it is proving is rejected."""
    return {
        "registry_version": "1.0",
        "sources": [
            {
                "source_id": "SRC-A",
                "source_type": "policy_document",
                "authority_level": 90,
                "owner": "AICO Synthetic Procurement",
                "status": "active",
                "allowed_intents": ["INT-POLICY-QUESTION"],
                "supported_facets": ["payment_terms"],
                "freshness_policy_id": "FRESH-POLICY-30D",
            }
        ],
    }


# ---------------------------------------------------------------------------
# Fixture loads into typed objects
# ---------------------------------------------------------------------------


def test_committed_registry_matches_pack_fixture_verbatim():
    """`evidence/source_registry.v1.json` must be the same governed document
    the pack shipped -- Task 2 does not get to quietly edit fixture data to
    make the model happy (`day11_pack/README.md`: "Do not edit fixed
    fixtures to improve results")."""
    assert _load_committed_registry_dict() == _load_pack_fixture_dict()


def test_pack_fixture_loads_into_typed_objects():
    document = SourceRegistryDocument.model_validate(_load_pack_fixture_dict())

    assert isinstance(document, SourceRegistryDocument)
    assert document.registry_version == "1.0"
    assert len(document.sources) == 4
    assert all(isinstance(s, SourceRecord) for s in document.sources)

    policy_a = next(s for s in document.sources if s.source_id == "SRC-POLICY-A")
    assert policy_a.status is SourceStatus.ACTIVE
    assert policy_a.authority_level == 90
    assert policy_a.allowed_intents == ("INT-POLICY-QUESTION",)
    assert set(policy_a.supported_facets) == {"supplier_identity", "payment_terms", "invoice_window"}

    archive_a = next(s for s in document.sources if s.source_id == "SRC-ARCHIVE-A")
    assert archive_a.status is SourceStatus.DISABLED

    contract_a = next(s for s in document.sources if s.source_id == "SRC-CONTRACT-A")
    assert contract_a.authority_level == 100
    assert set(contract_a.allowed_intents) == {"INT-POLICY-QUESTION", "INT-STRUCTURED-LOOKUP"}


def test_minimal_valid_registry_is_accepted():
    """Sanity check on the isolated fixture the negative-path tests below
    mutate -- if this ever stops validating, every test that copies it is
    testing something other than what it claims."""
    document = SourceRegistryDocument.model_validate(_minimal_valid_registry())
    assert document.registry_version == "1.0"


# ---------------------------------------------------------------------------
# Required validation: registry version required
# ---------------------------------------------------------------------------


def test_missing_registry_version_rejected():
    data = _minimal_valid_registry()
    del data["registry_version"]
    with pytest.raises(ValidationError):
        SourceRegistryDocument.model_validate(data)


def test_empty_registry_version_rejected():
    data = _minimal_valid_registry()
    data["registry_version"] = ""
    with pytest.raises(ValidationError):
        SourceRegistryDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Required validation: duplicate source ID rejected
# ---------------------------------------------------------------------------


def test_duplicate_source_id_rejected():
    data = _minimal_valid_registry()
    data["sources"].append(copy.deepcopy(data["sources"][0]))
    with pytest.raises(ValidationError, match="duplicate source_id"):
        SourceRegistryDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Required validation: invalid status rejected
# ---------------------------------------------------------------------------


def test_invalid_source_status_rejected():
    data = _minimal_valid_registry()
    data["sources"][0]["status"] = "not_a_real_status"
    with pytest.raises(ValidationError):
        SourceRegistryDocument.model_validate(data)


def test_deprecated_status_not_governed_for_sources():
    """Unlike `ontology.py`'s `LifecycleStatus`, `SourceStatus` has no
    `deprecated`/`retired` value -- see `source_registry.py`'s "Status"
    docstring section for why a Gate-C source is deliberately either
    usable or not, nothing in between."""
    data = _minimal_valid_registry()
    data["sources"][0]["status"] = "deprecated"
    with pytest.raises(ValidationError):
        SourceRegistryDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Required validation: missing/blank required fields rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field", ["source_id", "source_type", "owner", "status", "freshness_policy_id"]
)
def test_missing_required_source_field_rejected(field):
    data = _minimal_valid_registry()
    del data["sources"][0][field]
    with pytest.raises(ValidationError):
        SourceRegistryDocument.model_validate(data)


def test_blank_source_id_rejected():
    data = _minimal_valid_registry()
    data["sources"][0]["source_id"] = ""
    with pytest.raises(ValidationError):
        SourceRegistryDocument.model_validate(data)


def test_negative_authority_level_rejected():
    data = _minimal_valid_registry()
    data["sources"][0]["authority_level"] = -1
    with pytest.raises(ValidationError):
        SourceRegistryDocument.model_validate(data)


def test_blank_allowed_intent_entry_rejected():
    data = _minimal_valid_registry()
    data["sources"][0]["allowed_intents"] = ["  "]
    with pytest.raises(ValidationError):
        SourceRegistryDocument.model_validate(data)


def test_blank_supported_facet_entry_rejected():
    data = _minimal_valid_registry()
    data["sources"][0]["supported_facets"] = [""]
    with pytest.raises(ValidationError):
        SourceRegistryDocument.model_validate(data)


# ---------------------------------------------------------------------------
# Required validation: unknown ontology intent rejected (via context)
# ---------------------------------------------------------------------------


def test_source_referencing_known_ontology_intent_accepted_with_context():
    data = _minimal_valid_registry()
    document = SourceRegistryDocument.model_validate(data, context={"known_intent_ids": KNOWN_ONTOLOGY_INTENT_IDS})
    assert document.sources[0].allowed_intents == ("INT-POLICY-QUESTION",)


def test_source_referencing_unknown_ontology_intent_rejected_with_context():
    data = _minimal_valid_registry()
    data["sources"][0]["allowed_intents"] = ["INT-NOT-REGISTERED"]
    with pytest.raises(ValidationError, match="unknown intent"):
        SourceRegistryDocument.model_validate(data, context={"known_intent_ids": KNOWN_ONTOLOGY_INTENT_IDS})


def test_source_referencing_unknown_ontology_intent_accepted_without_context():
    """Without a supplied `known_intent_ids` context, the ontology
    cross-check is skipped -- documented, deliberate behavior (see
    `source_registry.py`'s "Ontology cross-reference" docstring section),
    not silently permissive by accident. `SourceRegistry.load()` is what
    makes this check mandatory in practice for the one document that is
    ever actually served."""
    data = _minimal_valid_registry()
    data["sources"][0]["allowed_intents"] = ["INT-NOT-REGISTERED"]
    document = SourceRegistryDocument.model_validate(data)
    assert document.sources[0].allowed_intents == ("INT-NOT-REGISTERED",)


def test_pack_fixture_sources_all_reference_real_ontology_intents():
    """The real, committed fixture must pass the ontology cross-check
    against the real committed Mode-A registry's intent ids -- proves the
    two Day 9/Day 11 resources actually agree, not just that the mechanism
    exists in isolation."""
    document = SourceRegistryDocument.model_validate(
        _load_pack_fixture_dict(), context={"known_intent_ids": KNOWN_ONTOLOGY_INTENT_IDS}
    )
    for source in document.sources:
        assert set(source.allowed_intents) <= KNOWN_ONTOLOGY_INTENT_IDS


# ---------------------------------------------------------------------------
# Unchecked dictionaries are not acceptable: extra="forbid" everywhere
# ---------------------------------------------------------------------------


def test_unknown_top_level_field_rejected():
    data = _minimal_valid_registry()
    data["not_a_governed_field"] = "should be rejected"
    with pytest.raises(ValidationError):
        SourceRegistryDocument.model_validate(data)


def test_unknown_field_on_a_source_record_rejected():
    data = _minimal_valid_registry()
    data["sources"][0]["not_a_governed_field"] = "should be rejected"
    with pytest.raises(ValidationError):
        SourceRegistryDocument.model_validate(data)


# ===========================================================================
# Task 2 -- SourceRegistry
# ===========================================================================

# ---------------------------------------------------------------------------
# Load committed registry / validate it
# ---------------------------------------------------------------------------


def test_default_source_registry_path_points_at_committed_file():
    assert DEFAULT_SOURCE_REGISTRY_PATH == Path("evidence/source_registry.v1.json")


def test_load_reads_the_real_committed_registry():
    registry = SourceRegistry.load()

    assert registry.registry_version == "1.0"
    assert len(registry.sources) == 4
    assert {s.source_id for s in registry.sources} == {
        "SRC-POLICY-A",
        "SRC-CONTRACT-A",
        "SRC-ARCHIVE-A",
        "SRC-REFERENCE-A",
    }


def test_load_accepts_an_explicit_path(tmp_path):
    explicit_path = tmp_path / "source_registry.v1.json"
    explicit_path.write_text(COMMITTED_REGISTRY_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    registry = SourceRegistry.load(explicit_path)

    assert registry.registry_version == "1.0"


def test_load_missing_file_raises_source_registry_load_error(tmp_path):
    missing_path = tmp_path / "does_not_exist.json"
    with pytest.raises(SourceRegistryLoadError, match="not found"):
        SourceRegistry.load(missing_path)


def test_load_malformed_json_raises_source_registry_load_error(tmp_path):
    bad_path = tmp_path / "bad.json"
    bad_path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(SourceRegistryLoadError, match="not valid JSON"):
        SourceRegistry.load(bad_path)


def test_load_registry_failing_typed_validation_raises_source_registry_load_error(tmp_path):
    invalid_data = _minimal_valid_registry()
    del invalid_data["registry_version"]
    bad_path = tmp_path / "invalid.json"
    bad_path.write_text(json.dumps(invalid_data), encoding="utf-8")

    with pytest.raises(SourceRegistryLoadError, match="failed validation"):
        SourceRegistry.load(bad_path)


# ---------------------------------------------------------------------------
# Ontology cross-reference is mandatory by default
# ---------------------------------------------------------------------------


def test_load_defaults_to_the_real_committed_ontology_registry(tmp_path):
    """No `ontology_registry` passed -- `load()` resolves the real
    committed `ontology/registry.v1.json` itself and rejects a source
    referencing an intent that registry does not govern, exactly as
    `source_registry.py`'s docstring documents."""
    data = _minimal_valid_registry()
    data["sources"][0]["allowed_intents"] = ["INT-NOT-REGISTERED"]
    bad_path = tmp_path / "unknown_intent.json"
    bad_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(SourceRegistryLoadError, match="unknown intent"):
        SourceRegistry.load(bad_path)


def test_load_accepts_an_explicit_ontology_registry(tmp_path):
    """An explicitly supplied `OntologyRegistry` is honored instead of the
    default committed one -- a source referencing an intent that registry
    *does* govern (even one absent from the real committed registry) is
    accepted."""
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
    data = _minimal_valid_registry()
    data["sources"][0]["allowed_intents"] = ["INT-THROWAWAY"]
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(data), encoding="utf-8")

    registry = SourceRegistry.load(registry_path, ontology_registry=OntologyRegistry(throwaway_ontology))

    assert registry.get_source("SRC-A").allowed_intents == ("INT-THROWAWAY",)


def test_pack_fixture_sources_all_reference_real_committed_ontology_intents():
    """The real committed registry loads cleanly against the real committed
    ontology by default -- proves the two Day 9/Day 11 committed resources
    actually agree end to end, not just that the mechanism exists."""
    registry = SourceRegistry.load()
    assert registry.registry_version == "1.0"


# ---------------------------------------------------------------------------
# Expose active registry version / read-only lookups
# ---------------------------------------------------------------------------


@pytest.fixture
def registry() -> SourceRegistry:
    ontology_registry = _load_committed_ontology_registry()
    known_intent_ids = {intent.intent_id for intent in ontology_registry.intents}
    document = SourceRegistryDocument.model_validate(
        _load_committed_registry_dict(), context={"known_intent_ids": known_intent_ids}
    )
    return SourceRegistry(document)


def test_registry_version_property(registry):
    assert registry.registry_version == "1.0"


def test_collection_accessor_returns_tuple_not_list(registry):
    assert isinstance(registry.sources, tuple)


def test_collection_accessor_returns_an_immutable_tuple(registry):
    """A caller gets a `tuple` (no `.append`/`.remove`/item assignment at
    all) -- `SourceRecord`s are stored as `tuple[SourceRecord, ...]` on
    `SourceRegistryDocument` itself (Task 2's models, unlike
    `OntologyDocument`'s plain `list` fields, are immutable one layer
    lower), so every access is already read-only at the model level, not
    only via a defensive copy in this registry's own accessor (Task 2
    working rule: "registry is read-only at runtime")."""
    first_read = registry.sources
    assert not hasattr(first_read, "append")
    with pytest.raises(TypeError):
        first_read[0] = first_read[0]  # tuples reject item assignment

    second_read = registry.sources
    assert first_read == second_read


def test_registry_has_no_public_mutator_methods(registry):
    forbidden_prefixes = ("add_", "set_", "update_", "delete_", "remove_", "mutate_", "enable_", "disable_")
    public_methods = [name for name in dir(registry) if not name.startswith("_")]
    offending = [name for name in public_methods if name.startswith(forbidden_prefixes)]
    assert offending == []


# ---------------------------------------------------------------------------
# Resolve sources by id
# ---------------------------------------------------------------------------


def test_has_source_membership_check(registry):
    assert registry.has_source("SRC-POLICY-A") is True
    assert registry.has_source("SRC-UNKNOWN") is False


def test_get_source_resolves_known_source(registry):
    source = registry.get_source("SRC-CONTRACT-A")
    assert isinstance(source, SourceRecord)
    assert source.authority_level == 100


def test_get_source_unknown_id_raises_source_registry_lookup_error(registry):
    with pytest.raises(SourceRegistryLookupError) as exc_info:
        registry.get_source("SRC-UNKNOWN")
    assert exc_info.value.kind == "source"
    assert exc_info.value.identifier == "SRC-UNKNOWN"


# ---------------------------------------------------------------------------
# Task 2 "Required behavior": unknown/disabled source cannot pass Gate-C
# ---------------------------------------------------------------------------


def test_is_source_active_true_for_an_active_source(registry):
    assert registry.is_source_active("SRC-POLICY-A") is True


def test_is_source_active_false_for_a_disabled_source(registry):
    """SRC-ARCHIVE-A is `status: disabled` in the real committed registry --
    Task 2's "disabled source cannot pass Gate-C" bullet."""
    assert registry.is_source_active("SRC-ARCHIVE-A") is False


def test_is_source_active_false_for_an_unknown_source(registry):
    """Folds "unknown" into the same `False` as "disabled" -- Task 2's
    "unknown/disabled source cannot pass Gate-C" bullet, without requiring
    a caller to wrap this in a try/except just to find out."""
    assert registry.is_source_active("SRC-DOES-NOT-EXIST") is False


def test_source_record_is_active_property_matches_registry_method(registry):
    source = registry.get_source("SRC-ARCHIVE-A")
    assert source.is_active is False
    assert source.is_active == registry.is_source_active(source.source_id)


# ---------------------------------------------------------------------------
# Task 2 "Required behavior": source intent compatibility is enforced
# ---------------------------------------------------------------------------


def test_supports_intent_true_for_a_governed_combination(registry):
    assert registry.supports_intent("SRC-POLICY-A", "INT-POLICY-QUESTION") is True


def test_supports_intent_false_for_an_ungoverned_combination(registry):
    """SRC-POLICY-A's `allowed_intents` is only `INT-POLICY-QUESTION` in the
    real committed registry -- it may not back evidence for
    `INT-STRUCTURED-LOOKUP`."""
    assert registry.supports_intent("SRC-POLICY-A", "INT-STRUCTURED-LOOKUP") is False


def test_supports_intent_true_for_a_source_governed_for_multiple_intents(registry):
    assert registry.supports_intent("SRC-CONTRACT-A", "INT-POLICY-QUESTION") is True
    assert registry.supports_intent("SRC-CONTRACT-A", "INT-STRUCTURED-LOOKUP") is True


def test_supports_intent_unknown_source_raises_source_registry_lookup_error(registry):
    with pytest.raises(SourceRegistryLookupError):
        registry.supports_intent("SRC-DOES-NOT-EXIST", "INT-POLICY-QUESTION")


# ---------------------------------------------------------------------------
# Task 2 "Required behavior": source supported facets are enforced
# ---------------------------------------------------------------------------


def test_supports_facet_true_for_a_governed_facet(registry):
    assert registry.supports_facet("SRC-REFERENCE-A", "invoice_window") is True


def test_supports_facet_false_for_an_ungoverned_facet(registry):
    """SRC-REFERENCE-A's `supported_facets` is only `invoice_window` in the
    real committed registry -- it may not be credited for `payment_terms`
    (Task 7's completeness check depends on this)."""
    assert registry.supports_facet("SRC-REFERENCE-A", "payment_terms") is False


def test_supports_facet_unknown_source_raises_source_registry_lookup_error(registry):
    with pytest.raises(SourceRegistryLookupError):
        registry.supports_facet("SRC-DOES-NOT-EXIST", "payment_terms")


def test_source_record_supports_intent_and_facet_methods_match_registry(registry):
    source = registry.get_source("SRC-CONTRACT-A")
    assert source.supports_intent("INT-STRUCTURED-LOOKUP") is True
    assert source.supports_facet("contract_status") is True
    assert source.supports_facet("invoice_window") is False
