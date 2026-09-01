"""
Day 4 Task 6 — backward compatibility and versioning.

Uses `data/day04_pack/fixtures/existing_caller_v1.json` - a frozen
snapshot of what an already-deployed v1 caller sends/expects, with no
`warning` key - to prove the one required compatibility case: adding an
optional field to `ResponseEnvelope` after v1 shipped did not break that
caller.

The rest of this file proves, executably, the breaking-change examples
documented in `docs/adr/ADR-004-day4-contract-versioning.md`: removing a
required field, changing a field type incompatibly, making an optional
field required, and an incompatible enum change. Each is shown to fail
validation - either against the real v1 models directly (removing a
field, changing a type - both are things the real model already rejects),
or against a small model declared only in this file and clearly labeled
hypothetical, for the two cases where the real v1 model can't demonstrate
the "before" state because it is already correct (making an optional
field required, renaming an enum value) - neither hypothetical model is
ever applied to `src/aico/contracts/models.py`.
"""
from __future__ import annotations

import copy
import json
from enum import Enum
from pathlib import Path
from typing import Literal, Optional

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aico.contracts.models import (
    CITED_ANSWER_SCHEMA_VERSION,
    RESPONSE_ENVELOPE_SCHEMA_VERSION,
    AnswerStatus,
    Citation,
    CitedAnswer,
    ConfidenceLabel,
    ResponseEnvelope,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO_ROOT / "data" / "day04_pack" / "fixtures" / "existing_caller_v1.json"


def _load_existing_caller_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _sample_response() -> dict:
    # A fresh deep copy every call - tests mutate this freely.
    return copy.deepcopy(_load_existing_caller_fixture()["sample_response"])


# ═══════════════════════════════════════════════════════════════════════
# the required case: adding an optional field stayed backward compatible
# ═══════════════════════════════════════════════════════════════════════

def test_existing_caller_v1_fixture_never_sends_warning():
    # Confirms the premise: this fixture predates `warning` and is a
    # genuine "old caller" snapshot, not one that happens to include it.
    payload = _sample_response()
    assert "warning" not in payload
    assert "trace_id" not in payload


def test_existing_caller_v1_fixture_is_still_valid_after_optional_warning_field_was_added():
    envelope = ResponseEnvelope.model_validate(_sample_response())
    assert envelope.warning is None
    assert envelope.trace_id is None


def test_existing_caller_v1_expected_fields_all_present_and_correct():
    case = _load_existing_caller_fixture()
    payload = case["sample_response"]
    envelope = ResponseEnvelope.model_validate(payload)

    for field_name in case["expected_fields"]:
        assert hasattr(envelope, field_name), f"expected field {field_name!r} missing from ResponseEnvelope"

    assert envelope.schema_version == payload["schema_version"]
    assert envelope.request_id == payload["request_id"]
    assert envelope.model_alias == payload["model_alias"]
    assert envelope.result.status is AnswerStatus(payload["result"]["status"])
    assert envelope.result.answer == payload["result"]["answer"]
    assert [c.chunk_id for c in envelope.result.citations] == [
        c["chunk_id"] for c in payload["result"]["citations"]
    ]


def test_existing_caller_v1_still_valid_even_if_a_new_optional_field_is_sent_too():
    # Compatibility runs both directions: a server that *does* start
    # sending `warning` doesn't break a caller that reads it either.
    payload = _sample_response()
    payload["warning"] = "partial result - low confidence"
    envelope = ResponseEnvelope.model_validate(payload)
    assert envelope.warning == "partial result - low confidence"


def test_compatibility_change_documented_in_fixture_matches_the_real_model():
    case = _load_existing_caller_fixture()
    change = case["compatibility_change_to_prove"]
    assert change["field"] == "warning"
    assert change["change"] == "add_optional_field"
    field_info = ResponseEnvelope.model_fields["warning"]
    assert field_info.is_required() is False


# ═══════════════════════════════════════════════════════════════════════
# schema version appears in output metadata (both contracts)
# ═══════════════════════════════════════════════════════════════════════

def test_schema_version_appears_on_both_typed_contracts():
    envelope = ResponseEnvelope.model_validate(_sample_response())
    assert envelope.schema_version == RESPONSE_ENVELOPE_SCHEMA_VERSION
    assert envelope.result.schema_version == CITED_ANSWER_SCHEMA_VERSION


# ═══════════════════════════════════════════════════════════════════════
# breaking changes: each requires an explicit version decision
# (see docs/adr/ADR-004-day4-contract-versioning.md)
# ═══════════════════════════════════════════════════════════════════════

# ── 1. removing a required field ────────────────────────────────────────

def test_removing_a_required_field_breaks_existing_validation():
    payload = _sample_response()
    del payload["model_alias"]
    with pytest.raises(ValidationError):
        ResponseEnvelope.model_validate(payload)


# ── 2. changing a field type incompatibly ───────────────────────────────

def test_changing_a_field_type_incompatibly_breaks_existing_validation():
    payload = _sample_response()
    payload["result"]["confidence_label"] = 0.87  # was the enum string "medium"
    with pytest.raises(ValidationError):
        ResponseEnvelope.model_validate(payload)


# ── 3. making an optional field required ────────────────────────────────

class _HypotheticalEnvelopeWarningRequired(BaseModel):
    """NOT a real contract - test-only. Demonstrates why promoting an
    optional field to required needs a version bump: `warning` is
    required here (optional in the real v1 `ResponseEnvelope`), so the
    genuine existing-caller-v1 fixture - which never sends it - fails."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    request_id: str = Field(min_length=1)
    result: CitedAnswer
    model_alias: str = Field(min_length=1)
    trace_id: Optional[str] = Field(default=None, min_length=1)
    warning: str = Field(min_length=1)  # required here; Optional in the real contract


def test_making_an_optional_field_required_would_break_existing_callers():
    payload = _sample_response()  # the real, unmodified v1 fixture - never sends `warning`
    with pytest.raises(ValidationError):
        _HypotheticalEnvelopeWarningRequired.model_validate(payload)


# ── 4. an incompatible enum change ──────────────────────────────────────

class _HypotheticalAnswerStatusRenamed(str, Enum):
    """NOT a real contract - test-only. `AnswerStatus.ANSWERED`'s value
    renamed from "answered" to "responded"."""

    RESPONDED = "responded"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class _HypotheticalCitedAnswerRenamedEnum(BaseModel):
    """NOT a real contract - test-only sibling of `CitedAnswer` with a
    renamed enum value, to demonstrate why an incompatible enum change
    needs a version bump."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    status: _HypotheticalAnswerStatusRenamed
    answer: str = Field(min_length=1)
    citations: list[Citation] = Field(default_factory=list)
    confidence_label: ConfidenceLabel


def test_incompatible_enum_change_would_break_existing_callers():
    payload = _sample_response()["result"]  # status == "answered" in the real fixture
    with pytest.raises(ValidationError):
        _HypotheticalCitedAnswerRenamedEnum.model_validate(payload)
