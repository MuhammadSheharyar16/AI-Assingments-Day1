"""
Day 11 Task 6 -- freshness policy (`src/aico/evidence/freshness.py`).

Two sections, matching the module's own two layers: the first proves
`evaluate_freshness()` (the pure, low-level core) against every Task 6
"Required cases" bullet, including a fixture-driven parametrization over
the real `gate_c_cases.json` `freshness_cases` (FRESH-001..004) -- those
fixtures are shaped as raw `(as_of, source_updated_at, max_age_hours)`
triples, exactly `evaluate_freshness()`'s own signature, so no adaptation
is needed the way Task 4/5's hash fixtures required. The second proves
`validate_freshness()` end to end against the real committed
`SourceRegistry`/`GateCPolicyRegistry`, including "source whose policy has
a different max age" (two items from `SRC-POLICY-A`/`SRC-CONTRACT-A`,
each with its own 30-day/7-day threshold, evaluated independently in one
package) and the defensive lookup-failure branches a real, cross-validated
pair of registries cannot otherwise reach.

Working rule proven structurally throughout: every `as_of` is an explicit,
fixed value this file supplies -- no test, and no line in `freshness.py`
itself, ever calls `datetime.now()`/`datetime.utcnow()`.
"""
from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from aico.control.ontology_registry import OntologyRegistry
from aico.evidence import freshness as freshness_module
from aico.evidence.freshness import (
    FreshnessFailureReason,
    FreshnessReport,
    FreshnessStatus,
    evaluate_freshness,
    validate_freshness,
)
from aico.evidence.models import EvidenceItem, EvidencePackage
from aico.evidence.policy import GateCPolicyDocument, GateCPolicyRegistry
from aico.evidence.source_registry import SourceRegistry, SourceRegistryDocument

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE_C_CASES_PATH = REPO_ROOT / "data" / "day11_pack" / "fixtures" / "gate_c_cases.json"

GATE_C_CASES = json.loads(GATE_C_CASES_PATH.read_text(encoding="utf-8"))
FRESHNESS_CASES = GATE_C_CASES["freshness_cases"]

# "fresh_at_threshold" is still a FRESH outcome (see freshness.py's
# docstring: "the rule's own '<=' wording settles which side of the
# boundary it falls on") -- this maps the fixture's scenario labels onto
# the two real FreshnessStatus outcomes those cases exercise.
_EXPECTED_STATUS_BY_FIXTURE_LABEL = {
    "fresh": FreshnessStatus.FRESH,
    "fresh_at_threshold": FreshnessStatus.FRESH,
    "stale": FreshnessStatus.STALE,
}


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


# ---------------------------------------------------------------------------
# evaluate_freshness() -- Required cases
# ---------------------------------------------------------------------------


def test_fresh_evidence():
    as_of = _dt("2026-09-11T12:00:00+05:00")
    source_updated_at = _dt("2026-09-01T12:00:00+05:00")
    assert evaluate_freshness(as_of=as_of, source_updated_at=source_updated_at, max_age_hours=720) is FreshnessStatus.FRESH


def test_exactly_at_threshold_evidence_is_fresh():
    """`age == max_age` is still `age <= max_age` -- the rule's own
    wording, not a special case this function adds."""
    as_of = _dt("2026-09-11T12:00:00+05:00")
    source_updated_at = as_of - timedelta(hours=720)
    assert evaluate_freshness(as_of=as_of, source_updated_at=source_updated_at, max_age_hours=720) is FreshnessStatus.FRESH


def test_stale_evidence():
    as_of = _dt("2026-09-11T12:00:00+05:00")
    source_updated_at = as_of - timedelta(hours=720, seconds=1)
    assert evaluate_freshness(as_of=as_of, source_updated_at=source_updated_at, max_age_hours=720) is FreshnessStatus.STALE


def test_missing_freshness_timestamp():
    as_of = _dt("2026-09-11T12:00:00+05:00")
    assert (
        evaluate_freshness(as_of=as_of, source_updated_at=None, max_age_hours=720) is FreshnessStatus.MISSING_TIMESTAMP
    )


def test_future_source_timestamp_is_invalid():
    """A source claiming to have been updated after the request's own
    reference time is not a valid age claim at all -- fail closed rather
    than compute (and possibly misuse) a negative age."""
    as_of = _dt("2026-09-11T12:00:00+05:00")
    future = as_of + timedelta(hours=1)
    assert evaluate_freshness(as_of=as_of, source_updated_at=future, max_age_hours=720) is FreshnessStatus.INVALID_TIMESTAMP


def test_source_updated_at_equal_to_as_of_is_not_invalid():
    """Exactly equal to `as_of` (age zero) is legitimate -- only strictly
    *after* `as_of` is invalid."""
    as_of = _dt("2026-09-11T12:00:00+05:00")
    assert evaluate_freshness(as_of=as_of, source_updated_at=as_of, max_age_hours=720) is FreshnessStatus.FRESH


def test_evaluate_freshness_never_calls_wall_clock():
    """AST-based, not a docstring substring search (this module's own
    docstrings quote the working rule by name) -- walks each function's
    actual call nodes for a `.now(`/`.utcnow()` attribute call."""
    import ast

    for func in (freshness_module.evaluate_freshness, freshness_module.validate_freshness, freshness_module._evaluate_item_freshness):
        tree = ast.parse(inspect.getsource(func))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in ("now", "utcnow"), f"{func.__name__} calls wall-clock {node.func.attr}()"


@pytest.mark.parametrize("case", FRESHNESS_CASES, ids=[c["id"] for c in FRESHNESS_CASES])
def test_real_freshness_cases_fixture(case):
    """`gate_c_cases.json`'s `freshness_cases` fed directly into
    `evaluate_freshness()` -- these fixtures are already shaped as this
    function's own three parameters, so nothing needs adapting."""
    status = evaluate_freshness(
        as_of=_dt(case["as_of"]),
        source_updated_at=_dt(case["source_updated_at"]),
        max_age_hours=case["max_age_hours"],
    )
    assert status is _EXPECTED_STATUS_BY_FIXTURE_LABEL[case["expected"]]


# ---------------------------------------------------------------------------
# Rule: retrieval score does not override staleness
# ---------------------------------------------------------------------------


def test_evaluate_freshness_signature_has_no_rank_or_score_parameter():
    params = set(inspect.signature(evaluate_freshness).parameters)
    assert params == {"as_of", "source_updated_at", "max_age_hours"}


def test_validate_freshness_signature_has_no_rank_or_score_parameter():
    params = set(inspect.signature(validate_freshness).parameters)
    assert params == {"package", "source_registry", "policy_registry"}


# ===========================================================================
# validate_freshness() -- package-level, against real committed registries
# ===========================================================================


def _committed_source_registry() -> SourceRegistry:
    ontology_registry = OntologyRegistry.load()
    return SourceRegistry.load(ontology_registry=ontology_registry)


def _committed_policy_registry() -> GateCPolicyRegistry:
    ontology_registry = OntologyRegistry.load()
    source_registry = SourceRegistry.load(ontology_registry=ontology_registry)
    return GateCPolicyRegistry.load(ontology_registry=ontology_registry, source_registry=source_registry)


@pytest.fixture(scope="module")
def source_registry() -> SourceRegistry:
    return _committed_source_registry()


@pytest.fixture(scope="module")
def policy_registry() -> GateCPolicyRegistry:
    return _committed_policy_registry()


def _item(**overrides) -> dict:
    content = "Synthetic Supplier Alpha uses net 30 payment terms."
    data = {
        "evidence_id": "E-001",
        "chunk_id": "CH-001",
        "source_id": "SRC-POLICY-A",
        "source_version": "3",
        "source_updated_at": "2026-09-01T12:00:00+05:00",
        "retrieved_at": "2026-09-11T11:59:00+05:00",
        "content_hash": "irrelevant-for-freshness",
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


def test_valid_freshness_report_type(source_registry, policy_registry):
    report = validate_freshness(_package([_item()]), source_registry=source_registry, policy_registry=policy_registry)
    assert isinstance(report, FreshnessReport)


def test_fresh_item_against_real_policy_thirty_day_source(source_registry, policy_registry):
    """`SRC-POLICY-A` is governed by `FRESH-POLICY-30D` (720h) in the real
    committed data -- FRESH-001's own scenario, at the package level."""
    package = _package([_item(source_updated_at="2026-09-01T12:00:00+05:00")])

    report = validate_freshness(package, source_registry=source_registry, policy_registry=policy_registry)

    result = report.result_for("E-001")
    assert result.valid is True
    assert result.status is FreshnessStatus.FRESH
    assert result.max_age_hours == 720
    assert result.age_hours == pytest.approx(240.0)  # 10 days


def test_stale_item_against_real_policy_seven_day_source(source_registry, policy_registry):
    """`SRC-CONTRACT-A` is governed by `FRESH-CONTRACT-7D` (168h) --
    FRESH-004's own scenario, proving each source's *own* threshold is
    used, not a shared/hardcoded one."""
    package = _package(
        [
            _item(
                evidence_id="E-004",
                chunk_id="CH-004",
                source_id="SRC-CONTRACT-A",
                source_updated_at="2026-09-04T11:59:59+05:00",
            )
        ]
    )

    report = validate_freshness(package, source_registry=source_registry, policy_registry=policy_registry)

    result = report.result_for("E-004")
    assert result.valid is False
    assert result.status is FreshnessStatus.STALE
    assert result.max_age_hours == 168
    assert FreshnessFailureReason.STALE in result.reasons


def test_two_items_from_different_sources_each_use_their_own_policy(source_registry, policy_registry):
    """"source whose policy has a different max age" (Task 6's own
    bullet), proven within a single package: the 30-day-fresh item and the
    7-day-stale item are evaluated side by side and reach different, each
    individually-correct outcomes."""
    package = _package(
        [
            _item(evidence_id="E-001", chunk_id="CH-001", source_id="SRC-POLICY-A", source_updated_at="2026-09-01T12:00:00+05:00"),
            _item(
                evidence_id="E-004",
                chunk_id="CH-004",
                source_id="SRC-CONTRACT-A",
                source_updated_at="2026-09-04T11:59:59+05:00",
            ),
        ]
    )

    report = validate_freshness(package, source_registry=source_registry, policy_registry=policy_registry)

    assert report.result_for("E-001").valid is True
    assert report.result_for("E-004").valid is False
    assert report.validated_evidence_ids == ("E-001",)
    assert report.rejected_evidence_ids == ("E-004",)


def test_unknown_source_fails_closed(source_registry, policy_registry):
    package = _package([_item(source_id="SRC-DOES-NOT-EXIST")])

    report = validate_freshness(package, source_registry=source_registry, policy_registry=policy_registry)

    result = report.result_for("E-001")
    assert result.valid is False
    assert result.status is None
    assert FreshnessFailureReason.UNKNOWN_SOURCE in result.reasons


def test_stale_top_ranked_item_is_still_stale(source_registry, policy_registry):
    """Structural proof of "retrieval score does not override staleness":
    a stale item placed *first* (as a real retrieval ranking would place a
    top hit) is evaluated identically to one placed last -- position in
    `package.items` has no bearing on the outcome."""
    stale_item = _item(
        evidence_id="E-004", chunk_id="CH-004", source_id="SRC-CONTRACT-A", source_updated_at="2026-09-04T11:59:59+05:00"
    )
    fresh_item = _item(evidence_id="E-001", chunk_id="CH-001", source_id="SRC-POLICY-A")
    package = _package([stale_item, fresh_item])  # stale item ranked first

    report = validate_freshness(package, source_registry=source_registry, policy_registry=policy_registry)

    assert report.result_for("E-004").valid is False
    assert report.result_for("E-001").valid is True


def test_missing_timestamp_unreachable_through_a_validated_item(source_registry, policy_registry):
    """Task 1's envelope already makes `source_updated_at` required and
    non-blank -- `validate_freshness()` never observes
    `FreshnessStatus.MISSING_TIMESTAMP` for a properly constructed
    `EvidenceItem`. `evaluate_freshness()` alone (tested above) is where
    that branch is actually exercised."""
    with pytest.raises(ValidationError):
        EvidenceItem.model_validate({**_item(), "source_updated_at": None})


def test_result_for_unknown_evidence_id_raises_key_error(source_registry, policy_registry):
    report = validate_freshness(_package([_item()]), source_registry=source_registry, policy_registry=policy_registry)
    with pytest.raises(KeyError):
        report.result_for("E-DOES-NOT-EXIST")


def test_validate_freshness_never_mutates_inputs(source_registry, policy_registry):
    package = _package([_item()])
    items_before = package.items

    validate_freshness(package, source_registry=source_registry, policy_registry=policy_registry)

    assert package.items == items_before


# ---------------------------------------------------------------------------
# Defensive branch: an unresolved governed freshness policy (unreachable
# through the real, cross-validated committed registries -- built by hand)
# ---------------------------------------------------------------------------


def test_unresolved_freshness_policy_fails_closed():
    """A `SourceRecord` whose own `freshness_policy_id` the paired policy
    registry does not declare -- impossible through `GateCPolicyRegistry.
    load()`'s own cross-reference check against the real committed data
    (Task 3), but `validate_freshness()` still fails closed rather than
    assuming the lookup always succeeds, the same defensive posture
    `provenance.py`'s `GovernedProvenanceIndex.get()` returning `None`
    already takes."""
    known_intent_ids = {"INT-POLICY-QUESTION"}
    source_registry = SourceRegistry(
        SourceRegistryDocument.model_validate(
            {
                "registry_version": "1.0",
                "sources": [
                    {
                        "source_id": "SRC-ORPHAN",
                        "source_type": "policy_document",
                        "authority_level": 50,
                        "owner": "team",
                        "status": "active",
                        "allowed_intents": ["INT-POLICY-QUESTION"],
                        "supported_facets": ["payment_terms"],
                        "freshness_policy_id": "FRESH-DOES-NOT-EXIST",
                    }
                ],
            },
            context={"known_intent_ids": known_intent_ids},
        )
    )
    policy_registry = GateCPolicyRegistry(
        GateCPolicyDocument.model_validate(
            {
                "policy_version": "1.0",
                "status": "active",
                "freshness_policies": [{"policy_id": "FRESH-SOMETHING-ELSE", "max_age_hours": 24}],
                "intent_requirements": [],
            }
        )
    )
    package = _package([_item(source_id="SRC-ORPHAN")])

    report = validate_freshness(package, source_registry=source_registry, policy_registry=policy_registry)

    result = report.result_for("E-001")
    assert result.valid is False
    assert FreshnessFailureReason.UNKNOWN_FRESHNESS_POLICY in result.reasons
