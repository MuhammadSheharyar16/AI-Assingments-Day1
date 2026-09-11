"""
Day 11 Task 1 -- the typed evidence envelope.

Proves `EvidenceItem`/`EvidencePackage` (`aico.evidence.models`) turn a
well-formed candidate-evidence payload into typed objects (not dicts), and
that `parse_evidence_item()`/`parse_evidence_package()` reject every
"Required validation" bullet Task 1 names (`day11_pack/
evidence_policy_requirements.md`): missing source id, missing source
version, missing provenance identifier, invalid timestamps, missing
content hash, invalid classification enum, and a malformed evidence
package -- always as one sanitized `EvidenceEnvelopeError`, never a raw
`pydantic.ValidationError` leaking through and never a value that silently
became a plain dict instead of a typed model (Table 16's "Evidence
envelope | Malformed evidence rejected" row).

Not listed among Task 16's named test files (`test_day11_source_registry.py`
onward) -- added because Task 1's own envelope has no other required test
file to live in, and the assignment explicitly allows this ("Exact names
may differ if documented" / structure rule: "keep it and document the
mapping in README.md"). Documented in `README.md`'s Day 11 section.
"""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from aico.control.ontology import LaneId
from aico.control.policy_models import DataClassification
from aico.evidence.errors import EvidenceEnvelopeError
from aico.evidence.models import EvidenceItem, EvidencePackage, parse_evidence_item, parse_evidence_package


def _valid_item_data() -> dict:
    return {
        "evidence_id": "E-001",
        "chunk_id": "CH-001",
        "source_id": "SRC-POLICY-A",
        "source_version": "3",
        "source_updated_at": "2026-09-05T09:00:00+05:00",
        "retrieved_at": "2026-09-11T11:59:00+05:00",
        "content_hash": "HASH-E001-V3",
        "tenant_id": "TENANT-A",
        "data_classification": "internal",
        "evidence_facets": ["supplier_identity", "payment_terms"],
        "content": "Synthetic Supplier Alpha uses net 30 payment terms.",
    }


def _valid_package_data() -> dict:
    return {
        "request_id": "REQ-001",
        "intent_id": "INT-POLICY-QUESTION",
        "lane": "rag",
        "as_of": "2026-09-11T12:00:00+05:00",
        "required_facets": ["supplier_identity", "payment_terms"],
        "items": [_valid_item_data()],
    }


# --- valid envelope: typed objects, not dicts -----------------------------


def test_valid_evidence_item_parses_to_typed_object():
    item = parse_evidence_item(_valid_item_data())
    assert isinstance(item, EvidenceItem)
    assert item.evidence_id == "E-001"
    assert item.data_classification == DataClassification.INTERNAL
    assert item.evidence_facets == ("supplier_identity", "payment_terms")


def test_valid_evidence_package_parses_to_typed_object():
    package = parse_evidence_package(_valid_package_data())
    assert isinstance(package, EvidencePackage)
    assert package.lane == LaneId.RAG
    assert len(package.items) == 1
    assert isinstance(package.items[0], EvidenceItem)


def test_direct_model_construction_also_validates():
    """`EvidenceItem`/`EvidencePackage` are self-validating on their own --
    a caller is never able to build one from unchecked data just by
    skipping `parse_evidence_item`/`parse_evidence_package`."""
    with pytest.raises(ValidationError):
        EvidenceItem.model_validate({**_valid_item_data(), "source_id": ""})


# --- Task 1 "Required validation": each reject case ------------------------


def test_missing_source_id_rejected():
    data = _valid_item_data()
    del data["source_id"]
    with pytest.raises(EvidenceEnvelopeError, match="source_id"):
        parse_evidence_item(data)


def test_blank_source_id_rejected():
    data = {**_valid_item_data(), "source_id": ""}
    with pytest.raises(EvidenceEnvelopeError, match="source_id"):
        parse_evidence_item(data)


def test_missing_source_version_rejected():
    data = _valid_item_data()
    del data["source_version"]
    with pytest.raises(EvidenceEnvelopeError, match="source_version"):
        parse_evidence_item(data)


def test_missing_provenance_identifier_rejected():
    """`chunk_id` is Task 1's stable provenance identifier -- missing or
    blank must fail the same way a missing source id does."""
    data = _valid_item_data()
    del data["chunk_id"]
    with pytest.raises(EvidenceEnvelopeError, match="chunk_id"):
        parse_evidence_item(data)


def test_blank_provenance_identifier_rejected():
    data = {**_valid_item_data(), "chunk_id": "   "}
    with pytest.raises(EvidenceEnvelopeError):
        parse_evidence_item(data)


@pytest.mark.parametrize("field", ["source_updated_at", "retrieved_at"])
def test_missing_timestamp_rejected(field):
    data = _valid_item_data()
    del data[field]
    with pytest.raises(EvidenceEnvelopeError, match=field):
        parse_evidence_item(data)


@pytest.mark.parametrize("field", ["source_updated_at", "retrieved_at"])
def test_malformed_timestamp_rejected(field):
    data = {**_valid_item_data(), field: "not-a-timestamp"}
    with pytest.raises(EvidenceEnvelopeError):
        parse_evidence_item(data)


@pytest.mark.parametrize("field", ["source_updated_at", "retrieved_at"])
def test_naive_timestamp_rejected(field):
    """A timestamp with no tzinfo is invalid -- freshness (Task 6) depends
    on unambiguous, timezone-aware timestamps throughout."""
    data = {**_valid_item_data(), field: "2026-09-05T09:00:00"}
    with pytest.raises(EvidenceEnvelopeError):
        parse_evidence_item(data)


def test_missing_content_hash_rejected():
    data = _valid_item_data()
    del data["content_hash"]
    with pytest.raises(EvidenceEnvelopeError, match="content_hash"):
        parse_evidence_item(data)


def test_blank_content_hash_rejected():
    data = {**_valid_item_data(), "content_hash": ""}
    with pytest.raises(EvidenceEnvelopeError, match="content_hash"):
        parse_evidence_item(data)


def test_invalid_classification_enum_rejected():
    data = {**_valid_item_data(), "data_classification": "top_secret"}
    with pytest.raises(EvidenceEnvelopeError, match="data_classification"):
        parse_evidence_item(data)


def test_malformed_evidence_package_unknown_field_rejected():
    data = {**_valid_package_data(), "unexpected_field": "nope"}
    with pytest.raises(EvidenceEnvelopeError):
        parse_evidence_package(data)


def test_malformed_evidence_package_missing_request_id_rejected():
    data = _valid_package_data()
    del data["request_id"]
    with pytest.raises(EvidenceEnvelopeError, match="request_id"):
        parse_evidence_package(data)


def test_malformed_evidence_package_invalid_lane_rejected():
    data = {**_valid_package_data(), "lane": "not_a_lane"}
    with pytest.raises(EvidenceEnvelopeError, match="lane"):
        parse_evidence_package(data)


def test_malformed_evidence_package_items_not_a_list_rejected():
    data = {**_valid_package_data(), "items": "not-a-list"}
    with pytest.raises(EvidenceEnvelopeError):
        parse_evidence_package(data)


def test_malformed_evidence_package_propagates_item_error():
    """A malformed item nested inside an otherwise well-formed package must
    still fail the whole package -- Gate-C never receives a package half
    built from unchecked item data."""
    data = _valid_package_data()
    del data["items"][0]["source_id"]
    with pytest.raises(EvidenceEnvelopeError, match="source_id"):
        parse_evidence_package(data)


def test_duplicate_evidence_id_within_package_rejected():
    data = _valid_package_data()
    second = copy.deepcopy(data["items"][0])
    data["items"] = [data["items"][0], second]
    with pytest.raises(EvidenceEnvelopeError, match="duplicate evidence_id"):
        parse_evidence_package(data)


def test_parse_errors_never_leak_raw_pydantic_validation_error():
    """`parse_evidence_item`/`parse_evidence_package` always translate
    Pydantic's own exception into `EvidenceEnvelopeError` -- a caller never
    needs to know Pydantic's exception shape (mirrors
    `contracts/validator.py`'s identical boundary for Day 4)."""
    data = _valid_item_data()
    del data["source_id"]
    try:
        parse_evidence_item(data)
    except EvidenceEnvelopeError as exc:
        assert exc.field_path == "source_id"
    else:
        pytest.fail("expected EvidenceEnvelopeError")


# --- claims (added for Task 8's conflict detection) ------------------------


def test_claims_defaults_to_empty_dict():
    item = parse_evidence_item(_valid_item_data())
    assert item.claims == {}


def test_claims_accepts_per_facet_values():
    data = {**_valid_item_data(), "claims": {"supplier_identity": "Synthetic Supplier Alpha", "payment_terms": "net 30"}}
    item = parse_evidence_item(data)
    assert item.claims == {"supplier_identity": "Synthetic Supplier Alpha", "payment_terms": "net 30"}


def test_blank_claim_value_rejected():
    data = {**_valid_item_data(), "claims": {"payment_terms": "   "}}
    with pytest.raises(EvidenceEnvelopeError):
        parse_evidence_item(data)


def test_blank_claim_key_rejected():
    data = {**_valid_item_data(), "claims": {"": "net 30"}}
    with pytest.raises(EvidenceEnvelopeError):
        parse_evidence_item(data)
