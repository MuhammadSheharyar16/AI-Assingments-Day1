"""
Day 7 Task 2 — golden dataset schema tests.

Two groups:
1. Tests against the real `evals/golden_v1.json` (Task 1's committed
   dataset) — required-field/category/split coverage, minimum case count,
   unique IDs, no cross-split leakage. These are a regression check on the
   committed file itself, in the same spirit as test_day01_eval.py running
   against the real corpus/queries.
2. Tests against hand-built dicts via `parse_dataset` — prove the loader
   actually *rejects* every documented failure mode (missing field, bad
   enum value, duplicate ID, below the minimum count, missing category),
   not just that the real file happens to pass.
"""
import json
import pathlib

import pytest

from aico.evals.dataset import (
    MIN_CASES,
    REQUIRED_CATEGORIES,
    VALID_ANSWERABILITY,
    VALID_SPLITS,
    DatasetValidationError,
    category_counts,
    duplicate_case_ids,
    load_dataset,
    missing_categories,
    parse_dataset,
    split_counts,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
GOLDEN_PATH = REPO_ROOT / "evals" / "golden_v1.json"

DATASET = load_dataset(GOLDEN_PATH)


# ── Real committed dataset ──────────────────────────────────────────────

def test_real_dataset_loads_without_raising():
    # load_dataset already ran at module scope above; this just documents
    # the expectation for a reader of this file.
    assert DATASET is not None


def test_minimum_case_count_is_met():
    assert len(DATASET) >= MIN_CASES == 25


def test_every_required_category_is_represented():
    assert missing_categories(DATASET.cases) == []
    present = {c.category for c in DATASET.cases}
    assert present == REQUIRED_CATEGORIES


def test_no_duplicate_case_ids():
    assert duplicate_case_ids(DATASET.cases) == []


def test_every_case_has_a_valid_split():
    for c in DATASET.cases:
        assert c.split in VALID_SPLITS, f"{c.case_id} has invalid split {c.split!r}"


def test_every_case_has_a_valid_answerability():
    for c in DATASET.cases:
        assert c.answerability in VALID_ANSWERABILITY, f"{c.case_id} has invalid answerability {c.answerability!r}"


def test_every_case_has_a_non_empty_question():
    for c in DATASET.cases:
        assert c.question.strip(), f"{c.case_id} has an empty question"


def test_expected_sources_have_non_empty_doc_id_and_anchor():
    for c in DATASET.cases:
        for src in c.expected_sources:
            assert src.doc_id.strip()
            assert src.anchor.strip()


def test_adversarial_and_unanswerable_cases_assert_nothing():
    # Design invariant from evals/README.md: a case the system must refuse
    # or cannot answer should never carry a critical_fact to assert.
    for c in DATASET.cases:
        if c.category in ("adversarial", "unanswerable"):
            assert c.critical_facts == ()


def test_no_cross_split_question_leakage_in_committed_dataset():
    from aico.evals.dataset import cross_split_question_leakage
    assert cross_split_question_leakage(DATASET.cases) == []


def test_declared_counts_match_computed_counts():
    # golden_v1.json embeds its own "counts" block (Task 1) - this guards
    # that block against silently drifting from the actual case list.
    declared = DATASET.raw["counts"]
    assert declared["total_cases"] == len(DATASET)
    assert declared["by_split"] == split_counts(DATASET)
    assert declared["by_category"] == category_counts(DATASET)


# ── Loader validation, against hand-built dicts ─────────────────────────

def _valid_case(**overrides) -> dict:
    case = {
        "case_id": "T-001",
        "category": "answerable",
        "split": "train",
        "question": "What is the payment term?",
        "expected_sources": [{"doc_id": "DOC-003", "anchor": "net thirty days"}],
        "answerability": "answerable",
        "critical_facts": ["Payment is due in thirty days"],
        "prohibited_claims": [],
    }
    case.update(overrides)
    return case


def _dataset_with(cases: list[dict]) -> dict:
    return {"version": "1.0", "dataset_id": "test", "corpus": ["DOC-001"], "matching_rule": "", "cases": cases}


def _all_categories_cases(base_id: str = "C") -> list[dict]:
    """MIN_CASES cases covering every required category and every split,
    used as a baseline a single test then breaks one way at a time."""
    cases = []
    categories = sorted(REQUIRED_CATEGORIES)
    splits = sorted(VALID_SPLITS)
    for i in range(MIN_CASES):
        cases.append(_valid_case(
            case_id=f"{base_id}-{i:03d}",
            category=categories[i % len(categories)],
            split=splits[i % len(splits)],
            question=f"Unique question number {i}?",
        ))
    return cases


def test_parse_dataset_accepts_a_well_formed_minimal_dataset():
    dataset = parse_dataset(_dataset_with(_all_categories_cases()))
    assert len(dataset) == MIN_CASES


def test_missing_required_field_is_rejected():
    case = _valid_case()
    del case["critical_facts"]
    cases = _all_categories_cases()
    cases[0] = case
    with pytest.raises(DatasetValidationError) as exc_info:
        parse_dataset(_dataset_with(cases))
    assert any("critical_facts" in p for p in exc_info.value.problems)


def test_invalid_category_is_rejected():
    cases = _all_categories_cases()
    cases[0] = _valid_case(case_id=cases[0]["case_id"], category="not_a_real_category")
    with pytest.raises(DatasetValidationError) as exc_info:
        parse_dataset(_dataset_with(cases))
    assert any("category" in p for p in exc_info.value.problems)


def test_invalid_split_is_rejected():
    cases = _all_categories_cases()
    cases[0] = _valid_case(case_id=cases[0]["case_id"], split="testing")
    with pytest.raises(DatasetValidationError) as exc_info:
        parse_dataset(_dataset_with(cases))
    assert any("split" in p for p in exc_info.value.problems)


def test_invalid_answerability_is_rejected():
    cases = _all_categories_cases()
    cases[0] = _valid_case(case_id=cases[0]["case_id"], answerability="maybe")
    with pytest.raises(DatasetValidationError) as exc_info:
        parse_dataset(_dataset_with(cases))
    assert any("answerability" in p for p in exc_info.value.problems)


def test_duplicate_case_id_is_rejected():
    cases = _all_categories_cases()
    cases[1] = _valid_case(case_id=cases[0]["case_id"], question="A different question entirely")
    with pytest.raises(DatasetValidationError) as exc_info:
        parse_dataset(_dataset_with(cases))
    assert any("duplicate case_id" in p for p in exc_info.value.problems)


def test_below_minimum_case_count_is_rejected():
    cases = _all_categories_cases()[:MIN_CASES - 1]
    with pytest.raises(DatasetValidationError) as exc_info:
        parse_dataset(_dataset_with(cases))
    assert any("fewer than the required minimum" in p for p in exc_info.value.problems)


def test_missing_category_coverage_is_rejected():
    cases = [c for c in _all_categories_cases() if c["category"] != "adversarial"]
    # pad back up to the minimum count with an already-covered category so
    # this test isolates the missing-category problem, not the count one.
    while len(cases) < MIN_CASES:
        cases.append(_valid_case(case_id=f"PAD-{len(cases)}", category="answerable", question=f"Padding question {len(cases)}?"))
    with pytest.raises(DatasetValidationError) as exc_info:
        parse_dataset(_dataset_with(cases))
    assert any("adversarial" in p for p in exc_info.value.problems)


def test_empty_split_is_rejected():
    cases = [c for c in _all_categories_cases()]
    for c in cases:
        if c["split"] == "holdout":
            c["split"] = "train"
    with pytest.raises(DatasetValidationError) as exc_info:
        parse_dataset(_dataset_with(cases))
    assert any("holdout" in p and "zero cases" in p for p in exc_info.value.problems)


def test_malformed_expected_source_is_rejected():
    cases = _all_categories_cases()
    cases[0] = _valid_case(case_id=cases[0]["case_id"], expected_sources=[{"doc_id": "DOC-001"}])
    with pytest.raises(DatasetValidationError) as exc_info:
        parse_dataset(_dataset_with(cases))
    assert any("expected_sources[0]" in p for p in exc_info.value.problems)


def test_all_problems_are_reported_at_once_not_just_the_first():
    cases = _all_categories_cases()
    del cases[0]["question"]
    cases[1] = _valid_case(case_id=cases[1]["case_id"], split="nowhere")
    with pytest.raises(DatasetValidationError) as exc_info:
        parse_dataset(_dataset_with(cases))
    assert len(exc_info.value.problems) >= 2


def test_load_dataset_reads_from_a_file(tmp_path):
    path = tmp_path / "golden_test.json"
    path.write_text(json.dumps(_dataset_with(_all_categories_cases())), encoding="utf-8")
    dataset = load_dataset(path)
    assert len(dataset) == MIN_CASES
