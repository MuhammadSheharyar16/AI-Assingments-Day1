"""
Day 11 Task 8 -- conflict detection (`src/aico/evidence/conflicts.py`).

Two sections, matching the module's own two layers: the first proves
`evaluate_conflict()` (the pure, low-level core) against every Task 8
"Required behavior" bullet, including a fixture-driven parametrization
over the real `conflict_cases.json` (CONFLICT-001..003) -- those fixtures
are already shaped as this function's own `(facet, claims, policy)`
parameters, so no adaptation is needed. The second proves
`validate_conflicts()` end to end against the real committed
`SourceRegistry`, resolving authority from governed source data rather
than from the claiming item itself.

"The model must not be asked to 'pick whichever source looks better'" is
proven structurally: neither function's signature has anywhere a model
call, prompt, or generated preference could be threaded through.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aico.evidence.conflicts import (
    ConflictClaim,
    ConflictOutcome,
    ConflictReport,
    ConflictResolution,
    evaluate_conflict,
    validate_conflicts,
)
from aico.evidence.models import EvidencePackage
from aico.evidence.policy import ConflictPolicy
from aico.evidence.source_registry import SourceRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFLICT_CASES_PATH = REPO_ROOT / "data" / "day11_pack" / "fixtures" / "conflict_cases.json"
CONFLICT_CASES = json.loads(CONFLICT_CASES_PATH.read_text(encoding="utf-8"))["cases"]
CASES_BY_ID = {case["id"]: case for case in CONFLICT_CASES}

POLICY = ConflictPolicy.AUTHORITY_THEN_REJECT_TIE


def _claims(raw_items: list[dict]) -> list[ConflictClaim]:
    return [ConflictClaim(evidence_id=i["evidence_id"], authority=i["authority"], value=i["value"]) for i in raw_items]


# ---------------------------------------------------------------------------
# evaluate_conflict() -- Required behavior
# ---------------------------------------------------------------------------


def test_non_conflicting_evidence_passes():
    claims = _claims([{"evidence_id": "E-1", "authority": 90, "value": "net 30"}])
    result = evaluate_conflict("payment_terms", claims, policy=POLICY)
    assert isinstance(result, ConflictResolution)
    assert result.outcome is ConflictOutcome.NO_CONFLICT
    assert result.winning_value == "net 30"
    assert result.conflicting_evidence_ids == ()


def test_exact_duplicate_supporting_evidence_is_not_a_conflict():
    claims = _claims(
        [
            {"evidence_id": "E-1", "authority": 90, "value": "net 30"},
            {"evidence_id": "E-2", "authority": 90, "value": "net 30"},
        ]
    )
    result = evaluate_conflict("payment_terms", claims, policy=POLICY)
    assert result.outcome is ConflictOutcome.NO_CONFLICT
    assert result.winning_value == "net 30"


def test_empty_claims_is_no_conflict_with_no_winner():
    result = evaluate_conflict("payment_terms", [], policy=POLICY)
    assert result.outcome is ConflictOutcome.NO_CONFLICT
    assert result.winning_value is None
    assert result.conflicting_evidence_ids == ()


def test_contradictory_same_authority_evidence_is_detected():
    claims = _claims(
        [
            {"evidence_id": "E-1", "authority": 90, "value": "net 30"},
            {"evidence_id": "E-2", "authority": 90, "value": "net 45"},
        ]
    )
    result = evaluate_conflict("payment_terms", claims, policy=POLICY)
    assert result.outcome is ConflictOutcome.UNRESOLVED_CONFLICT
    assert result.winning_value is None
    assert set(result.conflicting_evidence_ids) == {"E-1", "E-2"}


def test_governed_authority_precedence_resolves_a_conflict():
    claims = _claims(
        [
            {"evidence_id": "E-contract", "authority": 100, "value": "net 30"},
            {"evidence_id": "E-policy", "authority": 90, "value": "net 45"},
        ]
    )
    result = evaluate_conflict("payment_terms", claims, policy=POLICY)
    assert result.outcome is ConflictOutcome.RESOLVED_BY_GOVERNED_AUTHORITY
    assert result.winning_value == "net 30"
    assert set(result.conflicting_evidence_ids) == {"E-contract", "E-policy"}


def test_three_way_tie_at_top_authority_is_unresolved():
    claims = _claims(
        [
            {"evidence_id": "E-1", "authority": 90, "value": "net 30"},
            {"evidence_id": "E-2", "authority": 90, "value": "net 45"},
            {"evidence_id": "E-3", "authority": 50, "value": "net 60"},
        ]
    )
    result = evaluate_conflict("payment_terms", claims, policy=POLICY)
    assert result.outcome is ConflictOutcome.UNRESOLVED_CONFLICT


def test_lower_authority_claim_does_not_break_a_clear_winner():
    """A third, lower-authority claim joining a genuine two-way
    disagreement does not change who wins -- only the single highest
    authority matters."""
    claims = _claims(
        [
            {"evidence_id": "E-contract", "authority": 100, "value": "net 30"},
            {"evidence_id": "E-policy", "authority": 90, "value": "net 45"},
            {"evidence_id": "E-archive", "authority": 50, "value": "net 60"},
        ]
    )
    result = evaluate_conflict("payment_terms", claims, policy=POLICY)
    assert result.outcome is ConflictOutcome.RESOLVED_BY_GOVERNED_AUTHORITY
    assert result.winning_value == "net 30"


# ---------------------------------------------------------------------------
# "configured authority precedence may resolve a conflict only when
# explicitly governed"
# ---------------------------------------------------------------------------


def test_authority_precedence_not_applied_for_an_unrecognized_policy():
    """A `policy` value `evaluate_conflict()` does not recognize as an
    authority-resolving strategy must never silently fall back to
    resolving by authority anyway -- resolution is only attempted for a
    policy this function explicitly matches."""
    claims = _claims(
        [
            {"evidence_id": "E-contract", "authority": 100, "value": "net 30"},
            {"evidence_id": "E-policy", "authority": 90, "value": "net 45"},
        ]
    )
    unrecognized_policy = object()  # stands in for a hypothetical future ConflictPolicy value

    result = evaluate_conflict("payment_terms", claims, policy=unrecognized_policy)

    assert result.outcome is ConflictOutcome.UNRESOLVED_CONFLICT
    assert result.winning_value is None


# ---------------------------------------------------------------------------
# The model must not be asked to "pick whichever source looks better"
# ---------------------------------------------------------------------------


def test_evaluate_conflict_signature_has_no_model_parameter():
    import inspect

    params = set(inspect.signature(evaluate_conflict).parameters)
    assert params == {"facet", "claims", "policy"}


def test_validate_conflicts_signature_has_no_model_parameter():
    import inspect

    params = set(inspect.signature(validate_conflicts).parameters)
    assert params == {"package", "valid_evidence_ids", "source_registry", "conflict_policy"}


def test_evaluate_conflict_never_mutates_claims():
    claims = _claims(
        [
            {"evidence_id": "E-1", "authority": 90, "value": "net 30"},
            {"evidence_id": "E-2", "authority": 90, "value": "net 45"},
        ]
    )
    claims_before = list(claims)
    evaluate_conflict("payment_terms", claims, policy=POLICY)
    assert claims == claims_before


# ---------------------------------------------------------------------------
# gate_c_cases.json -- real conflict_cases.json, fed directly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", ["CONFLICT-001", "CONFLICT-002", "CONFLICT-003"])
def test_real_conflict_case(case_id):
    case = CASES_BY_ID[case_id]
    claims = _claims(case["items"])

    result = evaluate_conflict(case["facet"], claims, policy=POLICY)

    assert result.outcome.value == case["expected"]
    if "winning_value" in case:
        assert result.winning_value == case["winning_value"]


# ===========================================================================
# validate_conflicts() -- package-level, against the real committed
# SourceRegistry
# ===========================================================================


@pytest.fixture(scope="module")
def source_registry() -> SourceRegistry:
    return SourceRegistry.load()


def _item(evidence_id: str, *, source_id: str = "SRC-POLICY-A", claims: dict[str, str] | None = None, **overrides) -> dict:
    data = {
        "evidence_id": evidence_id,
        "chunk_id": f"CH-{evidence_id}",
        "source_id": source_id,
        "source_version": "3",
        "source_updated_at": "2026-09-05T09:00:00+05:00",
        "retrieved_at": "2026-09-11T11:59:00+05:00",
        "content_hash": "irrelevant-for-conflicts",
        "tenant_id": "TENANT-A",
        "data_classification": "internal",
        "evidence_facets": list((claims or {}).keys()),
        "content": f"Content for {evidence_id}.",
        "claims": claims or {},
    }
    data.update(overrides)
    return data


def _package(items: list[dict], **overrides) -> EvidencePackage:
    data = {
        "request_id": "REQ-001",
        "intent_id": "INT-POLICY-QUESTION",
        "lane": "rag",
        "as_of": "2026-09-11T12:00:00+05:00",
        "required_facets": [],
        "items": items,
    }
    data.update(overrides)
    return EvidencePackage.model_validate(data)


def test_validate_conflicts_returns_typed_report(source_registry):
    package = _package([_item("E-A", claims={"payment_terms": "net 30"})])
    report = validate_conflicts(
        package, valid_evidence_ids={"E-A"}, source_registry=source_registry, conflict_policy=POLICY
    )
    assert isinstance(report, ConflictReport)


def test_agreeing_items_from_different_sources_pass(source_registry):
    package = _package(
        [
            _item("E-A", source_id="SRC-POLICY-A", claims={"payment_terms": "net 30"}),
            _item("E-B", source_id="SRC-CONTRACT-A", claims={"payment_terms": "net 30"}),
        ]
    )
    report = validate_conflicts(
        package, valid_evidence_ids={"E-A", "E-B"}, source_registry=source_registry, conflict_policy=POLICY
    )
    assert report.has_unresolved_conflict is False
    assert report.resolution_for("payment_terms").outcome is ConflictOutcome.NO_CONFLICT


def test_higher_authority_source_wins_a_real_conflict(source_registry):
    """`SRC-CONTRACT-A` (authority 100) outranks `SRC-POLICY-A` (authority
    90) in the real committed registry."""
    assert source_registry.get_source("SRC-CONTRACT-A").authority_level == 100
    assert source_registry.get_source("SRC-POLICY-A").authority_level == 90

    package = _package(
        [
            _item("E-contract", source_id="SRC-CONTRACT-A", claims={"payment_terms": "net 30"}),
            _item("E-policy", source_id="SRC-POLICY-A", claims={"payment_terms": "net 45"}),
        ]
    )
    report = validate_conflicts(
        package, valid_evidence_ids={"E-contract", "E-policy"}, source_registry=source_registry, conflict_policy=POLICY
    )

    resolution = report.resolution_for("payment_terms")
    assert resolution.outcome is ConflictOutcome.RESOLVED_BY_GOVERNED_AUTHORITY
    assert resolution.winning_value == "net 30"
    assert report.has_unresolved_conflict is False


def test_same_source_two_items_with_contradicting_claims_is_unresolved(source_registry):
    """Two different evidence items from the identical governed source
    necessarily share that source's authority -- a real, reachable "tied
    at the top" scenario without needing two distinct sources with equal
    `authority_level` (none exist in the real committed registry)."""
    package = _package(
        [
            _item("E-A", source_id="SRC-POLICY-A", claims={"payment_terms": "net 30"}),
            _item("E-B", source_id="SRC-POLICY-A", claims={"payment_terms": "net 45"}),
        ]
    )
    report = validate_conflicts(
        package, valid_evidence_ids={"E-A", "E-B"}, source_registry=source_registry, conflict_policy=POLICY
    )

    resolution = report.resolution_for("payment_terms")
    assert resolution.outcome is ConflictOutcome.UNRESOLVED_CONFLICT
    assert report.has_unresolved_conflict is True
    assert report.unresolved_facets == ("payment_terms",)


def test_invalid_item_excluded_from_conflict_consideration(source_registry):
    """The item that would otherwise create a disagreement is excluded via
    `valid_evidence_ids` (simulating a stale/invalid item Task 4/5/6
    already rejected) -- its claim is never even considered."""
    package = _package(
        [
            _item("E-A", source_id="SRC-POLICY-A", claims={"payment_terms": "net 30"}),
            _item("E-B", source_id="SRC-POLICY-A", claims={"payment_terms": "net 45"}),
        ]
    )
    report = validate_conflicts(
        package, valid_evidence_ids={"E-A"}, source_registry=source_registry, conflict_policy=POLICY
    )

    resolution = report.resolution_for("payment_terms")
    assert resolution.outcome is ConflictOutcome.NO_CONFLICT
    assert resolution.winning_value == "net 30"


def test_unknown_source_excluded_from_conflict_consideration(source_registry):
    package = _package(
        [
            _item("E-A", source_id="SRC-POLICY-A", claims={"payment_terms": "net 30"}),
            _item("E-B", source_id="SRC-DOES-NOT-EXIST", claims={"payment_terms": "net 45"}),
        ]
    )
    report = validate_conflicts(
        package, valid_evidence_ids={"E-A", "E-B"}, source_registry=source_registry, conflict_policy=POLICY
    )
    resolution = report.resolution_for("payment_terms")
    assert resolution.outcome is ConflictOutcome.NO_CONFLICT


def test_facet_with_no_claims_has_no_resolution(source_registry):
    package = _package([_item("E-A", claims={"payment_terms": "net 30"})])
    report = validate_conflicts(
        package, valid_evidence_ids={"E-A"}, source_registry=source_registry, conflict_policy=POLICY
    )
    with pytest.raises(KeyError):
        report.resolution_for("invoice_window")


def test_multiple_facets_aggregate_independently(source_registry):
    """One facet resolves cleanly, a different facet on the same items is
    an unresolved tie -- `unresolved_facets`/`has_unresolved_conflict`
    reflect only the genuinely conflicting facet."""
    package = _package(
        [
            _item("E-A", source_id="SRC-CONTRACT-A", claims={"payment_terms": "net 30", "contract_status": "active"}),
            _item("E-B", source_id="SRC-POLICY-A", claims={"payment_terms": "net 30", "contract_status": "expired"}),
        ]
    )
    report = validate_conflicts(
        package, valid_evidence_ids={"E-A", "E-B"}, source_registry=source_registry, conflict_policy=POLICY
    )

    assert report.resolution_for("payment_terms").outcome is ConflictOutcome.NO_CONFLICT
    assert report.resolution_for("contract_status").outcome is ConflictOutcome.RESOLVED_BY_GOVERNED_AUTHORITY
    assert report.has_unresolved_conflict is False


def test_validate_conflicts_never_mutates_inputs(source_registry):
    package = _package([_item("E-A", claims={"payment_terms": "net 30"})])
    items_before = package.items

    validate_conflicts(package, valid_evidence_ids={"E-A"}, source_registry=source_registry, conflict_policy=POLICY)

    assert package.items == items_before


def test_item_with_no_claims_contributes_nothing(source_registry):
    package = _package([_item("E-A", claims={})])
    report = validate_conflicts(
        package, valid_evidence_ids={"E-A"}, source_registry=source_registry, conflict_policy=POLICY
    )
    assert report.resolutions == ()
    assert report.has_unresolved_conflict is False
