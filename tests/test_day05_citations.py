"""
Day 5 Task 3 — citation validation.

Proves `aico.rag.citation_validator.validate_citations` directly against
the supplied fixture pack (data/day05_pack/citation_cases.json - CIT-001..004)
plus the edge cases the brief explicitly warns about:

    - a citation is checked by *membership* against the chunk IDs actually
      retrieved this turn, never by whether the string merely *looks like*
      a chunk ID (working rule / common cause of failure list)
    - a forged citation is never silently dropped while the rest of the
      answer is still trusted - the whole result fails closed
    - every cited ID is validated, not just the first/last one

`test_day05_grounding.py` additionally proves the validator is actually
wired into `GroundedAnswerService` (Task 1) and fails the whole answer
closed end to end - this file is the validator in isolation.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from aico.rag.citation_validator import EvidenceChunk, validate_citations

PACK_DIR = pathlib.Path(__file__).resolve().parent.parent / "data" / "day05_pack"
CITATION_CASES = json.loads((PACK_DIR / "citation_cases.json").read_text(encoding="utf-8"))["cases"]


def _retrieved(chunk_ids: list[str]) -> list[EvidenceChunk]:
    # Only chunk_id is under test here - source_file/text are irrelevant to
    # membership, so any synthetic placeholder is fine.
    return [EvidenceChunk(chunk_id=cid, source_file="synthetic.md", text="synthetic evidence text") for cid in chunk_ids]


# ── Supplied fixture pack (data/day05_pack/citation_cases.json) ─────────────

@pytest.mark.parametrize("case", CITATION_CASES, ids=[c["id"] for c in CITATION_CASES])
def test_supplied_citation_cases_produce_their_expected_outcome(case):
    result = validate_citations(case["model_citations"], _retrieved(case["retrieved_context_ids"]))
    expected_valid = case["expected"] == "pass"
    assert result.valid is expected_valid, (
        f"{case['id']} ({case['name']}): expected {case['expected']!r}, got "
        f"{'pass' if result.valid else 'fail_closed'}"
    )


def test_citation_case_pack_has_the_four_documented_cases():
    ids = {c["id"] for c in CITATION_CASES}
    assert {"CIT-001", "CIT-002", "CIT-003", "CIT-004"} <= ids


# ── Focused behavior proofs ──────────────────────────────────────────────

def test_single_valid_citation_passes():
    result = validate_citations(["CHUNK-001"], _retrieved(["CHUNK-001", "CHUNK-004"]))
    assert result.valid is True
    assert result.forged_citation_ids == ()


def test_every_cited_id_is_checked_not_just_the_first():
    # Two valid + one forged - the two valid ones must not mask the forged
    # one ("every cited chunk ID must belong to the retrieved context").
    result = validate_citations(
        ["CHUNK-001", "CHUNK-999", "CHUNK-004"], _retrieved(["CHUNK-001", "CHUNK-004"])
    )
    assert result.valid is False
    assert result.forged_citation_ids == ("CHUNK-999",)


def test_forged_citation_is_reported_not_silently_dropped():
    # Common cause of failure: "deleting a forged citation and still
    # returning the answer as trusted." The validator must report the
    # forged id and the *original, unfiltered* cited_ids - never quietly
    # trim the list to just what happened to be real.
    result = validate_citations(["CHUNK-001", "CHUNK-999"], _retrieved(["CHUNK-001", "CHUNK-004"]))
    assert result.valid is False
    assert result.cited_ids == ("CHUNK-001", "CHUNK-999")  # nothing removed
    assert "CHUNK-999" in result.forged_citation_ids


def test_membership_is_checked_against_actual_retrieved_ids_not_id_shape():
    # "CHUNK-002" is exactly the right shape/format for a chunk ID but was
    # never retrieved this turn - format validity must not substitute for
    # real membership (working rule / common cause of failure list).
    result = validate_citations(["CHUNK-002"], _retrieved(["CHUNK-001", "CHUNK-004"]))
    assert result.valid is False
    assert result.forged_citation_ids == ("CHUNK-002",)


def test_no_citations_is_trivially_valid():
    # An empty citation list is a vacuous subset of the retrieved context -
    # nothing to forge, so it passes (a model that cites nothing is a
    # different problem, handled by semantic/insufficient-evidence rules,
    # not citation validation).
    result = validate_citations([], _retrieved(["CHUNK-001", "CHUNK-004"]))
    assert result.valid is True
    assert result.forged_citation_ids == ()


def test_empty_retrieved_context_fails_any_citation_closed():
    # Nothing was retrieved this turn, so no chunk_id can ever be a valid
    # citation - even one that would otherwise look plausible.
    result = validate_citations(["CHUNK-001"], _retrieved([]))
    assert result.valid is False
    assert result.forged_citation_ids == ("CHUNK-001",)
    assert result.retrieved_ids == ()


def test_duplicate_valid_citations_remain_valid():
    result = validate_citations(["CHUNK-001", "CHUNK-001"], _retrieved(["CHUNK-001", "CHUNK-004"]))
    assert result.valid is True
    assert result.cited_ids == ("CHUNK-001", "CHUNK-001")


def test_all_forged_citations_are_listed_not_just_one():
    result = validate_citations(["CHUNK-777", "CHUNK-888"], _retrieved(["CHUNK-001"]))
    assert result.valid is False
    assert set(result.forged_citation_ids) == {"CHUNK-777", "CHUNK-888"}


def test_result_records_the_retrieved_context_it_validated_against():
    # The result should be self-describing enough to audit/log (Task 8/9
    # artifacts) without a caller having to keep the original retrieval
    # list around separately.
    result = validate_citations(["CHUNK-004"], _retrieved(["CHUNK-001", "CHUNK-004"]))
    assert result.retrieved_ids == ("CHUNK-001", "CHUNK-004")
