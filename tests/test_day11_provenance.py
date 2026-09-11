"""
Day 11 Task 4 -- provenance validation (`src/aico/evidence/provenance.py`).
Day 11 Task 5 -- integrity / mutation check (same module).

Proves each of Task 4's five "At minimum prove" bullets in isolation
(unknown source, content-hash self-consistency, Gate-B tenant/data-
classification scope, the two package-level cross-item consistency checks
standing in for "source_version matches" / "stable identity" -- see
`provenance.py`'s own docstring for why those two are necessarily
package-level rather than per-field), then against real
`gate_c_cases.json` cases adapted for this module's hash representation
(the fixture's own documented allowance: "implementation may map them
into its normal hash/provenance representation").

Task 4's critical rule -- "do not validate the corpus, validate the
evidence actually returned" -- is proven structurally here: every test
builds an `EvidencePackage`/`EvidenceItem` by hand or from the fixture's
own `items`, never touches `data/documents/` or any index, and
`validate_provenance()`'s signature has no parameter for one.

The second half of this file proves Task 5's `validate_integrity()`
against its four named "Required behavior" bullets, each checked against
an independent, caller-supplied `GovernedProvenanceIndex` rather than only
the item's own self-reported fields -- including the specific "content
changed *and* its accompanying hash was recomputed to match" forgery
Task 4's self-consistency check alone cannot catch, and the "missing
governed reference fails closed, never silently allowed" anti-pattern
Task 5 explicitly names.

Gate-C itself (Task 9) is not implemented yet and is out of scope here --
disabled-source rejection (Task 2's own concern) and the overall
`expected_decision` an end-to-end pipeline would reach are not
re-derived; this file only proves what `provenance.py` itself is
documented to check.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aico.control.models import GateBDecision, GateBStatus
from aico.control.ontology import LaneId
from aico.control.policy_models import DataClassification
from aico.evidence.models import EvidenceItem, EvidencePackage
from aico.evidence.provenance import (
    GovernedProvenanceIndex,
    GovernedProvenanceRecord,
    IntegrityFailureReason,
    IntegrityReport,
    ProvenanceFailureReason,
    ProvenanceReport,
    stable_content_hash,
    validate_integrity,
    validate_provenance,
)
from aico.evidence.source_registry import SourceRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE_C_CASES_PATH = REPO_ROOT / "data" / "day11_pack" / "fixtures" / "gate_c_cases.json"

GATE_C_CASES = json.loads(GATE_C_CASES_PATH.read_text(encoding="utf-8"))
CASES_BY_ID = {case["id"]: case for case in GATE_C_CASES["cases"]}


@pytest.fixture(scope="module")
def source_registry() -> SourceRegistry:
    return SourceRegistry.load()


def _allow_decision(*, tenant_ids: tuple[str, ...], data_classes: tuple[DataClassification, ...]) -> GateBDecision:
    """A minimal, real `GateBDecision` (`aico.control.models`) granting
    exactly the given effective scope -- provenance validation is checked
    against this typed object, never a bespoke stand-in scope type."""
    return GateBDecision(
        decision=GateBStatus.ALLOW,
        effective_tenant_scope=tenant_ids,
        effective_data_classes=data_classes,
        effective_pii_policy=(),
        disclosure_profile="policy_reader",
        role_id="supplier_reader",
        intent_id="INT-POLICY-QUESTION",
        lane=LaneId.RAG,
        rule_id="GB-R001",
        reason_code="test_fixture",
        policy_version="1.0",
    )


def _deny_decision() -> GateBDecision:
    return GateBDecision(decision=GateBStatus.DENY, reason_code="test_fixture", policy_version="1.0")


def _valid_item(**overrides) -> dict:
    content = "Synthetic Supplier Alpha uses net 30 payment terms."
    data = {
        "evidence_id": "E-001",
        "chunk_id": "CH-001",
        "source_id": "SRC-POLICY-A",
        "source_version": "3",
        "source_updated_at": "2026-09-05T09:00:00+05:00",
        "retrieved_at": "2026-09-11T11:59:00+05:00",
        "content_hash": stable_content_hash(content),
        "tenant_id": "TENANT-A",
        "data_classification": "internal",
        "evidence_facets": ["supplier_identity", "payment_terms"],
        "content": content,
    }
    data.update(overrides)
    return data


def _package(items: list[dict], **overrides) -> EvidencePackage:
    data = {
        "request_id": "REQ-001",
        "intent_id": "INT-POLICY-QUESTION",
        "lane": "rag",
        "as_of": "2026-09-11T12:00:00+05:00",
        "required_facets": ["supplier_identity", "payment_terms"],
        "items": items,
    }
    data.update(overrides)
    return EvidencePackage.model_validate(data)


# ---------------------------------------------------------------------------
# stable_content_hash
# ---------------------------------------------------------------------------


def test_stable_content_hash_is_deterministic():
    assert stable_content_hash("hello world") == stable_content_hash("hello world")


def test_stable_content_hash_differs_for_different_content():
    assert stable_content_hash("hello world") != stable_content_hash("hello world!")


def test_stable_content_hash_matches_chunker_scheme():
    """Identical scheme to `retrieval/chunker.py`'s own `_content_hash()`
    -- full SHA-256 hex digest of the UTF-8 text, not an independently
    invented one."""
    import hashlib

    text = "Synthetic Supplier Alpha uses net 30 payment terms."
    assert stable_content_hash(text) == hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# A fully valid item passes every Task 4 check
# ---------------------------------------------------------------------------


def test_valid_item_passes_every_check(source_registry):
    package = _package([_valid_item()])
    decision = _allow_decision(tenant_ids=("TENANT-A",), data_classes=(DataClassification.INTERNAL,))

    report = validate_provenance(package, source_registry=source_registry, gate_b_decision=decision)

    assert isinstance(report, ProvenanceReport)
    assert report.validated_evidence_ids == ("E-001",)
    assert report.rejected_evidence_ids == ()
    result = report.result_for("E-001")
    assert result.valid is True
    assert result.reasons == ()


# ---------------------------------------------------------------------------
# Task 4: evidence source_id exists
# ---------------------------------------------------------------------------


def test_unknown_source_rejected(source_registry):
    package = _package([_valid_item(source_id="SRC-DOES-NOT-EXIST")])
    decision = _allow_decision(tenant_ids=("TENANT-A",), data_classes=(DataClassification.INTERNAL,))

    report = validate_provenance(package, source_registry=source_registry, gate_b_decision=decision)

    result = report.result_for("E-001")
    assert result.valid is False
    assert ProvenanceFailureReason.UNKNOWN_SOURCE in result.reasons
    assert "E-001" in report.rejected_evidence_ids


def test_known_but_disabled_source_is_not_provenance_s_own_concern(source_registry):
    """`SRC-ARCHIVE-A` is `status: disabled` in the real committed
    registry -- it exists, so provenance's own "source_id exists" check
    passes; rejecting a disabled source is Task 2's `is_source_active()`
    bullet, composed by Gate-C (Task 9), not re-implemented here."""
    package = _package([_valid_item(source_id="SRC-ARCHIVE-A", evidence_facets=["payment_terms"])])
    decision = _allow_decision(tenant_ids=("TENANT-A",), data_classes=(DataClassification.INTERNAL,))

    report = validate_provenance(package, source_registry=source_registry, gate_b_decision=decision)

    result = report.result_for("E-001")
    assert ProvenanceFailureReason.UNKNOWN_SOURCE not in result.reasons
    assert source_registry.is_source_active("SRC-ARCHIVE-A") is False  # the separate Task 2 check


# ---------------------------------------------------------------------------
# Task 4: content hash matches the returned content
# ---------------------------------------------------------------------------


def test_matching_content_hash_passes(source_registry):
    package = _package([_valid_item()])
    decision = _allow_decision(tenant_ids=("TENANT-A",), data_classes=(DataClassification.INTERNAL,))
    report = validate_provenance(package, source_registry=source_registry, gate_b_decision=decision)
    assert ProvenanceFailureReason.CONTENT_HASH_MISMATCH not in report.result_for("E-001").reasons


def test_stale_hash_after_content_mutation_rejected(source_registry):
    """Task 5's defining scenario, provable now: content changed, the
    hash attached to the item did not -- a fresh hash of the current
    (mutated) content will not match the stale value."""
    original_content = "Synthetic Supplier Alpha uses net 30 payment terms."
    item = _valid_item(content_hash=stable_content_hash(original_content), content="Mutated content, not the original.")
    package = _package([item])
    decision = _allow_decision(tenant_ids=("TENANT-A",), data_classes=(DataClassification.INTERNAL,))

    report = validate_provenance(package, source_registry=source_registry, gate_b_decision=decision)

    result = report.result_for("E-001")
    assert result.valid is False
    assert ProvenanceFailureReason.CONTENT_HASH_MISMATCH in result.reasons


# ---------------------------------------------------------------------------
# Task 4: tenant/data classification stays within Gate-B effective scope
# ---------------------------------------------------------------------------


def test_tenant_outside_gate_b_scope_rejected(source_registry):
    package = _package([_valid_item(tenant_id="TENANT-B")])
    decision = _allow_decision(tenant_ids=("TENANT-A",), data_classes=(DataClassification.INTERNAL,))

    report = validate_provenance(package, source_registry=source_registry, gate_b_decision=decision)

    result = report.result_for("E-001")
    assert result.valid is False
    assert ProvenanceFailureReason.TENANT_OUT_OF_SCOPE in result.reasons


def test_data_classification_outside_gate_b_scope_rejected(source_registry):
    package = _package([_valid_item(data_classification="restricted")])
    decision = _allow_decision(tenant_ids=("TENANT-A",), data_classes=(DataClassification.INTERNAL,))

    report = validate_provenance(package, source_registry=source_registry, gate_b_decision=decision)

    result = report.result_for("E-001")
    assert result.valid is False
    assert ProvenanceFailureReason.DATA_CLASSIFICATION_OUT_OF_SCOPE in result.reasons


def test_non_allow_gate_b_decision_rejects_every_item_on_scope_grounds(source_registry):
    """A `DENY`/`CLARIFY` `GateBDecision` carries empty effective scopes by
    construction -- every item fails the scope check automatically, fail
    closed, not a special case this module needs to detect separately
    (working rule: "Gate-C must not widen Gate-B authorization scope")."""
    package = _package([_valid_item()])
    decision = _deny_decision()

    report = validate_provenance(package, source_registry=source_registry, gate_b_decision=decision)

    result = report.result_for("E-001")
    assert result.valid is False
    assert ProvenanceFailureReason.TENANT_OUT_OF_SCOPE in result.reasons
    assert ProvenanceFailureReason.DATA_CLASSIFICATION_OUT_OF_SCOPE in result.reasons


def test_gate_b_scope_check_never_widens_to_a_tenant_or_class_not_declared(source_registry):
    """A tenant/classification simply absent from the requested scope
    stays rejected even though it would be a perfectly normal governed
    value elsewhere -- provenance only ever narrows against what Gate-B
    actually granted this decision, never infers a broader default."""
    package = _package([_valid_item(tenant_id="TENANT-A", data_classification="public")])
    decision = _allow_decision(tenant_ids=("TENANT-A",), data_classes=(DataClassification.INTERNAL,))

    report = validate_provenance(package, source_registry=source_registry, gate_b_decision=decision)

    assert ProvenanceFailureReason.DATA_CLASSIFICATION_OUT_OF_SCOPE in report.result_for("E-001").reasons


# ---------------------------------------------------------------------------
# Task 4: evidence source_version matches the governed/returned source
# version (package-level cross-item consistency -- see provenance.py)
# ---------------------------------------------------------------------------


def test_agreeing_source_versions_across_items_pass(source_registry):
    package = _package(
        [
            _valid_item(evidence_id="E-001", chunk_id="CH-001", source_version="3"),
            _valid_item(evidence_id="E-002", chunk_id="CH-002", source_version="3"),
        ]
    )
    decision = _allow_decision(tenant_ids=("TENANT-A",), data_classes=(DataClassification.INTERNAL,))

    report = validate_provenance(package, source_registry=source_registry, gate_b_decision=decision)

    assert report.validated_evidence_ids == ("E-001", "E-002")


def test_conflicting_source_versions_for_the_same_source_rejects_both(source_registry):
    """Two items both claiming `source_id="SRC-POLICY-A"` but disagreeing
    on `source_version` -- retrieval contradicting itself about what state
    the source was in for this request. Both sides of the disagreement are
    flagged, not just the second one seen."""
    package = _package(
        [
            _valid_item(evidence_id="E-001", chunk_id="CH-001", source_version="3"),
            _valid_item(evidence_id="E-002", chunk_id="CH-002", source_version="4"),
        ]
    )
    decision = _allow_decision(tenant_ids=("TENANT-A",), data_classes=(DataClassification.INTERNAL,))

    report = validate_provenance(package, source_registry=source_registry, gate_b_decision=decision)

    assert ProvenanceFailureReason.SOURCE_VERSION_MISMATCH in report.result_for("E-001").reasons
    assert ProvenanceFailureReason.SOURCE_VERSION_MISMATCH in report.result_for("E-002").reasons
    assert set(report.rejected_evidence_ids) == {"E-001", "E-002"}


def test_different_sources_may_freely_use_different_versions(source_registry):
    """The cross-item check only compares items that share a `source_id`
    -- two items from genuinely different sources disagreeing about
    "version" is meaningless and must not be flagged."""
    package = _package(
        [
            _valid_item(evidence_id="E-001", chunk_id="CH-001", source_id="SRC-POLICY-A", source_version="3"),
            _valid_item(
                evidence_id="E-002",
                chunk_id="CH-002",
                source_id="SRC-CONTRACT-A",
                source_version="9",
                content="Contract record content.",
                content_hash=stable_content_hash("Contract record content."),
            ),
        ]
    )
    decision = _allow_decision(tenant_ids=("TENANT-A",), data_classes=(DataClassification.INTERNAL,))

    report = validate_provenance(package, source_registry=source_registry, gate_b_decision=decision)

    assert report.validated_evidence_ids == ("E-001", "E-002")


# ---------------------------------------------------------------------------
# Task 4: evidence record/chunk carries stable identity (package-level
# cross-item consistency -- see provenance.py)
# ---------------------------------------------------------------------------


def test_shared_chunk_id_with_agreeing_identity_passes(source_registry):
    """Genuine duplicate/supporting evidence -- the same chunk returned
    twice with identical identity -- is not a broken identity."""
    item = _valid_item(evidence_id="E-001")
    duplicate = _valid_item(evidence_id="E-002")  # same chunk_id, source_id, content_hash
    package = _package([item, duplicate])
    decision = _allow_decision(tenant_ids=("TENANT-A",), data_classes=(DataClassification.INTERNAL,))

    report = validate_provenance(package, source_registry=source_registry, gate_b_decision=decision)

    assert report.validated_evidence_ids == ("E-001", "E-002")


def test_shared_chunk_id_with_conflicting_source_rejects_both(source_registry):
    """Two items claiming the identical `chunk_id` but a different
    `source_id` -- the same stable identity cannot legitimately belong to
    two different governed sources."""
    package = _package(
        [
            _valid_item(evidence_id="E-001", chunk_id="CH-SHARED", source_id="SRC-POLICY-A"),
            _valid_item(
                evidence_id="E-002",
                chunk_id="CH-SHARED",
                source_id="SRC-CONTRACT-A",
                content="Different content entirely.",
                content_hash=stable_content_hash("Different content entirely."),
            ),
        ]
    )
    decision = _allow_decision(tenant_ids=("TENANT-A",), data_classes=(DataClassification.INTERNAL,))

    report = validate_provenance(package, source_registry=source_registry, gate_b_decision=decision)

    assert ProvenanceFailureReason.UNSTABLE_IDENTITY in report.result_for("E-001").reasons
    assert ProvenanceFailureReason.UNSTABLE_IDENTITY in report.result_for("E-002").reasons


def test_shared_chunk_id_with_conflicting_content_hash_rejects_both(source_registry):
    """Same `chunk_id` and `source_id`, but a different `content_hash` --
    the identity claims two different pieces of content for what should be
    one stable record."""
    package = _package(
        [
            _valid_item(evidence_id="E-001", chunk_id="CH-SHARED"),
            _valid_item(
                evidence_id="E-002",
                chunk_id="CH-SHARED",
                content="A completely different payment terms statement.",
                content_hash=stable_content_hash("A completely different payment terms statement."),
            ),
        ]
    )
    decision = _allow_decision(tenant_ids=("TENANT-A",), data_classes=(DataClassification.INTERNAL,))

    report = validate_provenance(package, source_registry=source_registry, gate_b_decision=decision)

    assert ProvenanceFailureReason.UNSTABLE_IDENTITY in report.result_for("E-001").reasons
    assert ProvenanceFailureReason.UNSTABLE_IDENTITY in report.result_for("E-002").reasons


# ---------------------------------------------------------------------------
# ProvenanceReport.result_for
# ---------------------------------------------------------------------------


def test_result_for_unknown_evidence_id_raises_key_error(source_registry):
    package = _package([_valid_item()])
    decision = _allow_decision(tenant_ids=("TENANT-A",), data_classes=(DataClassification.INTERNAL,))
    report = validate_provenance(package, source_registry=source_registry, gate_b_decision=decision)

    with pytest.raises(KeyError):
        report.result_for("E-DOES-NOT-EXIST")


def test_report_never_mutates_inputs(source_registry):
    package = _package([_valid_item()])
    decision = _allow_decision(tenant_ids=("TENANT-A",), data_classes=(DataClassification.INTERNAL,))
    items_before = package.items

    validate_provenance(package, source_registry=source_registry, gate_b_decision=decision)

    assert package.items == items_before
    assert isinstance(package.items[0], EvidenceItem)


# ---------------------------------------------------------------------------
# gate_c_cases.json -- real fixture cases, remapped for this module's hash
# representation per the fixture's own documented allowance
# ---------------------------------------------------------------------------


def _item_from_case(case: dict, *, matching_hash: bool) -> dict:
    """Adapt one `gate_c_cases.json` item into `EvidenceItem` data. The
    fixture's `expected_content_hash`/`provided_content_hash` are
    documented synthetic placeholders, not real hashes of `content`
    (`gate_c_cases.json`'s own note) -- `matching_hash=True` computes the
    real `stable_content_hash()` of the case's `content` (so the item is
    genuinely self-consistent, reproducing a "valid provenance" case
    honestly rather than by coincidence); `matching_hash=False` uses the
    fixture's own `provided_content_hash` string directly, which -- being
    a synthetic label, not a real digest -- reliably does not match a
    fresh hash of the real content, reproducing the fixture's intended
    "mutated" outcome."""
    raw_item = case["items"][0]
    content = raw_item["content"]
    content_hash = stable_content_hash(content) if matching_hash else raw_item["provided_content_hash"]
    return {
        "evidence_id": raw_item["evidence_id"],
        "chunk_id": raw_item["chunk_id"],
        "source_id": raw_item["source_id"],
        "source_version": raw_item["source_version"],
        "source_updated_at": raw_item["source_updated_at"],
        "retrieved_at": raw_item["retrieved_at"],
        "content_hash": content_hash,
        "tenant_id": raw_item["tenant_id"],
        "data_classification": raw_item["data_classification"],
        "evidence_facets": raw_item["evidence_facets"],
        "content": content,
    }


def _package_from_case(case: dict, *, matching_hash: bool) -> EvidencePackage:
    return _package([_item_from_case(case, matching_hash=matching_hash)], intent_id=case["intent_id"])


def _decision_from_case(case: dict) -> GateBDecision:
    scope = case["gate_b_scope"]
    data_classes = tuple(DataClassification(c) for c in scope["data_classes"])
    return _allow_decision(tenant_ids=tuple(scope["tenant_ids"]), data_classes=data_classes)


def test_gc001_valid_payment_evidence_passes_provenance(source_registry):
    case = CASES_BY_ID["GC-001"]
    package = _package_from_case(case, matching_hash=True)
    decision = _decision_from_case(case)

    report = validate_provenance(package, source_registry=source_registry, gate_b_decision=decision)

    result = report.result_for("E-001")
    assert result.valid is True
    assert result.reasons == ()


def test_gc002_unknown_source_rejected_by_provenance(source_registry):
    case = CASES_BY_ID["GC-002"]
    package = _package_from_case(case, matching_hash=True)
    decision = _decision_from_case(case)

    report = validate_provenance(package, source_registry=source_registry, gate_b_decision=decision)

    result = report.result_for("E-002")
    assert result.valid is False
    assert ProvenanceFailureReason.UNKNOWN_SOURCE in result.reasons


def test_gc004_hash_mismatch_rejected_by_provenance(source_registry):
    case = CASES_BY_ID["GC-004"]
    package = _package_from_case(case, matching_hash=False)
    decision = _decision_from_case(case)

    report = validate_provenance(package, source_registry=source_registry, gate_b_decision=decision)

    result = report.result_for("E-004")
    assert result.valid is False
    assert ProvenanceFailureReason.CONTENT_HASH_MISMATCH in result.reasons


def test_gc005_cross_tenant_evidence_rejected_by_provenance(source_registry):
    case = CASES_BY_ID["GC-005"]
    package = _package_from_case(case, matching_hash=True)
    decision = _decision_from_case(case)

    report = validate_provenance(package, source_registry=source_registry, gate_b_decision=decision)

    result = report.result_for("E-005")
    assert result.valid is False
    assert ProvenanceFailureReason.TENANT_OUT_OF_SCOPE in result.reasons


# ===========================================================================
# Task 5 -- validate_integrity()
# ===========================================================================


def _governed_record(**overrides) -> GovernedProvenanceRecord:
    content = "Synthetic Supplier Alpha uses net 30 payment terms."
    data = {
        "source_id": "SRC-POLICY-A",
        "chunk_id": "CH-001",
        "source_version": "3",
        "content_hash": stable_content_hash(content),
    }
    data.update(overrides)
    return GovernedProvenanceRecord(**data)


# ---------------------------------------------------------------------------
# "unchanged content + same version -> integrity passes"
# ---------------------------------------------------------------------------


def test_unchanged_content_and_matching_version_passes_integrity():
    index = GovernedProvenanceIndex([_governed_record()])

    report = validate_integrity(_package([_valid_item()]), provenance_index=index)

    result = report.result_for("E-001")
    assert result.valid is True
    assert result.reasons == ()
    assert report.validated_evidence_ids == ("E-001",)


def test_validate_integrity_returns_typed_report():
    index = GovernedProvenanceIndex([_governed_record()])
    report = validate_integrity(_package([_valid_item()]), provenance_index=index)
    assert isinstance(report, IntegrityReport)


# ---------------------------------------------------------------------------
# "content changed but old hash retained -> fail"
# ---------------------------------------------------------------------------


def test_content_changed_but_old_hash_retained_fails():
    """The item's own content_hash was never updated after content was
    mutated -- caught by the self-consistency dimension
    (stable_content_hash(item.content) != item.content_hash), the same
    check validate_provenance() performs, reused here rather than
    duplicated."""
    original_content = "Synthetic Supplier Alpha uses net 30 payment terms."
    item_data = _valid_item(
        content_hash=stable_content_hash(original_content), content="Mutated content, hash not updated."
    )
    index = GovernedProvenanceIndex([_governed_record(content_hash=stable_content_hash(original_content))])

    report = validate_integrity(_package([item_data]), provenance_index=index)

    result = report.result_for("E-001")
    assert result.valid is False
    assert IntegrityFailureReason.CONTENT_HASH_MISMATCH in result.reasons


# ---------------------------------------------------------------------------
# The forgery scenario self-consistency alone cannot catch: content AND
# its own hash were both changed together, but the governed record was not
# ---------------------------------------------------------------------------


def test_coordinated_content_and_hash_mutation_still_caught_against_governed_record():
    """Self-consistency alone (Task 4's validate_provenance()) would
    pass this item outright: content and content_hash were both
    updated together, so they agree with each other. Only comparing
    against the independent governed record catches that they no longer
    agree with what was actually ingested -- exactly the gap Task 5 exists
    to close."""
    mutated_content = "Forged: Synthetic Supplier Alpha uses net 90 payment terms."
    item_data = _valid_item(content=mutated_content, content_hash=stable_content_hash(mutated_content))
    index = GovernedProvenanceIndex([_governed_record()])  # governed hash is of the ORIGINAL content

    # Self-consistency alone: this item looks perfectly fine.
    assert stable_content_hash(item_data["content"]) == item_data["content_hash"]

    report = validate_integrity(_package([item_data]), provenance_index=index)

    result = report.result_for("E-001")
    assert result.valid is False
    assert IntegrityFailureReason.CONTENT_HASH_MISMATCH not in result.reasons  # self-consistent
    assert IntegrityFailureReason.EXPECTED_HASH_MISMATCH in result.reasons  # but not the governed truth


# ---------------------------------------------------------------------------
# "source version mismatch -> fail"
# ---------------------------------------------------------------------------


def test_source_version_mismatch_against_governed_record_fails():
    item_data = _valid_item(source_version="4")  # governed record below still says "3"
    index = GovernedProvenanceIndex([_governed_record(source_version="3")])

    report = validate_integrity(_package([item_data]), provenance_index=index)

    result = report.result_for("E-001")
    assert result.valid is False
    assert IntegrityFailureReason.SOURCE_VERSION_MISMATCH in result.reasons
    # Content itself is untouched and correct -- only the version disagrees.
    assert IntegrityFailureReason.CONTENT_HASH_MISMATCH not in result.reasons
    assert IntegrityFailureReason.EXPECTED_HASH_MISMATCH not in result.reasons


def test_matching_source_version_does_not_fail_on_version_grounds():
    index = GovernedProvenanceIndex([_governed_record(source_version="3")])
    report = validate_integrity(_package([_valid_item(source_version="3")]), provenance_index=index)
    assert IntegrityFailureReason.SOURCE_VERSION_MISMATCH not in report.result_for("E-001").reasons


# ---------------------------------------------------------------------------
# "missing hash -> fail" (no governed reference resolves for this item)
# ---------------------------------------------------------------------------


def test_missing_governed_record_fails_closed():
    """No GovernedProvenanceRecord exists for this items
    (source_id, chunk_id) at all -- fails closed (MISSING_EXPECTED_RECORD),
    never silently falls back to treating the item as valid just because
    nothing contradicts it (Task 5's own anti-pattern warning: "Do not
    silently recompute a missing/incorrect expected hash and then treat
    the item as valid")."""
    empty_index = GovernedProvenanceIndex([])

    report = validate_integrity(_package([_valid_item()]), provenance_index=empty_index)

    result = report.result_for("E-001")
    assert result.valid is False
    assert result.reasons == (IntegrityFailureReason.MISSING_EXPECTED_RECORD,)
    assert report.rejected_evidence_ids == ("E-001",)


def test_missing_governed_record_never_falls_back_to_self_consistency_alone():
    """Even a perfectly self-consistent item (real content, real matching
    hash) must still fail when no governed record resolves for it --
    self-consistency is not a substitute for a governed reference."""
    empty_index = GovernedProvenanceIndex([])
    item_data = _valid_item()  # content_hash is already the real hash of content
    assert stable_content_hash(item_data["content"]) == item_data["content_hash"]

    report = validate_integrity(_package([item_data]), provenance_index=empty_index)

    assert report.result_for("E-001").valid is False


# ---------------------------------------------------------------------------
# GovernedProvenanceIndex
# ---------------------------------------------------------------------------


def test_governed_provenance_index_resolves_by_source_and_chunk_id():
    record = _governed_record(source_id="SRC-POLICY-A", chunk_id="CH-001")
    index = GovernedProvenanceIndex([record])

    assert index.get("SRC-POLICY-A", "CH-001") == record
    assert index.get("SRC-POLICY-A", "CH-DIFFERENT") is None
    assert index.get("SRC-DIFFERENT", "CH-001") is None


def test_governed_provenance_index_empty_by_default():
    assert GovernedProvenanceIndex().get("anything", "anything") is None


# ---------------------------------------------------------------------------
# Package-level aggregation / IntegrityReport.result_for
# ---------------------------------------------------------------------------


def test_validate_integrity_aggregates_multiple_items():
    good_content = "Synthetic Supplier Alpha uses net 30 payment terms."
    bad_item = _valid_item(evidence_id="E-002", chunk_id="CH-002", content_hash="not-a-real-hash")
    package = _package(
        [
            _valid_item(
                evidence_id="E-001",
                chunk_id="CH-001",
                content=good_content,
                content_hash=stable_content_hash(good_content),
            ),
            bad_item,
        ]
    )
    index = GovernedProvenanceIndex(
        [
            _governed_record(source_id="SRC-POLICY-A", chunk_id="CH-001", content_hash=stable_content_hash(good_content)),
            # No record for CH-002 at all -- E-002 fails closed regardless of its own hash.
        ]
    )

    report = validate_integrity(package, provenance_index=index)

    assert report.validated_evidence_ids == ("E-001",)
    assert report.rejected_evidence_ids == ("E-002",)
    assert report.result_for("E-002").reasons == (IntegrityFailureReason.MISSING_EXPECTED_RECORD,)


def test_integrity_report_result_for_unknown_evidence_id_raises_key_error():
    index = GovernedProvenanceIndex([_governed_record()])
    report = validate_integrity(_package([_valid_item()]), provenance_index=index)

    with pytest.raises(KeyError):
        report.result_for("E-DOES-NOT-EXIST")


def test_validate_integrity_never_mutates_inputs():
    index = GovernedProvenanceIndex([_governed_record()])
    package = _package([_valid_item()])
    items_before = package.items

    validate_integrity(package, provenance_index=index)

    assert package.items == items_before


# ---------------------------------------------------------------------------
# gate_c_cases.json GC-001/GC-004, adapted for the expected-vs-governed split
# Task 5's design maps onto directly (unlike Task 4's self-consistency-only
# check, this is exactly what expected_content_hash/provided_content_hash
# were made for).
# ---------------------------------------------------------------------------


def test_gc001_valid_payment_evidence_passes_integrity_against_governed_record():
    case = CASES_BY_ID["GC-001"]
    raw_item = case["items"][0]
    content = raw_item["content"]
    real_hash = stable_content_hash(content)
    item_data = _item_from_case(case, matching_hash=True)
    index = GovernedProvenanceIndex(
        [
            GovernedProvenanceRecord(
                source_id=raw_item["source_id"],
                chunk_id=raw_item["chunk_id"],
                source_version=raw_item["source_version"],
                content_hash=real_hash,  # provided_content_hash == expected_content_hash in this fixture
            )
        ]
    )

    report = validate_integrity(_package([item_data]), provenance_index=index)

    result = report.result_for(raw_item["evidence_id"])
    assert result.valid is True
    assert result.reasons == ()


def test_gc004_hash_mismatch_caught_even_when_self_consistent():
    """GC-004's real intent (expected_content_hash != provided_content_hash):
    the returned item was forged to be internally self-consistent (its own
    hash was recomputed to match its own, mutated content), but the
    governed record -- untouched by the forgery -- still holds the
    original, correct hash. validate_integrity() is what catches this;
    validate_provenance()'s self-consistency check alone would not."""
    case = CASES_BY_ID["GC-004"]
    raw_item = case["items"][0]
    mutated_content = raw_item["content"]
    item_data = _item_from_case(case, matching_hash=True)  # self-consistent: forged hash matches mutated content
    original_content = "Synthetic Supplier Alpha uses net 5 payment terms (original, before mutation)."
    index = GovernedProvenanceIndex(
        [
            GovernedProvenanceRecord(
                source_id=raw_item["source_id"],
                chunk_id=raw_item["chunk_id"],
                source_version=raw_item["source_version"],
                content_hash=stable_content_hash(original_content),  # what governed ingestion actually recorded
            )
        ]
    )
    assert stable_content_hash(mutated_content) == item_data["content_hash"]  # self-consistency alone would pass

    report = validate_integrity(_package([item_data]), provenance_index=index)

    result = report.result_for(raw_item["evidence_id"])
    assert result.valid is False
    assert IntegrityFailureReason.CONTENT_HASH_MISMATCH not in result.reasons
    assert IntegrityFailureReason.EXPECTED_HASH_MISMATCH in result.reasons
