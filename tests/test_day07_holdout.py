"""
Day 7 Task 2 — holdout separation.

Working rule under test: "Holdout may be measured, but must not be used to
tune: retrieval settings, prompts, model routing, thresholds, labels,
expected sources, answerability, critical facts, prohibited claims."

`tunable_cases()` is the structural enforcement of that rule (see its
docstring in aico.evals.dataset): anything that tunes something should read
the dataset only through it, and it can never return a holdout case by
construction. These tests prove that boundary actually holds - both for the
real committed dataset and, for the leakage detector, against a synthetic
dataset built specifically to leak.
"""
import pathlib

from aico.evals.dataset import (
    REQUIRED_CATEGORIES,
    cross_split_question_leakage,
    group_by_split,
    holdout_cases,
    load_dataset,
    tunable_cases,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
GOLDEN_PATH = REPO_ROOT / "evals" / "golden_v1.json"

DATASET = load_dataset(GOLDEN_PATH)


def test_holdout_cases_are_only_holdout_split():
    holdout = holdout_cases(DATASET)
    assert len(holdout) > 0
    assert all(c.split == "holdout" for c in holdout)


def test_holdout_covers_every_required_category():
    # Design decision documented in evals/README.md: holdout is not just
    # easy answerable cases - the zero-tolerance safety gate (adversarial)
    # and the invented-fact guard (unanswerable) must both be provable on
    # data the system was never tuned against, not only on train/dev.
    holdout_categories = {c.category for c in holdout_cases(DATASET)}
    assert holdout_categories == REQUIRED_CATEGORIES


def test_tunable_cases_never_returns_a_holdout_case():
    tunable = tunable_cases(DATASET)
    assert all(c.split != "holdout" for c in tunable)


def test_tunable_cases_and_holdout_cases_partition_the_dataset():
    tunable = tunable_cases(DATASET)
    holdout = holdout_cases(DATASET)

    tunable_ids = {c.case_id for c in tunable}
    holdout_ids = {c.case_id for c in holdout}

    # disjoint - no case is both tunable and held out
    assert tunable_ids.isdisjoint(holdout_ids)
    # covers every case - no case is neither (falls through the split)
    assert tunable_ids | holdout_ids == {c.case_id for c in DATASET.cases}
    assert len(tunable_ids) + len(holdout_ids) == len(DATASET)


def test_tunable_cases_is_exactly_train_plus_development():
    tunable_ids = {c.case_id for c in tunable_cases(DATASET)}
    expected_ids = {c.case_id for c in DATASET.cases if c.split in ("train", "development")}
    assert tunable_ids == expected_ids


def test_group_by_split_shows_holdout_as_its_own_group():
    # Task 11's report must show holdout separately - this is the grouping
    # helper it will use, so prove the grouping itself is correct here.
    groups = group_by_split(DATASET)
    assert set(groups.keys()) == {"train", "development", "holdout"}
    assert groups["holdout"] == holdout_cases(DATASET)
    assert sum(len(cases) for cases in groups.values()) == len(DATASET)


def test_no_cross_split_leakage_in_the_real_dataset():
    assert cross_split_question_leakage(DATASET.cases) == []


# ── Leakage detector actually detects leakage (synthetic dataset) ───────

def _case(case_id, split, question, category="answerable"):
    return {
        "case_id": case_id,
        "category": category,
        "split": split,
        "question": question,
        "expected_sources": [],
        "answerability": "answerable",
        "critical_facts": [],
        "prohibited_claims": [],
    }


def test_leakage_detector_flags_the_same_question_in_two_splits():
    # Different case_id, same underlying question, split across train and
    # holdout - unique IDs alone would miss this; the detector must not.
    cases = [
        _case("A-001", "train", "What is the payment term?"),
        _case("A-002", "holdout", "What is the payment term?"),
    ]
    leaks = cross_split_question_leakage([_to_case(c) for c in cases])
    assert len(leaks) == 1
    assert set(leaks[0]["splits"].keys()) == {"train", "holdout"}


def test_leakage_detector_is_normalisation_aware():
    # Same question, different punctuation/casing - still a leak.
    cases = [
        _case("B-001", "development", "What is the Payment Term?"),
        _case("B-002", "holdout", "what is the payment term"),
    ]
    leaks = cross_split_question_leakage([_to_case(c) for c in cases])
    assert len(leaks) == 1


def test_leakage_detector_reports_nothing_for_distinct_questions():
    cases = [
        _case("C-001", "train", "What is the payment term?"),
        _case("C-002", "holdout", "What is the delivery window?"),
    ]
    assert cross_split_question_leakage([_to_case(c) for c in cases]) == []


def test_leakage_is_allowed_within_the_same_split():
    # Two cases in the same split sharing a question isn't a cross-split
    # leak (it might be a dataset-quality issue, but it's not what this
    # check is for) - only >1 distinct split for the same question counts.
    cases = [
        _case("D-001", "train", "What is the payment term?"),
        _case("D-002", "train", "What is the payment term?"),
    ]
    assert cross_split_question_leakage([_to_case(c) for c in cases]) == []


def _to_case(case_dict):
    # Route synthetic dicts through the real parser so these tests exercise
    # the same GoldenCase construction the loader uses, not a hand-rolled
    # stand-in that could silently drift from it.
    from aico.evals.dataset import _parse_case
    problems: list[str] = []
    parsed = _parse_case(case_dict, 0, problems)
    assert not problems, problems
    return parsed
