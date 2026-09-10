"""
Day 10 Task 9 -- safe disclosure output: `redaction.py`'s deterministic
masking and `disclosure.py`'s `apply_disclosure()`/`SafeDisclosureView`.

Proves, against the real committed `policy/gate_b_policy.v1.json` and the
real supplied `day10_pack/fixtures/pii_disclosure_cases.json` (including
its `synthetic_record`/`field_metadata`):

  - `redaction.py`'s three masking shapes match `disclosure_rules.md`'s own
    worked examples exactly (email/phone/identifier), are deterministic
    (repeated calls, same input, same output), and an unrecognized/empty/
    non-`str` value falls back to a fully opaque mask rather than guessing;
  - `apply_disclosure()` end to end, driving the *entire* real synthetic
    record through a real `GateBDecision` for each of the three real
    disclosure profiles (`policy_reader` / `structured_reader` /
    `compliance_view`) and cross-checking every resulting field against
    `pii_disclosure_cases.json`'s own expectations (PII-001..006) plus the
    fields the fixture doesn't name explicitly;
  - Task 9's five required behaviors, each as its own named test: fields
    not allowed by the disclosure profile are omitted with `value is None`
    (never silently included), redactable fields are transformed
    deterministically, permitted fields remain byte-for-byte unchanged, the
    original `ProtectedField`/candidate sequence is never mutated, and
    `SafeDisclosureView` identifies `disclosure_profile`/`policy_version`
    without exposing raw policy internals (no `PermissionRule`, no
    `PolicyRegistry` reference, nothing beyond those two identifiers);
  - the disclosure-level "no fall-through" guarantee: a `DENY`/`CLARIFY`
    `GateBDecision`, or a `None` profile, always produces a completely
    empty view -- zero fields, regardless of what `candidate_fields` was
    passed;
  - "the policy, not the model, decides the action" extended to
    disclosure: no parameter anywhere in this module's public surface
    could carry a model's suggested action, and no model-gateway import
    exists in either file.

Task 3-8's authorization boundary is exercised only far enough to build
realistic `GateBDecision` fixtures for these tests -- see
`test_day10_gate_b.py`/`test_day10_tenant_scope.py` for that boundary's
own coverage.
"""
from __future__ import annotations

import copy
import dataclasses
import inspect
import json
from pathlib import Path

import pytest

from aico.control.disclosure import DisclosedField, ProtectedField, SafeDisclosureView, apply_disclosure
from aico.control.models import GateBDecision, GateBStatus
from aico.control.policy_models import DataClassification, DisclosureAction, PiiCategory
from aico.control.policy_registry import PolicyRegistry
from aico.control.redaction import mask_email, mask_identifier, mask_phone, mask_value

REPO_ROOT = Path(__file__).resolve().parent.parent
PII_DISCLOSURE_CASES_PATH = REPO_ROOT / "data" / "day10_pack" / "fixtures" / "pii_disclosure_cases.json"

PII_FIXTURE = json.loads(PII_DISCLOSURE_CASES_PATH.read_text(encoding="utf-8"))
SYNTHETIC_RECORD: dict[str, str] = PII_FIXTURE["synthetic_record"]
FIELD_METADATA: dict[str, dict[str, str]] = PII_FIXTURE["field_metadata"]
PII_CASES = PII_FIXTURE["cases"]


def _protected_fields() -> list[ProtectedField]:
    return [
        ProtectedField(
            name=name,
            value=value,
            data_class=DataClassification(FIELD_METADATA[name]["data_class"]),
            pii_category=PiiCategory(FIELD_METADATA[name]["pii_category"]),
        )
        for name, value in SYNTHETIC_RECORD.items()
    ]


@pytest.fixture
def registry() -> PolicyRegistry:
    return PolicyRegistry.load()


def _allow_decision(*, profile_id: str, rule_id: str, pii_policy: tuple[PiiCategory, ...]) -> GateBDecision:
    return GateBDecision(
        decision=GateBStatus.ALLOW,
        effective_tenant_scope=("TENANT-A",),
        effective_data_classes=(DataClassification.INTERNAL,),
        effective_pii_policy=pii_policy,
        disclosure_profile=profile_id,
        role_id="test-role",
        intent_id="INT-POLICY-QUESTION",
        rule_id=rule_id,
        reason_code="rule_allowed",
        policy_version="1.0",
    )


# ---------------------------------------------------------------------------
# redaction.py -- deterministic masking
# ---------------------------------------------------------------------------


def test_mask_email_matches_the_pack_example_exactly():
    assert mask_email("alice@example.test") == "a***@example.test"


def test_mask_phone_matches_the_pack_example_exactly():
    assert mask_phone("+1-555-0102") == "***-***-0102"


def test_mask_identifier_matches_the_pack_example_exactly():
    assert mask_identifier("SYN-ID-123456") == "**********3456"


def test_mask_value_dispatches_by_shape():
    assert mask_value("alice@example.test") == "a***@example.test"
    assert mask_value("+1-555-0102") == "***-***-0102"
    assert mask_value("SYN-ID-123456") == "**********3456"
    assert mask_value("SYN-BANK-00001234") == "**********1234"


@pytest.mark.parametrize("bad_value", ["", None, 42, [], {}])
def test_mask_value_falls_back_to_opaque_mask_for_unrecognizable_input(bad_value):
    assert mask_value(bad_value) == "***"


def test_mask_value_is_deterministic_across_repeated_calls():
    results = {mask_value("alice@example.test") for _ in range(10)}
    assert results == {"a***@example.test"}


def test_mask_value_never_reveals_the_original_value():
    for value in SYNTHETIC_RECORD.values():
        masked = mask_value(value)
        assert value not in masked or value == masked  # no accidental echo of secret content
        assert masked != value


def test_redaction_module_never_imports_a_model_gateway():
    """"Do not invent/redact values using a generative model" -- structural
    proof, same technique as Task 6's identical check on `gate_b.py`."""
    import aico.control.redaction as redaction_module

    source = inspect.getsource(redaction_module)
    for forbidden in ("model_gateway", "foundry_adapter", "ModelGateway", "ChatRequest"):
        assert forbidden not in source


# ---------------------------------------------------------------------------
# apply_disclosure() -- pii_disclosure_cases.json, fixture-driven, end to end
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", PII_CASES, ids=[c["id"] for c in PII_CASES])
def test_pii_disclosure_case_matches_end_to_end(registry, case):
    """The same six cases Task 8 proved directly against
    `resolve_disclosure_action`, now proved through the full
    `apply_disclosure()` pipeline (real `ProtectedField`s, real
    `GateBDecision`, real `DisclosureProfile`)."""
    profile_id = case["profile"]
    profile = registry.get_disclosure_profile(profile_id)
    rule = next(r for r in registry.rules if r.disclosure_profile == profile_id and r.allowed)
    decision = _allow_decision(profile_id=profile_id, rule_id=rule.rule_id, pii_policy=tuple(rule.allowed_pii_categories))

    view = apply_disclosure(decision, profile, _protected_fields())
    field = next(f for f in view.fields if f.name == case["field"])

    assert field.action.value == case["expected_action"]
    if case["expected_action"] == "allow":
        assert field.value == SYNTHETIC_RECORD[case["field"]]
    elif case["expected_action"] == "deny":
        assert field.value is None
    else:  # redact
        assert field.value == mask_value(SYNTHETIC_RECORD[case["field"]])
        assert field.value != SYNTHETIC_RECORD[case["field"]]


def test_full_record_under_policy_reader_profile(registry):
    """Every field of the real synthetic record, not just the ones
    `pii_disclosure_cases.json` names individually -- `policy_reader`
    (`GB-R001`)."""
    profile = registry.get_disclosure_profile("policy_reader")
    rule = registry.get_rule("GB-R001")
    decision = _allow_decision(profile_id="policy_reader", rule_id="GB-R001", pii_policy=tuple(rule.allowed_pii_categories))

    view = apply_disclosure(decision, profile, _protected_fields())
    by_name = {f.name: f for f in view.fields}

    assert by_name["supplier_name"].action is DisclosureAction.ALLOW
    assert by_name["supplier_name"].value == "Synthetic Supplier Alpha"
    assert by_name["payment_terms"].action is DisclosureAction.ALLOW
    assert by_name["invoice_window"].action is DisclosureAction.ALLOW
    assert by_name["contact_email"].action is DisclosureAction.REDACT
    assert by_name["contact_email"].value == "a***@example.test"
    assert by_name["tax_identifier"].action is DisclosureAction.DENY
    assert by_name["tax_identifier"].value is None
    assert by_name["bank_account"].action is DisclosureAction.DENY
    assert by_name["personal_notes"].action is DisclosureAction.DENY
    # `contract_status` is not declared in `policy_reader.field_actions` at
    # all -- fail-closed DENY for an undeclared field (Task 8), proven here
    # end to end rather than only against `resolve_disclosure_action`.
    assert by_name["contract_status"].action is DisclosureAction.DENY


def test_disclosed_values_omits_every_denied_field(registry):
    profile = registry.get_disclosure_profile("policy_reader")
    rule = registry.get_rule("GB-R001")
    decision = _allow_decision(profile_id="policy_reader", rule_id="GB-R001", pii_policy=tuple(rule.allowed_pii_categories))

    view = apply_disclosure(decision, profile, _protected_fields())
    disclosed = view.disclosed_values()

    for denied_field in ("tax_identifier", "bank_account", "personal_notes", "contract_status"):
        assert denied_field not in disclosed
    assert disclosed["supplier_name"] == "Synthetic Supplier Alpha"
    assert disclosed["contact_email"] == "a***@example.test"


# ---------------------------------------------------------------------------
# Task 9 required behaviors
# ---------------------------------------------------------------------------


def test_fields_not_allowed_are_omitted_with_no_value(registry):
    profile = registry.get_disclosure_profile("policy_reader")
    rule = registry.get_rule("GB-R001")
    decision = _allow_decision(profile_id="policy_reader", rule_id="GB-R001", pii_policy=tuple(rule.allowed_pii_categories))

    view = apply_disclosure(decision, profile, _protected_fields())
    denied = [f for f in view.fields if f.action is DisclosureAction.DENY]

    assert denied  # sanity: at least one field is actually denied here
    for field in denied:
        assert field.value is None


def test_permitted_fields_remain_unchanged(registry):
    profile = registry.get_disclosure_profile("compliance_view")
    rule = registry.get_rule("GB-R005")
    decision = _allow_decision(profile_id="compliance_view", rule_id="GB-R005", pii_policy=tuple(rule.allowed_pii_categories))

    view = apply_disclosure(decision, profile, _protected_fields())
    allowed = {f.name: f.value for f in view.fields if f.action is DisclosureAction.ALLOW}

    for name, value in allowed.items():
        assert value == SYNTHETIC_RECORD[name]


def test_original_candidate_fields_sequence_is_not_mutated(registry):
    profile = registry.get_disclosure_profile("policy_reader")
    rule = registry.get_rule("GB-R001")
    decision = _allow_decision(profile_id="policy_reader", rule_id="GB-R001", pii_policy=tuple(rule.allowed_pii_categories))

    original_fields = _protected_fields()
    snapshot = copy.deepcopy(original_fields)

    apply_disclosure(decision, profile, original_fields)

    assert original_fields == snapshot
    for field in original_fields:
        assert field.value == SYNTHETIC_RECORD[field.name]  # untouched, still the raw value


def test_protected_field_is_frozen():
    field = ProtectedField(name="x", value="y", data_class=DataClassification.PUBLIC, pii_category=PiiCategory.NONE)
    with pytest.raises(Exception):  # noqa: B017 - dataclasses.FrozenInstanceError, deliberately broad
        field.value = "mutated"  # type: ignore[misc]


def test_view_identifies_profile_and_policy_version_without_raw_policy_internals(registry):
    profile = registry.get_disclosure_profile("policy_reader")
    rule = registry.get_rule("GB-R001")
    decision = _allow_decision(profile_id="policy_reader", rule_id="GB-R001", pii_policy=tuple(rule.allowed_pii_categories))

    view = apply_disclosure(decision, profile, _protected_fields())

    assert view.disclosure_profile == "policy_reader"
    assert view.policy_version == "1.0"
    view_field_names = {f.name for f in dataclasses.fields(SafeDisclosureView)}
    assert view_field_names == {"fields", "disclosure_profile", "policy_version"}


# ---------------------------------------------------------------------------
# No fall-through: DENY/CLARIFY/missing profile always produce an empty view
# ---------------------------------------------------------------------------


def test_deny_decision_produces_an_empty_view(registry):
    profile = registry.get_disclosure_profile("policy_reader")
    decision = GateBDecision(decision=GateBStatus.DENY, reason_code="rule_denied", policy_version="1.0")

    view = apply_disclosure(decision, profile, _protected_fields())

    assert view.fields == ()
    assert view.disclosure_profile is None
    assert view.disclosed_values() == {}


def test_clarify_decision_produces_an_empty_view(registry):
    profile = registry.get_disclosure_profile("policy_reader")
    decision = GateBDecision(
        decision=GateBStatus.CLARIFY, reason_code="data_class_selection_required", policy_version="1.0"
    )

    view = apply_disclosure(decision, profile, _protected_fields())

    assert view.fields == ()
    assert view.disclosure_profile is None


def test_missing_profile_produces_an_empty_view_even_for_an_allow_decision():
    """A hand-built `ALLOW` decision with no profile resolved (`profile`
    passed as `None`) still produces nothing -- defense-in-depth against a
    caller that forgot to resolve the profile before calling this
    function."""
    decision = GateBDecision(
        decision=GateBStatus.ALLOW,
        effective_tenant_scope=("TENANT-A",),
        disclosure_profile="policy_reader",
        reason_code="rule_allowed",
        policy_version="1.0",
    )

    view = apply_disclosure(decision, None, _protected_fields())

    assert view.fields == ()
    assert view.disclosure_profile is None


def test_empty_candidate_fields_produces_an_empty_view_for_an_allow_decision(registry):
    profile = registry.get_disclosure_profile("policy_reader")
    rule = registry.get_rule("GB-R001")
    decision = _allow_decision(profile_id="policy_reader", rule_id="GB-R001", pii_policy=tuple(rule.allowed_pii_categories))

    view = apply_disclosure(decision, profile, [])

    assert view.fields == ()
    assert view.disclosure_profile == "policy_reader"  # profile is still identified, just nothing to disclose


# ---------------------------------------------------------------------------
# PII-category defense-in-depth (beyond field_actions alone)
# ---------------------------------------------------------------------------


def test_pii_category_not_in_effective_policy_denies_even_if_field_actions_would_allow():
    """A hand-built scenario `pii_disclosure_cases.json` does not itself
    cover: a disclosure profile that says `allow` for a field whose PII
    category the decision's own `effective_pii_policy` does not actually
    authorize. `is_pii_category_permitted`'s defense-in-depth check
    downgrades this to `DENY` rather than trusting the profile alone."""
    from aico.control.policy_models import DisclosureProfile

    profile = DisclosureProfile(profile_id="test_profile", field_actions={"secret_field": DisclosureAction.ALLOW})
    decision = _allow_decision(profile_id="test_profile", rule_id="GB-TEST", pii_policy=(PiiCategory.NONE,))
    field = ProtectedField(
        name="secret_field", value="sensitive-value", data_class=DataClassification.INTERNAL, pii_category=PiiCategory.FINANCIAL
    )

    view = apply_disclosure(decision, profile, [field])

    assert view.fields[0].action is DisclosureAction.DENY
    assert view.fields[0].value is None


def test_pii_category_check_never_upgrades_a_deny_into_something_else():
    from aico.control.policy_models import DisclosureProfile

    profile = DisclosureProfile(profile_id="test_profile", field_actions={"secret_field": DisclosureAction.DENY})
    decision = _allow_decision(profile_id="test_profile", rule_id="GB-TEST", pii_policy=tuple(PiiCategory))
    field = ProtectedField(
        name="secret_field", value="sensitive-value", data_class=DataClassification.INTERNAL, pii_category=PiiCategory.NONE
    )

    view = apply_disclosure(decision, profile, [field])

    assert view.fields[0].action is DisclosureAction.DENY


# ---------------------------------------------------------------------------
# The policy, not the model, decides the action -- structural proof
# ---------------------------------------------------------------------------


def test_apply_disclosure_signature_has_no_model_or_memory_parameter():
    params = list(inspect.signature(apply_disclosure).parameters)
    assert params == ["decision", "profile", "candidate_fields"]


def test_disclosure_module_never_imports_a_model_gateway():
    import aico.control.disclosure as disclosure_module

    source = inspect.getsource(disclosure_module)
    for forbidden in ("model_gateway", "foundry_adapter", "ModelGateway", "ChatRequest"):
        assert forbidden not in source


def test_disclosed_field_has_no_hidden_raw_value_alongside_a_deny():
    """A denied `DisclosedField` never carries the original value under a
    different attribute name -- the dataclass has exactly `name`/`value`/
    `action`, nowhere else for a raw value to hide."""
    field_names = {f.name for f in dataclasses.fields(DisclosedField)}
    assert field_names == {"name", "value", "action"}
