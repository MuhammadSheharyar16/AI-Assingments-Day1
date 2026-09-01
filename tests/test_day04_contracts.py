"""
Day 4 Task 1 — versioned typed contracts.

Proves the acceptance-relevant behaviors of `src/aico/contracts/models.py`
directly against Pydantic (required/optional fields, enums, constrained
values, extra-field rejection, explicit schema version) and that the
committed JSON Schema under `contracts/schema/` is exactly what
`scripts/day04_generate_schemas.py` would regenerate from the current
source models - i.e. it cannot have drifted.

Contract/schema *validation of raw model output* (Task 2), semantic
validation (Task 3), repair (Task 4) and the broken-output fixture suite
(Task 5) are covered in their own test files, not here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

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
SCHEMA_DIR = REPO_ROOT / "contracts" / "schema"


def _valid_citation() -> dict:
    return {"chunk_id": "CHK-001", "source_file": "DOC-001.md"}


def _valid_cited_answer() -> dict:
    return {
        "schema_version": "1.0",
        "status": "answered",
        "answer": "Supplier insurance is required.",
        "citations": [_valid_citation()],
        "confidence_label": "medium",
    }


def _valid_envelope() -> dict:
    return {
        "schema_version": "1.0",
        "request_id": "REQ-001",
        "result": _valid_cited_answer(),
        "model_alias": "chat-primary",
    }


# ── required / optional fields ──────────────────────────────────────────

def test_cited_answer_valid_payload_becomes_typed_object():
    answer = CitedAnswer.model_validate(_valid_cited_answer())
    assert isinstance(answer, CitedAnswer)
    assert answer.status is AnswerStatus.ANSWERED
    assert answer.confidence_label is ConfidenceLabel.MEDIUM
    assert answer.citations == [Citation(**_valid_citation())]


@pytest.mark.parametrize(
    "missing_field", ["schema_version", "status", "answer", "confidence_label"]
)
def test_cited_answer_missing_required_field_is_rejected(missing_field):
    payload = _valid_cited_answer()
    del payload[missing_field]
    with pytest.raises(ValidationError):
        CitedAnswer.model_validate(payload)


def test_cited_answer_citations_defaults_to_empty_list_when_omitted():
    payload = _valid_cited_answer()
    del payload["citations"]
    answer = CitedAnswer.model_validate(payload)
    assert answer.citations == []


def test_response_envelope_valid_payload_becomes_typed_object():
    envelope = ResponseEnvelope.model_validate(_valid_envelope())
    assert isinstance(envelope, ResponseEnvelope)
    assert isinstance(envelope.result, CitedAnswer)
    assert envelope.trace_id is None
    assert envelope.warning is None


@pytest.mark.parametrize(
    "missing_field", ["schema_version", "request_id", "result", "model_alias"]
)
def test_response_envelope_missing_required_field_is_rejected(missing_field):
    payload = _valid_envelope()
    del payload[missing_field]
    with pytest.raises(ValidationError):
        ResponseEnvelope.model_validate(payload)


def test_response_envelope_optional_fields_accepted_when_present():
    payload = _valid_envelope()
    payload["trace_id"] = "TRACE-001"
    payload["warning"] = "partial result"
    envelope = ResponseEnvelope.model_validate(payload)
    assert envelope.trace_id == "TRACE-001"
    assert envelope.warning == "partial result"


def test_response_envelope_optional_fields_omitted_still_validates():
    # Mirrors data/day04_pack/fixtures/existing_caller_v1.json: no `warning`.
    envelope = ResponseEnvelope.model_validate(_valid_envelope())
    assert envelope.warning is None


# ── enums ────────────────────────────────────────────────────────────────

def test_cited_answer_invalid_status_enum_is_rejected():
    payload = _valid_cited_answer()
    payload["status"] = "maybe"
    with pytest.raises(ValidationError):
        CitedAnswer.model_validate(payload)


def test_cited_answer_invalid_confidence_enum_is_rejected():
    payload = _valid_cited_answer()
    payload["confidence_label"] = "extreme"
    with pytest.raises(ValidationError):
        CitedAnswer.model_validate(payload)


def test_cited_answer_insufficient_evidence_status_accepted():
    payload = _valid_cited_answer()
    payload["status"] = "insufficient_evidence"
    payload["citations"] = []
    answer = CitedAnswer.model_validate(payload)
    assert answer.status is AnswerStatus.INSUFFICIENT_EVIDENCE


# ── constrained / out-of-range values ───────────────────────────────────

def test_citation_empty_chunk_id_is_rejected():
    with pytest.raises(ValidationError):
        Citation.model_validate({"chunk_id": "", "source_file": "DOC-001.md"})


def test_citation_empty_source_file_is_rejected():
    with pytest.raises(ValidationError):
        Citation.model_validate({"chunk_id": "CHK-001", "source_file": ""})


def test_cited_answer_empty_answer_text_is_rejected():
    payload = _valid_cited_answer()
    payload["answer"] = ""
    with pytest.raises(ValidationError):
        CitedAnswer.model_validate(payload)


def test_response_envelope_empty_request_id_is_rejected():
    payload = _valid_envelope()
    payload["request_id"] = ""
    with pytest.raises(ValidationError):
        ResponseEnvelope.model_validate(payload)


# ── wrong types ──────────────────────────────────────────────────────────

def test_cited_answer_wrong_type_for_answer_is_rejected():
    payload = _valid_cited_answer()
    payload["answer"] = 123
    with pytest.raises(ValidationError):
        CitedAnswer.model_validate(payload)


# ── extra/unknown fields rejected everywhere ────────────────────────────

def test_citation_extra_field_is_rejected():
    payload = {**_valid_citation(), "unexpected": True}
    with pytest.raises(ValidationError):
        Citation.model_validate(payload)


def test_cited_answer_extra_field_is_rejected():
    payload = {**_valid_cited_answer(), "unexpected": True}
    with pytest.raises(ValidationError):
        CitedAnswer.model_validate(payload)


def test_response_envelope_extra_field_is_rejected():
    payload = {**_valid_envelope(), "unexpected": True}
    with pytest.raises(ValidationError):
        ResponseEnvelope.model_validate(payload)


# ── explicit schema version ─────────────────────────────────────────────

def test_cited_answer_wrong_schema_version_literal_is_rejected():
    payload = _valid_cited_answer()
    payload["schema_version"] = "2.0"
    with pytest.raises(ValidationError):
        CitedAnswer.model_validate(payload)


def test_response_envelope_wrong_schema_version_literal_is_rejected():
    payload = _valid_envelope()
    payload["schema_version"] = "2.0"
    with pytest.raises(ValidationError):
        ResponseEnvelope.model_validate(payload)


def test_schema_version_constants_match_literal_values():
    assert CITED_ANSWER_SCHEMA_VERSION == "1.0"
    assert RESPONSE_ENVELOPE_SCHEMA_VERSION == "1.0"


# ── generated schema matches the source model (no drift) ───────────────

@pytest.mark.parametrize(
    "filename,model",
    [
        ("cited_answer.v1.schema.json", CitedAnswer),
        ("response_envelope.v1.schema.json", ResponseEnvelope),
    ],
)
def test_committed_schema_matches_generated_source_model(filename, model):
    committed = json.loads((SCHEMA_DIR / filename).read_text(encoding="utf-8"))
    regenerated = json.loads(json.dumps(model.model_json_schema(), sort_keys=True))
    committed_sorted = json.loads(json.dumps(committed, sort_keys=True))
    assert committed_sorted == regenerated, (
        f"{filename} is out of date - re-run "
        f"`python scripts/day04_generate_schemas.py` and commit the result"
    )
