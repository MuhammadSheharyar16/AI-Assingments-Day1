"""
Day 12 Task 6 -- final disclosure validation, the field/profile-driven
half (`check_final_disclosure()`, `src/aico/control/gate_d.py`).

`disclosure_leak_cases.json`'s six cases split cleanly across two
mechanisms (see `gate_d.py`'s own "Day 12 Task 6" section): DISC12-001
through DISC12-004 are governed by Gate-B's own real disclosure profile
(`policy/gate_b_policy.v1.json`), reused wholesale via Day 10's
`apply_disclosure()` -- these four are this file's Section 1. DISC12-005/
DISC12-006 (a raw secret-token leak, a raw hidden-prompt-marker leak) are
Task 7's own dedicated deterministic detector's responsibility -- Section
1 still proves `check_final_disclosure()` correctly reports `passed=True`
for both in isolation (nothing in this check's scope to flag), and this
file will grow a matching section for Task 7's own function once that
task lands, the same way `test_day12_final_citations.py` grew a Task 4
section once Task 4 was in scope.

Section 2 proves the remaining Task 6 behaviors directly: an `ALLOW`-
resolved field is never flagged even though it is "raw PII", the
`enforce_gate_b_profile`/no-resolved-profile skip cases, and that no raw
protected value ever appears in the report itself (working rule: "Gate-D
telemetry must remain sanitized").
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aico.control.disclosure import ProtectedField
from aico.control.final_response import FinalResponseCandidate, parse_final_response_candidate
from aico.control.gate_d import DisclosureCheckReport, DisclosureReasonCode, check_final_disclosure
from aico.control.models import GateBDecision, GateBStatus
from aico.control.ontology import LaneId
from aico.control.policy_models import DataClassification, DisclosureAction, PiiCategory
from aico.control.policy_registry import GateDPolicyRegistry, PolicyRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent
DAY12_FIXTURES = REPO_ROOT / "data" / "day12_pack" / "fixtures"
DISCLOSURE_LEAK_CASES = json.loads((DAY12_FIXTURES / "disclosure_leak_cases.json").read_text(encoding="utf-8"))
PROTECTED_VALUES = DISCLOSURE_LEAK_CASES["protected_values"]

_STARTED_AT = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)

_GATE_B_POLICY = PolicyRegistry.load()
_GATE_D_POLICY = GateDPolicyRegistry.load(gate_b_policy=_GATE_B_POLICY)
REAL_DISCLOSURE_POLICY = _GATE_D_POLICY.disclosure_policy

# Field metadata reused verbatim from Day 10's own
# `data/day10_pack/fixtures/pii_disclosure_cases.json` `field_metadata` --
# the identical synthetic record `disclosure_leak_cases.json`'s own
# `protected_values` is drawn from (same email/tax-id/bank-account
# strings), so the two packs' governed field shapes agree.
PROTECTED_FIELDS = (
    ProtectedField(
        name="contact_email",
        value=PROTECTED_VALUES["contact_email"],
        data_class=DataClassification.CONFIDENTIAL,
        pii_category=PiiCategory.CONTACT,
    ),
    ProtectedField(
        name="tax_identifier",
        value=PROTECTED_VALUES["tax_identifier"],
        data_class=DataClassification.CONFIDENTIAL,
        pii_category=PiiCategory.PERSONAL_IDENTIFIER,
    ),
    ProtectedField(
        name="bank_account",
        value=PROTECTED_VALUES["bank_account"],
        data_class=DataClassification.RESTRICTED,
        pii_category=PiiCategory.FINANCIAL,
    ),
)


def _gate_b_decision_for_profile(profile_id: str) -> GateBDecision:
    """A plausible real `GateBDecision` for one of the two profiles the
    fixture actually exercises -- `effective_pii_policy`/
    `effective_data_classes` mirror the real committed rule that grants
    each profile (`GB-R001` for `policy_reader`, `GB-R005` for
    `compliance_view`; `policy/gate_b_policy.v1.json`)."""
    if profile_id == "policy_reader":
        effective_pii_policy = (PiiCategory.NONE, PiiCategory.CONTACT)
        effective_data_classes = (DataClassification.PUBLIC, DataClassification.INTERNAL)
    elif profile_id == "compliance_view":
        effective_pii_policy = (PiiCategory.NONE, PiiCategory.CONTACT, PiiCategory.PERSONAL_IDENTIFIER)
        effective_data_classes = (DataClassification.PUBLIC, DataClassification.INTERNAL, DataClassification.CONFIDENTIAL)
    else:  # pragma: no cover - only the two profiles above are exercised
        raise ValueError(profile_id)

    return GateBDecision(
        decision=GateBStatus.ALLOW,
        effective_tenant_scope=("TENANT-A",),
        effective_data_classes=effective_data_classes,
        effective_pii_policy=effective_pii_policy,
        disclosure_profile=profile_id,
        lane=LaneId.RAG,
        reason_code="test_fixture",
        policy_version=_GATE_B_POLICY.policy_version,
    )


def _base_envelope(**overrides: object) -> dict:
    payload: dict = {
        "request_id": "REQ-1",
        "correlation_id": "CORR-1",
        "candidate_status": "answered",
        "candidate_answer": "Synthetic Supplier Alpha uses net 30 payment terms.",
        "candidate_citations": [],
        "gate_c_validated_evidence_ids": [],
        "started_at": _STARTED_AT.isoformat(),
        "elapsed_ms": 1200,
        "model_latency_ms": 800,
        "contract_validation_status": "passed",
        "semantic_validation_status": "passed",
    }
    payload.update(overrides)
    return payload


def _candidate_from(**overrides: object) -> FinalResponseCandidate:
    return parse_final_response_candidate(_base_envelope(**overrides))


def _check(case: dict) -> DisclosureCheckReport:
    profile_id = case.get("disclosure_profile")
    candidate = _candidate_from(
        candidate_answer=case["candidate_answer"], gate_b_disclosure_profile=profile_id
    )
    if profile_id is None:
        gate_b_decision = _gate_b_decision_for_profile("policy_reader")  # decision itself unused when profile is None
        disclosure_profile = None
    else:
        gate_b_decision = _gate_b_decision_for_profile(profile_id)
        disclosure_profile = _GATE_B_POLICY.get_disclosure_profile(profile_id)

    return check_final_disclosure(
        candidate,
        gate_b_decision=gate_b_decision,
        disclosure_profile=disclosure_profile,
        protected_fields=PROTECTED_FIELDS,
        policy=REAL_DISCLOSURE_POLICY,
    )


# ══════════════════════════════════════════════════════════════════════
# Section 1 -- fixture replay.
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("case", DISCLOSURE_LEAK_CASES["cases"], ids=lambda case: case["id"])
def test_disclosure_leak_cases_field_check_outcome(case: dict) -> None:
    """DISC12-001..004 reproduce their own `expected` outcome exactly
    through this check alone; DISC12-005/006 report `passed=True` here --
    see module docstring for why that is correct, not a gap."""
    report = _check(case)
    if case["id"] in ("DISC12-005", "DISC12-006"):
        assert report.passed is True  # Task 7's own detector is what fails these
    else:
        expected_passed = case["expected"] == "allow"
        assert report.passed is expected_passed, (case["id"], report.reason_codes)


def test_safe_output_case_names_no_failure_reason() -> None:
    case = next(c for c in DISCLOSURE_LEAK_CASES["cases"] if c["id"] == "DISC12-001")
    report = _check(case)
    assert report.passed is True
    assert report.reason_codes == ()
    assert report.leaked_field_names == ()


def test_redactable_contact_leak_case_reason() -> None:
    case = next(c for c in DISCLOSURE_LEAK_CASES["cases"] if c["id"] == "DISC12-002")
    report = _check(case)
    assert report.passed is False
    assert report.reason_codes == (DisclosureReasonCode.REDACTABLE_FIELD_VALUE_LEAKED,)
    assert report.leaked_field_names == ("contact_email",)


def test_denied_tax_identifier_case_reason() -> None:
    case = next(c for c in DISCLOSURE_LEAK_CASES["cases"] if c["id"] == "DISC12-003")
    report = _check(case)
    assert report.passed is False
    assert report.reason_codes == (DisclosureReasonCode.DENIED_FIELD_VALUE_LEAKED,)
    assert report.leaked_field_names == ("tax_identifier",)


def test_denied_bank_account_case_reason() -> None:
    case = next(c for c in DISCLOSURE_LEAK_CASES["cases"] if c["id"] == "DISC12-004")
    report = _check(case)
    assert report.passed is False
    assert report.reason_codes == (DisclosureReasonCode.DENIED_FIELD_VALUE_LEAKED,)
    assert report.leaked_field_names == ("bank_account",)


# ══════════════════════════════════════════════════════════════════════
# Section 2 -- specific Task 6 behaviors.
# ══════════════════════════════════════════════════════════════════════


def test_allowed_field_value_appearing_is_not_a_leak() -> None:
    """`compliance_view` resolves `contact_email` to `allow`
    (`policy/gate_b_policy.v1.json`) -- the raw value appearing in the
    final text is, by construction, authorized, not a leak, even though
    it is "raw PII"."""
    candidate = _candidate_from(
        candidate_answer=f"Contact email on file is {PROTECTED_VALUES['contact_email']}.",
        gate_b_disclosure_profile="compliance_view",
    )
    report = check_final_disclosure(
        candidate,
        gate_b_decision=_gate_b_decision_for_profile("compliance_view"),
        disclosure_profile=_GATE_B_POLICY.get_disclosure_profile("compliance_view"),
        protected_fields=PROTECTED_FIELDS,
        policy=REAL_DISCLOSURE_POLICY,
    )
    assert report.passed is True
    contact_email_check = next(c for c in report.field_checks if c.field_name == "contact_email")
    assert contact_email_check.action is DisclosureAction.ALLOW
    assert contact_email_check.leaked is False


def test_redacted_or_paraphrased_answer_without_raw_value_passes() -> None:
    """A `policy_reader` answer that never actually repeats the raw
    `contact_email` value (properly redacted/paraphrased upstream) is not
    a leak -- this check only ever flags the literal raw value
    reappearing, never the mere existence of a redact-governed field."""
    candidate = _candidate_from(
        candidate_answer="Contact email is on file and has been redacted for this view.",
        gate_b_disclosure_profile="policy_reader",
    )
    report = check_final_disclosure(
        candidate,
        gate_b_decision=_gate_b_decision_for_profile("policy_reader"),
        disclosure_profile=_GATE_B_POLICY.get_disclosure_profile("policy_reader"),
        protected_fields=PROTECTED_FIELDS,
        policy=REAL_DISCLOSURE_POLICY,
    )
    assert report.passed is True


def test_enforce_gate_b_profile_false_skips_check_entirely() -> None:
    """No shipped fixture exercises this flag as `False` -- proven
    directly: with it off, even a candidate leaking a denied field value
    is reported as passing this check (the policy has decided this lane
    does not re-validate against Gate-B's own profile at the release
    boundary)."""
    lenient_policy = REAL_DISCLOSURE_POLICY.model_copy(update={"enforce_gate_b_profile": False})
    candidate = _candidate_from(
        candidate_answer=f"Tax identifier is {PROTECTED_VALUES['tax_identifier']}.",
        gate_b_disclosure_profile="policy_reader",
    )
    report = check_final_disclosure(
        candidate,
        gate_b_decision=_gate_b_decision_for_profile("policy_reader"),
        disclosure_profile=_GATE_B_POLICY.get_disclosure_profile("policy_reader"),
        protected_fields=PROTECTED_FIELDS,
        policy=lenient_policy,
    )
    assert report.passed is True
    assert report.field_checks == ()


def test_no_resolved_disclosure_profile_trivially_passes() -> None:
    """`apply_disclosure()`'s own "no fall-through" guarantee (Day 10
    Task 9): a `None` profile produces an empty disclosed view, so there
    is nothing for this check to flag either -- correct, not a gap (see
    module docstring)."""
    candidate = _candidate_from(candidate_answer=f"Tax identifier is {PROTECTED_VALUES['tax_identifier']}.")
    report = check_final_disclosure(
        candidate,
        gate_b_decision=_gate_b_decision_for_profile("policy_reader"),
        disclosure_profile=None,
        protected_fields=PROTECTED_FIELDS,
        policy=REAL_DISCLOSURE_POLICY,
    )
    assert report.passed is True
    assert report.field_checks == ()


def test_report_never_contains_a_raw_protected_value() -> None:
    """Working rule: "Gate-D telemetry must remain sanitized." Proven
    structurally: nothing in a failing report's own serialized form is
    one of the raw protected values -- only field names and governed
    `DisclosureAction`s."""
    case = next(c for c in DISCLOSURE_LEAK_CASES["cases"] if c["id"] == "DISC12-003")
    report = _check(case)
    assert report.passed is False
    serialized = report.model_dump_json()
    assert PROTECTED_VALUES["tax_identifier"] not in serialized


def test_multiple_leaking_fields_all_reported() -> None:
    candidate = _candidate_from(
        candidate_answer=(
            f"Tax identifier is {PROTECTED_VALUES['tax_identifier']} "
            f"and contact email is {PROTECTED_VALUES['contact_email']}."
        ),
        gate_b_disclosure_profile="policy_reader",
    )
    report = check_final_disclosure(
        candidate,
        gate_b_decision=_gate_b_decision_for_profile("policy_reader"),
        disclosure_profile=_GATE_B_POLICY.get_disclosure_profile("policy_reader"),
        protected_fields=PROTECTED_FIELDS,
        policy=REAL_DISCLOSURE_POLICY,
    )
    assert report.passed is False
    assert set(report.leaked_field_names) == {"tax_identifier", "contact_email"}
    assert set(report.reason_codes) == {
        DisclosureReasonCode.DENIED_FIELD_VALUE_LEAKED,
        DisclosureReasonCode.REDACTABLE_FIELD_VALUE_LEAKED,
    }


def test_report_never_raises_for_an_ordinary_input() -> None:
    candidate = _candidate_from(gate_b_disclosure_profile="policy_reader")
    report = check_final_disclosure(
        candidate,
        gate_b_decision=_gate_b_decision_for_profile("policy_reader"),
        disclosure_profile=_GATE_B_POLICY.get_disclosure_profile("policy_reader"),
        protected_fields=(),
        policy=REAL_DISCLOSURE_POLICY,
    )
    assert isinstance(report, DisclosureCheckReport)
    assert report.passed is True
