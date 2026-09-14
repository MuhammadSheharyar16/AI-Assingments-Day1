"""
Day 12 Task 2 -- the Gate-D policy (`GateDPolicyDocument` /
`GateDPolicyRegistry`, both in `src/aico/control/policy_models.py` /
`policy_registry.py`).

Mirrors `test_day11_gate_c_policy.py`'s two-section structure: the first
proves `GateDPolicyDocument`'s own typed validation directly against
Pydantic (every Task 2 "Required validation" bullet, mapped in
`policy_models.py`'s own "Day 12 Task 2" section); the second proves
`GateDPolicyRegistry` end to end -- loading the real committed
`policy/gate_d_policy.v1.json`, its read-only accessors, and its
cross-reference against the real committed Gate-B policy's own governed
disclosure profiles (`has_disclosure_profile()`).

Gate-D itself (Task 10) is not implemented yet and is out of scope here --
this file only proves the typed Gate-D policy model and loader/lookup
boundary.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aico.contracts.models import AnswerStatus
from aico.control.errors import GateDPolicyLoadError
from aico.control.ontology import LifecycleStatus
from aico.control.policy_models import (
    CitationPolicy,
    GateDDisclosurePolicy,
    GateDPolicyDocument,
    LatencyBudgets,
    QualityPolicy,
    SafeFailureSpec,
)
from aico.control.policy_registry import (
    DEFAULT_GATE_D_POLICY_PATH,
    GateDPolicyRegistry,
    PolicyRegistry,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_POLICY_PATH = REPO_ROOT / "policy" / "gate_d_policy.v1.json"
COMMITTED_GATE_B_POLICY_PATH = REPO_ROOT / "policy" / "gate_b_policy.v1.json"
PACK_FIXTURE_PATH = REPO_ROOT / "data" / "day12_pack" / "fixtures" / "gate_d_policy_v1.json"


def _load_pack_fixture_dict() -> dict:
    return json.loads(PACK_FIXTURE_PATH.read_text(encoding="utf-8"))


def _load_committed_policy_dict() -> dict:
    return json.loads(COMMITTED_POLICY_PATH.read_text(encoding="utf-8"))


def _minimal_valid_policy() -> dict:
    """A small, hand-built valid Gate-D policy document for isolated
    negative-path mutation tests, distinct from the real fixture so each
    test only ever changes the one thing it is proving is rejected."""
    return {
        "policy_version": "1.0",
        "status": "active",
        "allowed_response_statuses": ["answered", "insufficient_evidence"],
        "citation_policy": {
            "answered_requires_citation": True,
            "citations_must_be_gate_c_approved": True,
            "reject_mixed_valid_invalid": True,
        },
        "quality_policy": {
            "max_answer_chars": 1600,
            "answered_must_be_nonempty": True,
            "contract_must_pass": True,
            "semantic_validation_must_pass": True,
        },
        "disclosure_policy": {
            "enforce_gate_b_profile": True,
            "block_secret_patterns": True,
            "block_hidden_prompt_markers": True,
        },
        "latency_budgets": {
            "max_total_latency_ms": 2500,
            "max_model_latency_ms": 1400,
            "threshold_is_inclusive": True,
        },
        "safe_failure": {
            "status": "safe_failure",
            "code": "FINAL_RESPONSE_REJECTED",
            "message": "The response could not be safely returned.",
        },
    }


# ══════════════════════════════════════════════════════════════════════
# Section 1 -- GateDPolicyDocument's own typed validation.
# ══════════════════════════════════════════════════════════════════════


def test_pack_fixture_is_committed_unmodified() -> None:
    """`policy/gate_d_policy.v1.json` must be byte-for-byte the resource
    pack's own fixture -- "Do not edit fixed fixtures to make the
    implementation pass" (`day12_pack/README.md`)."""
    assert _load_committed_policy_dict() == _load_pack_fixture_dict()


def test_committed_policy_parses() -> None:
    document = GateDPolicyDocument.model_validate(_load_committed_policy_dict())
    assert document.policy_version == "1.0"
    assert document.status is LifecycleStatus.ACTIVE
    assert document.allowed_response_statuses == (AnswerStatus.ANSWERED, AnswerStatus.INSUFFICIENT_EVIDENCE)
    assert isinstance(document.citation_policy, CitationPolicy)
    assert isinstance(document.quality_policy, QualityPolicy)
    assert isinstance(document.disclosure_policy, GateDDisclosurePolicy)
    assert isinstance(document.latency_budgets, LatencyBudgets)
    assert isinstance(document.safe_failure, SafeFailureSpec)
    assert document.quality_policy.max_answer_chars == 1600
    assert document.latency_budgets.max_total_latency_ms == 2500
    assert document.latency_budgets.max_model_latency_ms == 1400
    assert document.safe_failure.code == "FINAL_RESPONSE_REJECTED"


def test_minimal_valid_policy_parses() -> None:
    GateDPolicyDocument.model_validate(_minimal_valid_policy())


def test_policy_version_required() -> None:
    data = _minimal_valid_policy()
    del data["policy_version"]
    with pytest.raises(ValidationError):
        GateDPolicyDocument.model_validate(data)


def test_empty_policy_version_rejected() -> None:
    """`policy_version` is `Field(min_length=1)`, the identical bare
    constraint `GateBPolicyDocument`/`GateCPolicyDocument` already use for
    their own `policy_version` -- an empty string is rejected; a
    whitespace-only one is not stricter-checked here either, matching
    that sibling precedent exactly (contrast `final_response.py`'s
    identifier fields, which do add the stricter check)."""
    data = _minimal_valid_policy()
    data["policy_version"] = ""
    with pytest.raises(ValidationError):
        GateDPolicyDocument.model_validate(data)


def test_invalid_status_rejected() -> None:
    data = _minimal_valid_policy()
    data["status"] = "not_a_real_status"
    with pytest.raises(ValidationError):
        GateDPolicyDocument.model_validate(data)


def test_invalid_response_status_rejected() -> None:
    data = _minimal_valid_policy()
    data["allowed_response_statuses"] = ["answered", "fabricated_status"]
    with pytest.raises(ValidationError):
        GateDPolicyDocument.model_validate(data)


def test_empty_allowed_response_statuses_rejected() -> None:
    data = _minimal_valid_policy()
    data["allowed_response_statuses"] = []
    with pytest.raises(ValidationError):
        GateDPolicyDocument.model_validate(data)


def test_duplicate_allowed_response_statuses_rejected() -> None:
    data = _minimal_valid_policy()
    data["allowed_response_statuses"] = ["answered", "answered"]
    with pytest.raises(ValidationError, match="duplicate"):
        GateDPolicyDocument.model_validate(data)


@pytest.mark.parametrize("field_name", ["max_total_latency_ms", "max_model_latency_ms"])
@pytest.mark.parametrize("invalid_value", [-1, 0])
def test_invalid_or_negative_latency_budget_rejected(field_name: str, invalid_value: int) -> None:
    data = _minimal_valid_policy()
    data["latency_budgets"][field_name] = invalid_value
    with pytest.raises(ValidationError):
        GateDPolicyDocument.model_validate(data)


@pytest.mark.parametrize("invalid_value", [-1, 0])
def test_invalid_quality_policy_max_answer_chars_rejected(invalid_value: int) -> None:
    data = _minimal_valid_policy()
    data["quality_policy"]["max_answer_chars"] = invalid_value
    with pytest.raises(ValidationError):
        GateDPolicyDocument.model_validate(data)


@pytest.mark.parametrize(
    "section,field_name",
    [
        ("citation_policy", "answered_requires_citation"),
        ("quality_policy", "answered_must_be_nonempty"),
        ("disclosure_policy", "enforce_gate_b_profile"),
        ("latency_budgets", "threshold_is_inclusive"),
    ],
)
def test_missing_sub_policy_field_rejected(section: str, field_name: str) -> None:
    data = _minimal_valid_policy()
    del data[section][field_name]
    with pytest.raises(ValidationError):
        GateDPolicyDocument.model_validate(data)


def test_unknown_top_level_field_rejected() -> None:
    data = _minimal_valid_policy()
    data["unexpected_field"] = "surprise"
    with pytest.raises(ValidationError):
        GateDPolicyDocument.model_validate(data)


@pytest.mark.parametrize("section", ["citation_policy", "quality_policy", "disclosure_policy", "latency_budgets", "safe_failure"])
def test_unknown_sub_policy_field_rejected(section: str) -> None:
    data = _minimal_valid_policy()
    data[section]["unexpected_field"] = "surprise"
    with pytest.raises(ValidationError):
        GateDPolicyDocument.model_validate(data)


def test_safe_failure_status_pinned_to_literal() -> None:
    data = _minimal_valid_policy()
    data["safe_failure"]["status"] = "rejected"
    with pytest.raises(ValidationError):
        GateDPolicyDocument.model_validate(data)


@pytest.mark.parametrize("field_name", ["code", "message"])
def test_empty_safe_failure_field_rejected(field_name: str) -> None:
    data = _minimal_valid_policy()
    data["safe_failure"][field_name] = ""
    with pytest.raises(ValidationError):
        GateDPolicyDocument.model_validate(data)


def test_missing_required_sub_policy_rejected() -> None:
    for section in ("citation_policy", "quality_policy", "disclosure_policy", "latency_budgets", "safe_failure"):
        data = _minimal_valid_policy()
        del data[section]
        with pytest.raises(ValidationError):
            GateDPolicyDocument.model_validate(data)


def test_document_is_not_frozen_but_registry_never_mutates_it() -> None:
    """Same allowance `policy_registry.py`'s own docstring notes for
    `GateBPolicyDocument`: the document type itself is an ordinary,
    mutable Pydantic model (so isolated tests can build throwaway
    documents easily) -- it is `GateDPolicyRegistry` that guarantees
    nothing at runtime ever calls a mutator on the one instance it
    loaded (see Section 2's read-only accessor tests)."""
    document = GateDPolicyDocument.model_validate(_minimal_valid_policy())
    document.policy_version = "mutated"  # allowed on the bare model
    assert document.policy_version == "mutated"


# ══════════════════════════════════════════════════════════════════════
# Section 2 -- GateDPolicyRegistry end to end.
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def gate_b_policy() -> PolicyRegistry:
    return PolicyRegistry.load()


@pytest.fixture()
def gate_d_policy(gate_b_policy: PolicyRegistry) -> GateDPolicyRegistry:
    return GateDPolicyRegistry.load(gate_b_policy=gate_b_policy)


def test_load_default_path_matches_constant() -> None:
    assert DEFAULT_GATE_D_POLICY_PATH == Path("policy/gate_d_policy.v1.json")


def test_load_real_committed_policy_succeeds(gate_d_policy: GateDPolicyRegistry) -> None:
    assert gate_d_policy.policy_version == "1.0"
    assert gate_d_policy.allowed_response_statuses == (AnswerStatus.ANSWERED, AnswerStatus.INSUFFICIENT_EVIDENCE)


def test_load_defaults_gate_b_policy_when_not_supplied() -> None:
    """`load()` with no `gate_b_policy` argument still resolves the real
    committed Gate-B policy itself (`PolicyRegistry.load()`), the same
    "defaults to the real committed resource" convention
    `GateCPolicyRegistry.load()` already follows for its own
    ontology/source registry parameters."""
    registry = GateDPolicyRegistry.load()
    assert registry.has_disclosure_profile("policy_reader")


def test_missing_policy_file_raises() -> None:
    with pytest.raises(GateDPolicyLoadError):
        GateDPolicyRegistry.load(REPO_ROOT / "policy" / "does_not_exist.v1.json")


def test_not_json_raises(tmp_path) -> None:
    bad_path = tmp_path / "gate_d_policy.v1.json"
    bad_path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(GateDPolicyLoadError):
        GateDPolicyRegistry.load(bad_path)


def test_schema_invalid_file_raises(tmp_path) -> None:
    data = _minimal_valid_policy()
    data["latency_budgets"]["max_total_latency_ms"] = -5
    bad_path = tmp_path / "gate_d_policy.v1.json"
    bad_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(GateDPolicyLoadError):
        GateDPolicyRegistry.load(bad_path)


def test_load_never_leaks_raw_pydantic_validation_error(tmp_path) -> None:
    data = _minimal_valid_policy()
    del data["policy_version"]
    bad_path = tmp_path / "gate_d_policy.v1.json"
    bad_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(GateDPolicyLoadError):
        try:
            GateDPolicyRegistry.load(bad_path)
        except ValidationError:  # pragma: no cover - proves it never escapes
            pytest.fail("raw pydantic.ValidationError leaked past GateDPolicyRegistry.load()")


# ── read-only sub-policy accessors ──────────────────────────────────


def test_citation_policy_accessor(gate_d_policy: GateDPolicyRegistry) -> None:
    citation_policy = gate_d_policy.citation_policy
    assert citation_policy.answered_requires_citation is True
    assert citation_policy.citations_must_be_gate_c_approved is True
    assert citation_policy.reject_mixed_valid_invalid is True


def test_quality_policy_accessor(gate_d_policy: GateDPolicyRegistry) -> None:
    assert gate_d_policy.quality_policy.max_answer_chars == 1600


def test_disclosure_policy_accessor(gate_d_policy: GateDPolicyRegistry) -> None:
    disclosure_policy = gate_d_policy.disclosure_policy
    assert disclosure_policy.enforce_gate_b_profile is True
    assert disclosure_policy.block_secret_patterns is True
    assert disclosure_policy.block_hidden_prompt_markers is True


def test_latency_budgets_accessor(gate_d_policy: GateDPolicyRegistry) -> None:
    latency_budgets = gate_d_policy.latency_budgets
    assert latency_budgets.max_total_latency_ms == 2500
    assert latency_budgets.max_model_latency_ms == 1400
    assert latency_budgets.threshold_is_inclusive is True


def test_safe_failure_accessor(gate_d_policy: GateDPolicyRegistry) -> None:
    safe_failure = gate_d_policy.safe_failure
    assert safe_failure.status == "safe_failure"
    assert safe_failure.code == "FINAL_RESPONSE_REJECTED"
    assert safe_failure.message


def test_accessors_are_stable_and_immutable(gate_d_policy: GateDPolicyRegistry) -> None:
    """`allowed_response_statuses` returns a `tuple` -- itself immutable,
    so a caller can never mutate it regardless of whether two calls
    happen to return the identical object (CPython's `tuple(t)` returns
    `t` unchanged when `t` is already an exact `tuple`, the same
    non-guarantee `GateCPolicyRegistry`'s own `tuple(self._document....)`
    accessors carry) -- read-only in the sense that matters: there is no
    way to reach back into and mutate the loaded document through it."""
    first = gate_d_policy.allowed_response_statuses
    second = gate_d_policy.allowed_response_statuses
    assert first == second == (AnswerStatus.ANSWERED, AnswerStatus.INSUFFICIENT_EVIDENCE)
    assert not hasattr(first, "append")


def test_registry_has_no_mutator_methods(gate_d_policy: GateDPolicyRegistry) -> None:
    """Read-only at runtime (Task 2's own bullet): there is no
    `add_*`/`set_*`/`update_*` method anywhere on this registry."""
    public_methods = {name for name in dir(gate_d_policy) if not name.startswith("_")}
    assert not any(name.startswith(("add_", "set_", "update_", "delete_", "remove_")) for name in public_methods)


# ── governed membership checks ──────────────────────────────────────


def test_allows_response_status(gate_d_policy: GateDPolicyRegistry) -> None:
    assert gate_d_policy.allows_response_status(AnswerStatus.ANSWERED) is True
    assert gate_d_policy.allows_response_status(AnswerStatus.INSUFFICIENT_EVIDENCE) is True


@pytest.mark.parametrize("profile_id", ["policy_reader", "structured_reader", "compliance_view"])
def test_has_disclosure_profile_recognizes_real_gate_b_profiles(
    gate_d_policy: GateDPolicyRegistry, profile_id: str
) -> None:
    """Task 2's own "unknown disclosure profile ... reference rejected" --
    proven here as the positive case: every profile the real committed
    Gate-B policy governs (`policy/gate_b_policy.v1.json`) is recognized."""
    assert gate_d_policy.has_disclosure_profile(profile_id) is True


def test_has_disclosure_profile_rejects_unknown_reference(gate_d_policy: GateDPolicyRegistry) -> None:
    assert gate_d_policy.has_disclosure_profile("no_such_profile") is False


def test_has_disclosure_profile_false_without_supplied_context() -> None:
    """A registry built via the plain constructor, without
    `known_disclosure_profile_ids` supplied, recognizes nothing -- the
    same "no context supplied, check simply does not run" allowance
    `GateCPolicyDocument`'s own optional cross-reference gives, never a
    silent fall-through to "recognized"."""
    document = GateDPolicyDocument.model_validate(_minimal_valid_policy())
    registry = GateDPolicyRegistry(document)
    assert registry.has_disclosure_profile("policy_reader") is False


def test_known_disclosure_profile_ids_matches_has_disclosure_profile(gate_d_policy: GateDPolicyRegistry) -> None:
    """`known_disclosure_profile_ids` -- the property `GateD.evaluate()`
    (Task 10) forwards into `check_final_disclosure()`'s own candidate-
    level cross-check -- is exactly the same governed universe
    `has_disclosure_profile()` already checks membership against, never a
    second, independently-drifting set."""
    known = gate_d_policy.known_disclosure_profile_ids
    assert isinstance(known, frozenset)
    for profile_id in ("policy_reader", "structured_reader", "compliance_view"):
        assert profile_id in known
        assert gate_d_policy.has_disclosure_profile(profile_id) is True
    assert "no_such_profile" not in known
    assert gate_d_policy.has_disclosure_profile("no_such_profile") is False


def test_known_disclosure_profile_ids_empty_without_supplied_context() -> None:
    """Mirrors `test_has_disclosure_profile_false_without_supplied_context`
    one layer over: a registry built via the plain constructor exposes an
    empty set, not a silent fall-through to "every id recognized"."""
    document = GateDPolicyDocument.model_validate(_minimal_valid_policy())
    registry = GateDPolicyRegistry(document)
    assert registry.known_disclosure_profile_ids == frozenset()


def test_load_with_explicit_throwaway_gate_b_policy_narrows_known_profiles(gate_b_policy: PolicyRegistry) -> None:
    """`gate_b_policy` is an explicit parameter precisely so a caller can
    test against a different/throwaway Gate-B policy -- proven by loading
    against the real one and confirming the known-profile universe is
    exactly its own `disclosure_profiles`, nothing invented and nothing
    from some other source."""
    registry = GateDPolicyRegistry.load(gate_b_policy=gate_b_policy)
    real_profile_ids = {profile.profile_id for profile in gate_b_policy.disclosure_profiles}
    assert real_profile_ids  # sanity: the real policy does govern at least one
    for profile_id in real_profile_ids:
        assert registry.has_disclosure_profile(profile_id) is True
    assert registry.has_disclosure_profile("definitely_not_governed") is False


def test_mutating_a_copy_of_loaded_document_does_not_affect_registry(gate_d_policy: GateDPolicyRegistry) -> None:
    """The registry wraps the document it loaded; mutating an unrelated
    deep copy proves accessors read from the registry's own stored state,
    not from some external mutable structure a caller could still hold a
    reference to."""
    unrelated_copy = copy.deepcopy(_minimal_valid_policy())
    unrelated_copy["policy_version"] = "9.9"
    assert gate_d_policy.policy_version == "1.0"
