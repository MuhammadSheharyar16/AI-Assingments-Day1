"""
Day 11 Task 7 -- completeness validation (`src/aico/evidence/completeness.py`).

Proves every Task 7 "Required behavior" bullet in isolation, against the
real committed `SourceRegistry` (Task 2) throughout -- "unsupported facet
claim cannot be manufactured by the model" is only meaningful checked
against real governed `supported_facets` data, not a stand-in. The second
half adapts all four real `completeness_cases.json` cases (COMP-001..004)
directly: those fixtures carry `evidence_id`/`facets` (and, for COMP-004,
an explicit per-item `valid` flag) with no `source_id` at all, so every
adapted item is built against `SRC-POLICY-A` -- the one real governed
source whose own `supported_facets` (`supplier_identity`, `payment_terms`,
`invoice_window`) happens to cover every facet these particular fixtures
use, so no fixture item's claim is itself "unsupported" and each case
proves exactly what it names.

Gate-C itself (Task 9) is not implemented yet and is out of scope here --
this file only proves `validate_completeness()`'s own documented
behavior, never a full end-to-end `expected_decision`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aico.evidence.completeness import CompletenessResult, CompletenessStatus, validate_completeness
from aico.evidence.models import EvidencePackage
from aico.evidence.source_registry import SourceRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPLETENESS_CASES_PATH = REPO_ROOT / "data" / "day11_pack" / "fixtures" / "completeness_cases.json"
COMPLETENESS_CASES = json.loads(COMPLETENESS_CASES_PATH.read_text(encoding="utf-8"))["cases"]
CASES_BY_ID = {case["id"]: case for case in COMPLETENESS_CASES}


@pytest.fixture(scope="module")
def source_registry() -> SourceRegistry:
    return SourceRegistry.load()


def _item(evidence_id: str, facets: list[str], *, source_id: str = "SRC-POLICY-A", **overrides) -> dict:
    data = {
        "evidence_id": evidence_id,
        "chunk_id": f"CH-{evidence_id}",
        "source_id": source_id,
        "source_version": "3",
        "source_updated_at": "2026-09-05T09:00:00+05:00",
        "retrieved_at": "2026-09-11T11:59:00+05:00",
        "content_hash": "irrelevant-for-completeness",
        "tenant_id": "TENANT-A",
        "data_classification": "internal",
        "evidence_facets": facets,
        "content": f"Content for {evidence_id}.",
    }
    data.update(overrides)
    return data


def _package(items: list[dict], required_facets: list[str], **overrides) -> EvidencePackage:
    data = {
        "request_id": "REQ-001",
        "intent_id": "INT-POLICY-QUESTION",
        "lane": "rag",
        "as_of": "2026-09-11T12:00:00+05:00",
        "required_facets": required_facets,
        "items": items,
    }
    data.update(overrides)
    return EvidencePackage.model_validate(data)


REQUIRED = ["supplier_identity", "payment_terms", "invoice_window"]


# ---------------------------------------------------------------------------
# "all required facets covered -> may pass"
# ---------------------------------------------------------------------------


def test_all_required_facets_covered_passes(source_registry):
    package = _package(
        [
            _item("E-A", ["supplier_identity", "payment_terms"]),
            _item("E-B", ["invoice_window"]),
        ],
        REQUIRED,
    )

    result = validate_completeness(package, valid_evidence_ids={"E-A", "E-B"}, source_registry=source_registry)

    assert isinstance(result, CompletenessResult)
    assert result.status is CompletenessStatus.COMPLETE
    assert result.missing_facets == ()
    assert set(result.covered_facets) == set(REQUIRED)


def test_empty_required_facets_is_trivially_complete(source_registry):
    package = _package([], [])
    result = validate_completeness(package, valid_evidence_ids=set(), source_registry=source_registry)
    assert result.status is CompletenessStatus.COMPLETE
    assert result.missing_facets == ()


# ---------------------------------------------------------------------------
# "one required facet missing -> insufficient"
# ---------------------------------------------------------------------------


def test_one_required_facet_missing_is_insufficient(source_registry):
    package = _package([_item("E-A", ["supplier_identity", "payment_terms"])], REQUIRED)

    result = validate_completeness(package, valid_evidence_ids={"E-A"}, source_registry=source_registry)

    assert result.status is CompletenessStatus.INSUFFICIENT_EVIDENCE
    assert result.missing_facets == ("invoice_window",)
    assert "invoice_window" not in result.covered_facets


# ---------------------------------------------------------------------------
# "duplicate chunks covering the same facet do not count as another
# missing facet"
# ---------------------------------------------------------------------------


def test_duplicate_coverage_of_the_same_facet_is_not_double_counted(source_registry):
    package = _package(
        [
            _item("E-A", ["supplier_identity", "payment_terms"]),
            _item("E-B", ["payment_terms"]),
            _item("E-C", ["payment_terms"]),
        ],
        REQUIRED,
    )

    result = validate_completeness(package, valid_evidence_ids={"E-A", "E-B", "E-C"}, source_registry=source_registry)

    # payment_terms is claimed three times over -- still just one covered
    # facet, and invoice_window (claimed by nobody) is still missing.
    assert result.status is CompletenessStatus.INSUFFICIENT_EVIDENCE
    assert result.missing_facets == ("invoice_window",)
    assert result.covered_facets.count("payment_terms") == 1


# ---------------------------------------------------------------------------
# "evidence from an invalid/stale item does not count toward completeness"
# ---------------------------------------------------------------------------


def test_invalid_item_excluded_from_coverage(source_registry):
    package = _package(
        [
            _item("E-A", ["supplier_identity", "payment_terms"]),
            _item("E-B", ["invoice_window"]),
        ],
        REQUIRED,
    )

    # E-B is deliberately not in valid_evidence_ids -- simulating a stale/
    # invalid item that failed provenance/integrity/freshness.
    result = validate_completeness(package, valid_evidence_ids={"E-A"}, source_registry=source_registry)

    assert result.status is CompletenessStatus.INSUFFICIENT_EVIDENCE
    assert result.missing_facets == ("invoice_window",)


def test_valid_evidence_ids_naming_a_nonexistent_item_is_harmless(source_registry):
    """`valid_evidence_ids` is only ever a filter over `package.items` --
    an id it names that the package does not actually carry contributes
    nothing, it is not an error."""
    package = _package([_item("E-A", ["supplier_identity", "payment_terms", "invoice_window"])], REQUIRED)
    result = validate_completeness(package, valid_evidence_ids={"E-A", "E-GHOST"}, source_registry=source_registry)
    assert result.status is CompletenessStatus.COMPLETE


# ---------------------------------------------------------------------------
# "unsupported facet claim cannot be manufactured by the model"
# ---------------------------------------------------------------------------


def test_unsupported_facet_claim_is_not_credited(source_registry):
    """`SRC-REFERENCE-A` is governed for `invoice_window` only in the real
    committed registry -- an item from that source claiming
    `payment_terms` too must not have that claim counted, whatever its own
    `evidence_facets` list says."""
    package = _package(
        [_item("E-A", ["invoice_window", "payment_terms"], source_id="SRC-REFERENCE-A")],
        ["invoice_window", "payment_terms"],
    )

    result = validate_completeness(package, valid_evidence_ids={"E-A"}, source_registry=source_registry)

    assert result.status is CompletenessStatus.INSUFFICIENT_EVIDENCE
    assert result.missing_facets == ("payment_terms",)
    assert "invoice_window" in result.covered_facets


def test_source_registry_governs_which_claims_count_not_the_item_itself(source_registry):
    """The real committed `SRC-ARCHIVE-A` only supports `payment_terms` --
    proves the credit decision is driven by `source_registry.
    supports_facet()`, not merely "the item said so"."""
    assert source_registry.supports_facet("SRC-ARCHIVE-A", "payment_terms") is True
    assert source_registry.supports_facet("SRC-ARCHIVE-A", "supplier_identity") is False

    package = _package(
        [_item("E-A", ["payment_terms", "supplier_identity"], source_id="SRC-ARCHIVE-A")],
        ["payment_terms", "supplier_identity"],
    )
    result = validate_completeness(package, valid_evidence_ids={"E-A"}, source_registry=source_registry)

    assert result.missing_facets == ("supplier_identity",)


def test_unknown_source_contributes_nothing(source_registry):
    package = _package(
        [_item("E-A", ["supplier_identity"], source_id="SRC-DOES-NOT-EXIST")],
        ["supplier_identity"],
    )
    result = validate_completeness(package, valid_evidence_ids={"E-A"}, source_registry=source_registry)
    assert result.status is CompletenessStatus.INSUFFICIENT_EVIDENCE
    assert result.missing_facets == ("supplier_identity",)


# ---------------------------------------------------------------------------
# Purity / never mutates inputs
# ---------------------------------------------------------------------------


def test_validate_completeness_never_mutates_inputs(source_registry):
    package = _package([_item("E-A", ["supplier_identity"])], ["supplier_identity"])
    items_before = package.items

    validate_completeness(package, valid_evidence_ids={"E-A"}, source_registry=source_registry)

    assert package.items == items_before


def test_result_facets_are_immutable_tuples(source_registry):
    package = _package([_item("E-A", ["supplier_identity"])], ["supplier_identity"])
    result = validate_completeness(package, valid_evidence_ids={"E-A"}, source_registry=source_registry)
    assert isinstance(result.required_facets, tuple)
    assert isinstance(result.covered_facets, tuple)
    assert isinstance(result.missing_facets, tuple)


# ===========================================================================
# Real completeness_cases.json fixture -- COMP-001..004
# ===========================================================================


def _package_and_valid_ids_from_case(case: dict) -> tuple[EvidencePackage, set[str]]:
    """COMP-001/002/003 carry `validated_items` (already-validated, no
    `valid` flag needed); COMP-004 carries `items` with an explicit
    per-item `valid` flag -- both map onto `validate_completeness()`'s own
    `valid_evidence_ids` filter, just with the filtering already done for
    the first three and left explicit for the fourth."""
    raw_items = case.get("validated_items", case.get("items"))
    items = [_item(raw["evidence_id"], raw["facets"]) for raw in raw_items]
    valid_ids = {raw["evidence_id"] for raw in raw_items if raw.get("valid", True)}
    package = _package(items, case["required_facets"])
    return package, valid_ids


@pytest.mark.parametrize("case_id", ["COMP-001", "COMP-002", "COMP-003", "COMP-004"])
def test_real_completeness_case(source_registry, case_id):
    case = CASES_BY_ID[case_id]
    package, valid_ids = _package_and_valid_ids_from_case(case)

    result = validate_completeness(package, valid_evidence_ids=valid_ids, source_registry=source_registry)

    assert result.status.value == case["expected"]
    if "missing_facets" in case:
        assert set(result.missing_facets) == set(case["missing_facets"])


def test_comp004_invalid_item_excluded_end_to_end(source_registry):
    """COMP-004 by name: `E-B` (`valid: false`) claims `invoice_window`,
    the one facet that would otherwise complete coverage -- proves the
    exclusion actually changes the outcome, not just that the flag is
    read."""
    case = CASES_BY_ID["COMP-004"]
    package, valid_ids = _package_and_valid_ids_from_case(case)
    assert valid_ids == {"E-A"}

    result = validate_completeness(package, valid_evidence_ids=valid_ids, source_registry=source_registry)

    assert result.status is CompletenessStatus.INSUFFICIENT_EVIDENCE
    assert result.missing_facets == ("invoice_window",)
