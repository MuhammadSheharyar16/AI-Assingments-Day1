"""
Day 11 Task 9 -- the Gate-C decision contract (`src/aico/control/gate_c.py`).
Day 11 Task 10 -- filtering behavior (proven here too; see `gate_c.py`'s
own docstring for why the two are one algorithm, not two modules).

Structure: the first section proves all four Task 9 decision outcomes
individually, against real committed `SourceRegistry`/`GateCPolicyRegistry`
data throughout; the second proves Task 10's own filtering examples
("5 retrieved, 2 invalid, 3 valid, required facets covered -> allow" /
"... facet still missing -> insufficient_evidence") using the identical
real data; the third replays all five real `gate_c_cases.json` cases
end-to-end, reproducing each one's own `expected_decision`.

Task 11 (no-generation-fall-through, with an instrumented fake Model
Gateway) and Task 16's separately named `test_day11_no_fallthrough.py` are
out of scope here -- this file proves the decision contract itself, not
what a caller does with it afterward.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from aico.control.gate_c import GateC, GateCDecision, GateCRequest, GateCStatus
from aico.control.models import GateBDecision, GateBStatus
from aico.control.ontology import LaneId
from aico.control.policy_models import DataClassification
from aico.evidence.models import EvidencePackage
from aico.evidence.policy import GateCPolicyDocument, GateCPolicyRegistry
from aico.evidence.provenance import stable_content_hash
from aico.evidence.source_registry import SourceRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE_C_CASES_PATH = REPO_ROOT / "data" / "day11_pack" / "fixtures" / "gate_c_cases.json"
GATE_C_CASES = json.loads(GATE_C_CASES_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def source_registry() -> SourceRegistry:
    return SourceRegistry.load()


@pytest.fixture(scope="module")
def policy_registry(source_registry) -> GateCPolicyRegistry:
    return GateCPolicyRegistry.load(source_registry=source_registry)


@pytest.fixture()
def gate_c(source_registry, policy_registry) -> GateC:
    return GateC(source_registry=source_registry, policy_registry=policy_registry)


def _allow_decision(*, tenant_ids=("TENANT-A",), data_classes=(DataClassification.INTERNAL,)) -> GateBDecision:
    return GateBDecision(
        decision=GateBStatus.ALLOW,
        effective_tenant_scope=tenant_ids,
        effective_data_classes=data_classes,
        reason_code="test_fixture",
        policy_version="1.0",
        lane=LaneId.RAG,
    )


def _item(evidence_id: str, *, source_id: str, facets: list[str], claims: dict[str, str] | None = None, **overrides) -> dict:
    content = overrides.pop("content", f"Real content for {evidence_id}.")
    data = {
        "evidence_id": evidence_id,
        "chunk_id": f"CH-{evidence_id}",
        "source_id": source_id,
        "source_version": "3",
        "source_updated_at": "2026-09-05T09:00:00+05:00",
        "retrieved_at": "2026-09-11T11:59:00+05:00",
        "content_hash": stable_content_hash(content),
        "tenant_id": "TENANT-A",
        "data_classification": "internal",
        "evidence_facets": facets,
        "content": content,
        "claims": claims or {},
    }
    data.update(overrides)
    return data


def _package(items: list[dict], *, intent_id: str = "INT-POLICY-QUESTION", **overrides) -> EvidencePackage:
    data = {
        "request_id": "REQ-001",
        "intent_id": intent_id,
        "lane": "rag",
        "as_of": "2026-09-11T12:00:00+05:00",
        "required_facets": [],
        "items": items,
    }
    data.update(overrides)
    return EvidencePackage.model_validate(data)


# ===========================================================================
# Task 9 -- the four decision outcomes
# ===========================================================================


def test_allow_decision(gate_c):
    """`payment_and_invoice` (GC-R002) needs supplier_identity/
    payment_terms/invoice_window -- two real governed sources together
    cover all three."""
    package = _package(
        [
            _item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"]),
            _item("E-B", source_id="SRC-REFERENCE-A", facets=["invoice_window"]),
        ]
    )
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(),
        package=package,
        request=GateCRequest(request_kind="payment_and_invoice"),
    )

    assert isinstance(decision, GateCDecision)
    assert decision.decision is GateCStatus.ALLOW
    assert set(decision.validated_evidence_ids) == {"E-A", "E-B"}
    assert decision.rejected_evidence_ids == ()
    assert decision.missing_facets == ()
    assert decision.conflict_facets == ()


def test_insufficient_evidence_missing_required_facet(gate_c):
    package = _package([_item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"])])
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(),
        package=package,
        request=GateCRequest(request_kind="payment_and_invoice"),
    )

    assert decision.decision is GateCStatus.INSUFFICIENT_EVIDENCE
    assert decision.validated_evidence_ids == ()
    assert decision.missing_facets == ("invoice_window",)
    assert "missing_required_facets" in decision.reason_codes


def test_insufficient_evidence_empty_candidate_package(gate_c):
    """Zero candidate items at all -- `minimum_valid_items` (>= 1, Task
    3's own field constraint) is never met."""
    package = _package([])
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(),
        package=package,
        request=GateCRequest(request_kind="payment_terms_only"),
    )
    assert decision.decision is GateCStatus.INSUFFICIENT_EVIDENCE


def test_clarify_when_request_kind_not_resolved(gate_c):
    package = _package([_item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"])])
    decision = gate_c.evaluate(gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind=None))

    assert decision.decision is GateCStatus.CLARIFY
    assert decision.validated_evidence_ids == ()
    assert decision.rejected_evidence_ids == ()
    assert "request_kind_not_resolved" in decision.reason_codes


def test_clarify_is_also_the_default_request(gate_c):
    """`request=None` defaults to `GateCRequest()`, whose own
    `request_kind` defaults to `None` -- the same `clarify` outcome, not a
    crash from a missing argument."""
    package = _package([_item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"])])
    decision = gate_c.evaluate(gate_b_decision=_allow_decision(), package=package)
    assert decision.decision is GateCStatus.CLARIFY


def test_reject_when_gate_b_did_not_allow(gate_c):
    """Gate-C never widens Gate-B's own decision -- a `DENY` rejects
    regardless of how good the evidence itself looks."""
    package = _package([_item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"])])
    deny_decision = GateBDecision(decision=GateBStatus.DENY, reason_code="test_fixture", policy_version="1.0")

    decision = gate_c.evaluate(
        gate_b_decision=deny_decision, package=package, request=GateCRequest(request_kind="payment_terms_only")
    )

    assert decision.decision is GateCStatus.REJECT
    assert decision.validated_evidence_ids == ()
    assert "gate_b_not_allowed" in decision.reason_codes


def test_reject_when_no_governed_rule_for_request_kind(gate_c):
    package = _package([_item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"])])
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="totally_unrelated_kind")
    )
    assert decision.decision is GateCStatus.REJECT
    assert "no_governed_evidence_requirement" in decision.reason_codes


def test_reject_unknown_source(gate_c):
    package = _package([_item("E-A", source_id="SRC-DOES-NOT-EXIST", facets=["supplier_identity", "payment_terms"])])
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only")
    )
    assert decision.decision is GateCStatus.REJECT
    assert "unknown_source" in decision.reason_codes
    assert decision.rejected_evidence_ids == ("E-A",)


def test_reject_disabled_source(gate_c):
    """`SRC-ARCHIVE-A` is `status: disabled` in the real committed
    registry -- Task 2's own concern, only reachable end to end through
    Gate-C's own registry+policy gating (`_registry_policy_reasons`)."""
    package = _package([_item("E-A", source_id="SRC-ARCHIVE-A", facets=["payment_terms"])])
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only")
    )
    assert decision.decision is GateCStatus.REJECT
    assert "disabled_source" in decision.reason_codes


def test_reject_source_type_not_allowed_for_this_rule(gate_c):
    """`SRC-REFERENCE-A` (`source_type: reference_record`) is a real,
    active, intent-compatible source -- just not one `payment_terms_only`
    (GC-R001, `allowed_source_types: [policy_document, contract_record]`)
    governs."""
    package = _package([_item("E-A", source_id="SRC-REFERENCE-A", facets=["invoice_window"])])
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only")
    )
    assert decision.decision is GateCStatus.REJECT
    assert "source_type_not_allowed" in decision.reason_codes


def test_reject_source_intent_not_supported(gate_c):
    """`SRC-REFERENCE-A`'s `allowed_intents` is only `INT-POLICY-QUESTION`
    in the real committed registry -- it may not back
    `INT-STRUCTURED-LOOKUP` evidence at all, independent of source type."""
    # SRC-CONTRACT-A does support INT-STRUCTURED-LOOKUP -- SRC-REFERENCE-A
    # (governed for INT-POLICY-QUESTION only) is used here instead, to
    # isolate this specific check.
    package = _package(
        [_item("E-A", source_id="SRC-REFERENCE-A", facets=["supplier_identity"])],
        intent_id="INT-STRUCTURED-LOOKUP",
    )
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="contract_status")
    )
    assert decision.decision is GateCStatus.REJECT
    assert "source_intent_not_allowed" in decision.reason_codes


def test_reject_content_hash_mismatch(gate_c):
    package = _package(
        [_item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"], content_hash="not-a-real-hash")]
    )
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only")
    )
    assert decision.decision is GateCStatus.REJECT
    assert "content_hash_mismatch" in decision.reason_codes


def test_reject_cross_tenant_evidence(gate_c):
    package = _package(
        [_item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"], tenant_id="TENANT-B")]
    )
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(tenant_ids=("TENANT-A",)),
        package=package,
        request=GateCRequest(request_kind="payment_terms_only"),
    )
    assert decision.decision is GateCStatus.REJECT
    assert "tenant_out_of_scope" in decision.reason_codes


def test_reject_unresolved_conflict(gate_c):
    """Two otherwise-valid items from the same real governed source
    (necessarily sharing its authority) make incompatible `payment_terms`
    claims -- rejected outright, never silently proceeded with."""
    package = _package(
        [
            _item(
                "E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"], claims={"payment_terms": "net 30"}
            ),
            _item("E-B", source_id="SRC-POLICY-A", facets=["payment_terms"], claims={"payment_terms": "net 45"}),
        ]
    )
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only")
    )

    assert decision.decision is GateCStatus.REJECT
    assert decision.validated_evidence_ids == ()
    assert decision.conflict_facets == ("payment_terms",)
    assert "unresolved_conflict" in decision.reason_codes


def test_conflict_resolved_by_governed_authority_does_not_reject(gate_c):
    """`SRC-CONTRACT-A` (authority 100) outranks `SRC-POLICY-A` (authority
    90) -- a resolved conflict does not, by itself, block `allow`."""
    package = _package(
        [
            _item(
                "E-contract",
                source_id="SRC-CONTRACT-A",
                facets=["supplier_identity", "payment_terms"],
                claims={"payment_terms": "net 30"},
            ),
            _item("E-policy", source_id="SRC-POLICY-A", facets=["payment_terms"], claims={"payment_terms": "net 45"}),
        ]
    )
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only")
    )

    assert decision.decision is GateCStatus.ALLOW
    assert set(decision.validated_evidence_ids) == {"E-contract", "E-policy"}


# ===========================================================================
# Task 10 -- filtering behavior
# ===========================================================================


def test_filtering_five_retrieved_two_invalid_allow_using_three(gate_c):
    """Task 10's own worked example: 5 retrieved, 2 invalid, 3 valid,
    required facets still fully covered -> allow using only the 3
    validated items."""
    package = _package(
        [
            _item("E-1", source_id="SRC-POLICY-A", facets=["supplier_identity"]),
            _item("E-2", source_id="SRC-POLICY-A", facets=["payment_terms"]),
            _item("E-3", source_id="SRC-CONTRACT-A", facets=["payment_terms"]),
            _item("E-4", source_id="SRC-DOES-NOT-EXIST", facets=["payment_terms"]),  # invalid: unknown source
            _item("E-5", source_id="SRC-POLICY-A", facets=["payment_terms"], tenant_id="TENANT-B"),  # invalid: wrong tenant
        ]
    )
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only")
    )

    assert decision.decision is GateCStatus.ALLOW
    assert set(decision.validated_evidence_ids) == {"E-1", "E-2", "E-3"}
    assert set(decision.rejected_evidence_ids) == {"E-4", "E-5"}


def test_filtering_five_retrieved_two_invalid_required_facet_missing(gate_c):
    """Task 10's paired counter-example: same shape, but the surviving
    three never cover the request's full required-facet set ->
    insufficient_evidence, not allow."""
    package = _package(
        [
            _item("E-1", source_id="SRC-POLICY-A", facets=["supplier_identity"]),
            _item("E-2", source_id="SRC-POLICY-A", facets=["payment_terms"]),
            _item("E-3", source_id="SRC-CONTRACT-A", facets=["payment_terms"]),
            _item("E-4", source_id="SRC-DOES-NOT-EXIST", facets=["invoice_window"]),  # would have covered it
            _item("E-5", source_id="SRC-POLICY-A", facets=["invoice_window"], tenant_id="TENANT-B"),  # would have too
        ]
    )
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_and_invoice")
    )

    assert decision.decision is GateCStatus.INSUFFICIENT_EVIDENCE
    assert decision.missing_facets == ("invoice_window",)
    assert set(decision.rejected_evidence_ids) == {"E-4", "E-5"}


def test_rejected_evidence_never_appears_in_validated_ids(gate_c):
    package = _package(
        [
            _item("E-1", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"]),
            _item("E-2", source_id="SRC-DOES-NOT-EXIST", facets=["payment_terms"]),
        ]
    )
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only")
    )
    assert "E-2" not in decision.validated_evidence_ids
    assert "E-2" in decision.rejected_evidence_ids


def test_validated_evidence_ids_empty_for_every_non_allow_decision(gate_c):
    """Least privilege: nothing is forwarded until a decision actually
    resolves to `allow` -- the identical posture `GateBDecision.
    effective_tenant_scope` already takes for `ALLOW`."""
    package = _package([_item("E-1", source_id="SRC-DOES-NOT-EXIST", facets=["payment_terms"])])
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only")
    )
    assert decision.decision is not GateCStatus.ALLOW
    assert decision.validated_evidence_ids == ()


# ===========================================================================
# minimum_valid_items (Task 3's own field) -- a throwaway policy, since
# every real committed rule's minimum_valid_items is 1
# ===========================================================================


def test_insufficient_evidence_below_minimum_valid_items(source_registry):
    throwaway_policy_registry = GateCPolicyRegistry(
        GateCPolicyDocument.model_validate(
            {
                "policy_version": "throwaway",
                "status": "active",
                "freshness_policies": [
                    {"policy_id": "FRESH-POLICY-30D", "max_age_hours": 720},
                    {"policy_id": "FRESH-CONTRACT-7D", "max_age_hours": 168},
                ],
                "intent_requirements": [
                    {
                        "rule_id": "GC-THROWAWAY",
                        "intent_id": "INT-POLICY-QUESTION",
                        "request_kind": "payment_terms_only",
                        "required_facets": ["payment_terms"],
                        "minimum_valid_items": 2,
                        "allowed_source_types": ["policy_document", "contract_record"],
                        "conflict_policy": "authority_then_reject_tie",
                    }
                ],
            }
        )
    )
    gate_c = GateC(source_registry=source_registry, policy_registry=throwaway_policy_registry)

    package = _package([_item("E-1", source_id="SRC-POLICY-A", facets=["payment_terms"])])
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only")
    )

    assert decision.decision is GateCStatus.INSUFFICIENT_EVIDENCE
    assert "insufficient_valid_items" in decision.reason_codes


# ===========================================================================
# Decision provenance / purity
# ===========================================================================


def test_policy_and_source_registry_version_always_populated(gate_c):
    for request_kind, expected_items in [
        (None, []),
        ("totally_unknown", []),
        ("payment_terms_only", [_item("E-1", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"])]),
    ]:
        package = _package(expected_items)
        decision = gate_c.evaluate(
            gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind=request_kind)
        )
        assert decision.policy_version == "1.0"
        assert decision.source_registry_version == "1.0"


def test_evaluate_never_mutates_inputs(gate_c):
    package = _package([_item("E-1", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"])])
    items_before = copy.deepcopy(package.items)

    gate_c.evaluate(gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only"))

    assert package.items == items_before


def test_gate_c_is_reusable_and_stateless_across_calls(gate_c):
    good_package = _package([_item("E-1", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"])])
    bad_package = _package([_item("E-2", source_id="SRC-DOES-NOT-EXIST", facets=["payment_terms"])])

    first = gate_c.evaluate(gate_b_decision=_allow_decision(), package=good_package, request=GateCRequest(request_kind="payment_terms_only"))
    second = gate_c.evaluate(gate_b_decision=_allow_decision(), package=bad_package, request=GateCRequest(request_kind="payment_terms_only"))
    third = gate_c.evaluate(gate_b_decision=_allow_decision(), package=good_package, request=GateCRequest(request_kind="payment_terms_only"))

    assert first.decision is GateCStatus.ALLOW
    assert second.decision is GateCStatus.REJECT
    assert third.decision is GateCStatus.ALLOW
    assert third == first


def test_freshness_summary_reflects_item_outcomes(gate_c):
    package = _package([_item("E-1", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"])])
    decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=package, request=GateCRequest(request_kind="payment_terms_only")
    )
    assert decision.freshness_summary == "fresh=1;stale=0;other=0"


# ===========================================================================
# Real gate_c_cases.json -- end-to-end replay of every documented outcome
# ===========================================================================


_MATCHING_HASH_BY_CASE_ID = {"GC-001": True, "GC-002": True, "GC-003": True, "GC-004": False, "GC-005": True}


def _build_case_package(case: dict) -> EvidencePackage:
    items = []
    for raw in case["items"]:
        content = raw["content"]
        content_hash = stable_content_hash(content) if _MATCHING_HASH_BY_CASE_ID[case["id"]] else raw["provided_content_hash"]
        items.append(
            {
                "evidence_id": raw["evidence_id"],
                "chunk_id": raw["chunk_id"],
                "source_id": raw["source_id"],
                "source_version": raw["source_version"],
                "source_updated_at": raw["source_updated_at"],
                "retrieved_at": raw["retrieved_at"],
                "content_hash": content_hash,
                "tenant_id": raw["tenant_id"],
                "data_classification": raw["data_classification"],
                "evidence_facets": raw["evidence_facets"],
                "content": content,
                "claims": raw.get("claims", {}),
            }
        )
    return _package(items, intent_id=case["intent_id"], as_of=GATE_C_CASES["as_of"])


def _build_case_gate_b_decision(case: dict) -> GateBDecision:
    scope = case["gate_b_scope"]
    return _allow_decision(
        tenant_ids=tuple(scope["tenant_ids"]),
        data_classes=tuple(DataClassification(c) for c in scope["data_classes"]),
    )


@pytest.mark.parametrize("case_id", ["GC-001", "GC-002", "GC-003", "GC-004", "GC-005"])
def test_real_gate_c_case_reaches_documented_decision(gate_c, case_id):
    case = next(c for c in GATE_C_CASES["cases"] if c["id"] == case_id)
    package = _build_case_package(case)
    gate_b_decision = _build_case_gate_b_decision(case)

    decision = gate_c.evaluate(
        gate_b_decision=gate_b_decision, package=package, request=GateCRequest(request_kind=case["request_kind"])
    )

    assert decision.decision.value == case["expected_decision"]


# ===========================================================================
# Task 9 required decision contract fields
# ===========================================================================


def test_decision_type_is_a_closed_enum():
    assert {s.value for s in GateCStatus} == {"allow", "insufficient_evidence", "clarify", "reject"}


def test_decision_has_every_task_9_required_field():
    fields = set(GateCDecision.model_fields)
    assert fields == {
        "decision",
        "validated_evidence_ids",
        "rejected_evidence_ids",
        "reason_codes",
        "missing_facets",
        "conflict_facets",
        "freshness_summary",
        "policy_version",
        "source_registry_version",
    }
