"""
Day 4 Task 1/2 — versioned typed contracts, and contract/schema
validation of raw model output.

Task 1 section proves the acceptance-relevant behaviors of
`src/aico/contracts/models.py` directly against Pydantic (required/
optional fields, enums, constrained values, extra-field rejection,
explicit schema version) and that the committed JSON Schema under
`contracts/schema/` is exactly what `scripts/day04_generate_schemas.py`
would regenerate from the current source models - i.e. it cannot have
drifted.

Task 2 section proves `src/aico/contracts/validator.py` end to end: a raw
model-response *string* (not an already-parsed dict) becomes a typed
contract or a typed `ValidationFailure`, covering every required
rejection (malformed JSON, missing field, extra field, wrong type,
invalid enum, out-of-range value) plus the documented bounded
markdown-fence unwrap. It also runs every relevant case in the supplied
`data/day04_pack/fixtures/structured_output_cases.json` through the real
validator so Task 2's behavior is proven against the assignment's own
fixtures, not only hand-rolled payloads.

Semantic validation (Task 3), repair (Task 4) and the full broken-output
fixture suite (Task 5, including the repair-specific cases) are covered
in their own test files, not here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aico.contracts.errors import ValidationFailure
from aico.contracts.models import (
    CITED_ANSWER_SCHEMA_VERSION,
    RESPONSE_ENVELOPE_SCHEMA_VERSION,
    AnswerStatus,
    Citation,
    CitedAnswer,
    ConfidenceLabel,
    ResponseEnvelope,
)
from aico.contracts.validator import parse_and_validate, parse_json, validate_contract

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = REPO_ROOT / "contracts" / "schema"
FIXTURES_PATH = REPO_ROOT / "data" / "day04_pack" / "fixtures" / "structured_output_cases.json"


def _load_fixture_cases() -> dict:
    cases = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))["cases"]
    return {case["id"]: case for case in cases}


FIXTURE_CASES = _load_fixture_cases()


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


# ═══════════════════════════════════════════════════════════════════════
# TASK 2 — contract / schema validation of raw model output
# ═══════════════════════════════════════════════════════════════════════

# ── parse_json: malformed JSON, non-object JSON, markdown-fence unwrap ──

def test_parse_json_valid_object_returns_dict():
    result = parse_json('{"a": 1}')
    assert result == {"a": 1}


def test_parse_json_malformed_json_is_a_typed_parse_failure():
    result = parse_json('{"schema_version":"1.0","status":"answered",')
    assert isinstance(result, ValidationFailure)
    assert result.stage == "parse"
    assert result.category == "malformed_json"


def test_parse_json_non_object_json_is_a_typed_parse_failure():
    # A syntactically valid JSON array is still not a Day 4 contract shape.
    result = parse_json("[1, 2, 3]")
    assert isinstance(result, ValidationFailure)
    assert result.stage == "parse"
    assert result.category == "malformed_json"


def test_parse_json_unwraps_one_documented_markdown_fence():
    raw = '```json\n{"a": 1}\n```'
    result = parse_json(raw)
    assert result == {"a": 1}


def test_parse_json_rejects_prose_around_a_fence():
    # Not the documented bounded unwrap - text outside the fence besides
    # whitespace must not be silently accepted.
    raw = 'Here you go:\n```json\n{"a": 1}\n```\nHope that helps!'
    result = parse_json(raw)
    assert isinstance(result, ValidationFailure)
    assert result.stage == "parse"


def test_parse_json_rejects_non_json_inside_a_fence():
    raw = "```json\nnot json at all\n```"
    result = parse_json(raw)
    assert isinstance(result, ValidationFailure)
    assert result.stage == "parse"
    assert result.category == "malformed_json"


# ── validate_contract: category classification on an already-parsed dict ──

def test_validate_contract_valid_dict_returns_typed_contract():
    result = validate_contract(_valid_cited_answer(), CitedAnswer)
    assert isinstance(result, CitedAnswer)


@pytest.mark.parametrize(
    "mutate,expected_category",
    [
        (lambda p: p.pop("answer"), "missing_field"),
        (lambda p: p.update(unexpected=True), "extra_field"),
        (lambda p: p.update(answer=123), "wrong_type"),
        (lambda p: p.update(status="maybe"), "invalid_enum"),
        (lambda p: p.update(citations=[{"chunk_id": "", "source_file": "DOC-001.md"}]), "out_of_range"),
    ],
    ids=["missing_field", "extra_field", "wrong_type", "invalid_enum", "out_of_range"],
)
def test_validate_contract_categorizes_each_required_rejection(mutate, expected_category):
    payload = _valid_cited_answer()
    mutate(payload)
    result = validate_contract(payload, CitedAnswer)
    assert isinstance(result, ValidationFailure)
    assert result.stage == "contract"
    assert result.category == expected_category


def test_validate_contract_failure_carries_field_path_when_relevant():
    payload = _valid_cited_answer()
    payload["citations"] = [{"chunk_id": "", "source_file": "DOC-001.md"}]
    result = validate_contract(payload, CitedAnswer)
    assert isinstance(result, ValidationFailure)
    assert result.field_path == "citations.0.chunk_id"


def test_validate_contract_failure_message_never_contains_raw_payload_values():
    # The safe message names *what* was wrong, never echoes the model's
    # own submitted string value back out - see errors.py's module
    # docstring on not logging full invalid model responses.
    payload = _valid_cited_answer()
    payload["answer"] = "SECRET-MARKER-DO-NOT-LEAK"
    payload["confidence_label"] = "extreme"
    result = validate_contract(payload, CitedAnswer)
    assert isinstance(result, ValidationFailure)
    assert "SECRET-MARKER-DO-NOT-LEAK" not in result.message
    assert "extreme" not in result.message


# ── parse_and_validate: the full Task 2 pipeline, raw string in ────────

def test_parse_and_validate_valid_raw_string_returns_typed_contract():
    raw = json.dumps(_valid_cited_answer())
    result = parse_and_validate(raw, CitedAnswer)
    assert isinstance(result, CitedAnswer)


def test_parse_and_validate_malformed_json_short_circuits_at_parse_stage():
    # Never reaches Pydantic - a parse failure is reported as "parse",
    # not miscategorized as a contract failure.
    result = parse_and_validate('{"not": "closed"', CitedAnswer)
    assert isinstance(result, ValidationFailure)
    assert result.stage == "parse"


def test_parse_and_validate_well_formed_but_invalid_shape_is_a_contract_failure():
    raw = json.dumps({**_valid_cited_answer(), "status": "maybe"})
    result = parse_and_validate(raw, CitedAnswer)
    assert isinstance(result, ValidationFailure)
    assert result.stage == "contract"
    assert result.category == "invalid_enum"


# ── driven by the supplied structured_output_cases.json fixtures ───────

@pytest.mark.parametrize(
    "case_id,expected_category",
    [
        ("D04-04", "missing_field"),
        ("D04-05", "extra_field"),
        ("D04-06", "wrong_type"),
        ("D04-07", "invalid_enum"),
        ("D04-08", "out_of_range"),
    ],
)
def test_fixture_contract_failures_are_categorized_correctly(case_id, expected_category):
    raw = FIXTURE_CASES[case_id]["raw"]
    result = parse_and_validate(raw, CitedAnswer)
    assert isinstance(result, ValidationFailure)
    assert result.stage == "contract"
    assert result.category == expected_category


def test_fixture_d04_01_valid_first_pass_becomes_typed_contract():
    raw = FIXTURE_CASES["D04-01"]["raw"]
    result = parse_and_validate(raw, CitedAnswer)
    assert isinstance(result, CitedAnswer)


def test_fixture_d04_02_malformed_json_is_a_parse_failure():
    raw = FIXTURE_CASES["D04-02"]["raw"]
    result = parse_and_validate(raw, CitedAnswer)
    assert isinstance(result, ValidationFailure)
    assert result.stage == "parse"


def test_fixture_d04_03_markdown_wrapped_json_is_unwrapped_and_validates():
    # This module's documented choice for the "parse_or_documented_unwrap"
    # fixture case: support one bounded fence unwrap rather than reject.
    raw = FIXTURE_CASES["D04-03"]["raw"]
    result = parse_and_validate(raw, CitedAnswer)
    assert isinstance(result, CitedAnswer)


@pytest.mark.parametrize("case_id", ["D04-09", "D04-10"])
def test_fixture_semantic_cases_are_schema_valid_at_the_contract_stage(case_id):
    # D04-09/D04-10 are schema-valid but semantically invalid (Task 3
    # rules S1/S2) - proving that split is this test's job; Task 2's
    # validator must pass them through as typed contracts. The semantic
    # rejection itself is asserted in test_day04_semantic_validation.py.
    raw = FIXTURE_CASES[case_id]["raw"]
    result = parse_and_validate(raw, CitedAnswer)
    assert isinstance(result, CitedAnswer)


@pytest.mark.parametrize("case_id", ["D04-11", "D04-12"])
def test_fixture_repair_source_cases_fail_contract_validation(case_id):
    # D04-11/D04-12 are the *first* (pre-repair) responses Task 4's
    # bounded repair path is exercised against - both must fail contract
    # validation here for repair to have something to repair.
    raw = FIXTURE_CASES[case_id]["raw"]
    result = parse_and_validate(raw, CitedAnswer)
    assert isinstance(result, ValidationFailure)
    assert result.stage == "contract"
