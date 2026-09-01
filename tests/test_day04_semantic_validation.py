"""
Day 4 Task 3 — semantic validation.

Proves `src/aico/contracts/semantic.py` end to end against
`data/day04_pack/semantic_rules.md`'s five rules (S1-S5): each rule
rejects exactly the case it names and accepts everything else, semantic
validation is provably kept separate from Task 2's contract/schema
validation (same `ValidationFailure` type, distinguishable only by
`stage`), it never mutates its input, and it evaluates in a fixed,
deterministic order. Also runs the supplied semantic fixtures (D04-09,
D04-10) end to end through the full parse -> contract -> semantic
pipeline - each one parses, passes contract/schema validation, and only
fails at the semantic stage, which is the one fixture Task 3 explicitly
requires.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from aico.contracts.errors import ValidationFailure
from aico.contracts.models import AnswerStatus, Citation, CitedAnswer, ConfidenceLabel
from aico.contracts.semantic import validate_semantic
from aico.contracts.validator import parse_and_validate

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_PATH = REPO_ROOT / "data" / "day04_pack" / "fixtures" / "structured_output_cases.json"


def _load_fixture_cases() -> dict:
    cases = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))["cases"]
    return {case["id"]: case for case in cases}


FIXTURE_CASES = _load_fixture_cases()


def _answered(**overrides) -> CitedAnswer:
    payload = dict(
        schema_version="1.0",
        status=AnswerStatus.ANSWERED,
        answer="Supplier insurance is required.",
        citations=[Citation(chunk_id="CHK-001", source_file="DOC-001.md")],
        confidence_label=ConfidenceLabel.MEDIUM,
    )
    payload.update(overrides)
    return CitedAnswer(**payload)


def _insufficient(**overrides) -> CitedAnswer:
    payload = dict(
        schema_version="1.0",
        status=AnswerStatus.INSUFFICIENT_EVIDENCE,
        answer="INSUFFICIENT_EVIDENCE: source does not answer this.",
        citations=[],
        confidence_label=ConfidenceLabel.LOW,
    )
    payload.update(overrides)
    return CitedAnswer(**payload)


# ── baseline: a fully valid response of each status passes unchanged ───

def test_valid_answered_response_passes_semantic_validation():
    answer = _answered()
    result = validate_semantic(answer)
    assert result is answer  # same object - never a copy


def test_valid_insufficient_evidence_response_passes_semantic_validation():
    answer = _insufficient()
    assert validate_semantic(answer) is answer


# ── S1 — answered requires at least one citation ────────────────────────

def test_s1_answered_without_citation_fails():
    answer = _answered(citations=[])
    result = validate_semantic(answer)
    assert isinstance(result, ValidationFailure)
    assert result.stage == "semantic"
    assert result.category == "s1_answered_without_citation"


def test_s1_answered_with_citation_passes():
    answer = _answered(citations=[Citation(chunk_id="CHK-999", source_file="DOC-002.md")])
    assert validate_semantic(answer) is answer


# ── S2 — insufficient_evidence must not claim high confidence ──────────

def test_s2_insufficient_evidence_high_confidence_fails():
    answer = _insufficient(confidence_label=ConfidenceLabel.HIGH)
    result = validate_semantic(answer)
    assert isinstance(result, ValidationFailure)
    assert result.category == "s2_insufficient_evidence_high_confidence"


@pytest.mark.parametrize("label", [ConfidenceLabel.LOW, ConfidenceLabel.MEDIUM])
def test_s2_insufficient_evidence_non_high_confidence_passes(label):
    answer = _insufficient(confidence_label=label)
    assert validate_semantic(answer) is answer


# ── S3 — citation chunk_ids must be unique ──────────────────────────────

def test_s3_duplicate_chunk_id_fails():
    answer = _answered(
        citations=[
            Citation(chunk_id="CHK-001", source_file="DOC-001.md"),
            Citation(chunk_id="CHK-001", source_file="DOC-002.md"),
        ]
    )
    result = validate_semantic(answer)
    assert isinstance(result, ValidationFailure)
    assert result.category == "s3_duplicate_citation"
    assert result.field_path == "citations.1.chunk_id"


def test_s3_distinct_chunk_ids_pass():
    answer = _answered(
        citations=[
            Citation(chunk_id="CHK-001", source_file="DOC-001.md"),
            Citation(chunk_id="CHK-002", source_file="DOC-002.md"),
        ]
    )
    assert validate_semantic(answer) is answer


# ── S4 — insufficient_evidence must carry no citations ──────────────────

def test_s4_insufficient_evidence_with_citations_fails():
    answer = _insufficient(citations=[Citation(chunk_id="CHK-001", source_file="DOC-001.md")])
    result = validate_semantic(answer)
    assert isinstance(result, ValidationFailure)
    assert result.category == "s4_insufficient_evidence_with_citations"


def test_s4_insufficient_evidence_without_citations_passes():
    answer = _insufficient(citations=[])
    assert validate_semantic(answer) is answer


# ── S5 — answer text must agree with status ─────────────────────────────

def test_s5_answered_with_insufficient_evidence_prefix_fails():
    answer = _answered(answer="INSUFFICIENT_EVIDENCE: actually I do know.")
    result = validate_semantic(answer)
    assert isinstance(result, ValidationFailure)
    assert result.category == "s5_answer_status_mismatch"


def test_s5_insufficient_evidence_without_prefix_fails():
    answer = _insufficient(answer="I don't know.")
    result = validate_semantic(answer)
    assert isinstance(result, ValidationFailure)
    assert result.category == "s5_answer_status_mismatch"


def test_s5_answered_without_prefix_passes():
    answer = _answered(answer="A plain answer.")
    assert validate_semantic(answer) is answer


# ── deterministic rule order: first violation in S1..S5 order wins ─────

def test_multiple_violations_report_the_first_rule_in_order():
    # Violates both S1 (no citation) and S5 (answered text carries the
    # insufficient-evidence marker) - S1 must win, since rules run in
    # documented S1..S5 order.
    answer = _answered(citations=[], answer="INSUFFICIENT_EVIDENCE: whoops.")
    result = validate_semantic(answer)
    assert isinstance(result, ValidationFailure)
    assert result.category == "s1_answered_without_citation"


# ── semantic validation never silently "fixes" its input ───────────────

def test_semantic_validation_never_mutates_a_failing_input():
    answer = _answered(citations=[])
    before = copy.deepcopy(answer)
    validate_semantic(answer)
    assert answer == before


def test_semantic_validation_returns_the_identical_object_on_success():
    answer = _answered()
    assert validate_semantic(answer) is answer


# ── distinguishable from a contract/schema failure ──────────────────────

def test_semantic_failure_and_contract_failure_are_distinguishable_by_stage():
    contract_invalid_raw = json.dumps(
        {
            "schema_version": "1.0",
            "status": "maybe",  # invalid enum -> contract-stage failure
            "answer": "x",
            "citations": [],
            "confidence_label": "medium",
        }
    )
    contract_failure = parse_and_validate(contract_invalid_raw, CitedAnswer)
    semantic_failure = validate_semantic(_answered(citations=[]))

    assert isinstance(contract_failure, ValidationFailure)
    assert isinstance(semantic_failure, ValidationFailure)
    assert contract_failure.stage == "contract"
    assert semantic_failure.stage == "semantic"
    assert contract_failure.stage != semantic_failure.stage


# ── the required fixture: parses, passes schema, fails semantic ────────

@pytest.mark.parametrize(
    "case_id,expected_category",
    [
        ("D04-09", "s1_answered_without_citation"),
        ("D04-10", "s2_insufficient_evidence_high_confidence"),
    ],
)
def test_fixture_passes_contract_but_fails_semantic(case_id, expected_category):
    raw = FIXTURE_CASES[case_id]["raw"]

    contract_result = parse_and_validate(raw, CitedAnswer)
    assert isinstance(contract_result, CitedAnswer), (
        f"{case_id} must parse and pass contract/schema validation to prove "
        f"the schema-valid-but-semantically-invalid split Task 3 requires"
    )

    semantic_result = validate_semantic(contract_result)
    assert isinstance(semantic_result, ValidationFailure)
    assert semantic_result.stage == "semantic"
    assert semantic_result.category == expected_category
