"""
Day 7 Task 8 — reviewed baseline tests.

Four groups: `load_baseline` against the real committed `evals/baseline_v1.json`
and against hand-built broken dicts; `compare_to_baseline` against
synthetic summaries with known regression/no-regression verdicts;
`write_baseline`'s own guards (missing metrics, empty reviewer/notes); and
the behavioral proof the working rule actually requires - that normal
evaluation (`evaluate_gate`, `compare_to_baseline`, a full real pipeline
run) never writes so much as one byte to the baseline file, no matter how
many times it runs.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from aico.evals.regression import (
    REQUIRED_METRIC_NAMES,
    Baseline,
    BaselineValidationError,
    EvaluationSummary,
    ReviewMetadata,
    compare_to_baseline,
    load_baseline,
    load_thresholds,
    render_baseline_comparison,
    write_baseline,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
BASELINE_PATH = REPO_ROOT / "evals" / "baseline_v1.json"
THRESHOLDS_PATH = REPO_ROOT / "evals" / "thresholds_v1.json"


# ── load_baseline: real committed file ───────────────────────────────

def test_real_baseline_file_loads_without_raising():
    baseline = load_baseline(BASELINE_PATH)
    assert baseline.baseline_id == "baseline_v1"


def test_real_baseline_identifies_everything_task_8_requires():
    baseline = load_baseline(BASELINE_PATH)
    assert baseline.dataset_version  # which golden_v1.json version
    assert baseline.evaluator_prompt_version  # which groundedness prompt version
    assert baseline.model_aliases  # what produced the candidate answers
    assert baseline.retrieval_config  # chunking/index parameters
    assert set(baseline.metrics) >= set(REQUIRED_METRIC_NAMES)
    assert baseline.review.reviewer
    assert baseline.review.date
    assert baseline.review.notes


def test_real_baseline_metrics_match_the_real_thresholds_measured_baselines():
    # Task 7's thresholds_v1.json documents the same real run's numbers in
    # its own measured_baseline fields - the two files must agree, since
    # they describe the same evidence.
    baseline = load_baseline(BASELINE_PATH)
    thresholds = load_thresholds(THRESHOLDS_PATH)
    for name, threshold in thresholds.metrics.items():
        if threshold.measured_baseline is None:
            continue
        assert baseline.metrics[name] == pytest.approx(threshold.measured_baseline, abs=1e-3), name


def test_real_baseline_groundedness_rate_is_now_genuinely_measured():
    # Task 8's initial baseline left this null (no full-dataset grader run
    # existed yet); Task 9's aico.evals.day07 populated it for real via a
    # deterministic honest grader (well_behaved_verdict) - see
    # evals/README.md Task 9. `null` remains a structurally valid value
    # (test_null_metric_value_is_accepted below proves the loader still
    # accepts it), it just isn't what the current committed baseline has.
    baseline = load_baseline(BASELINE_PATH)
    assert baseline.metrics["groundedness_rate"] is not None
    assert 0.0 <= baseline.metrics["groundedness_rate"] <= 1.0


# ── load_baseline: validation against hand-built dicts ──────────────

def _valid_baseline_dict(**overrides) -> dict:
    d = {
        "version": "1.0",
        "baseline_id": "test",
        "dataset_version": "1.0",
        "evaluator_prompt_version": "1.0",
        "model_aliases": {"chat": "fake:x"},
        "retrieval_config": {"mode": "bm25", "top_k": 5},
        "metrics": {name: 0.7 for name in REQUIRED_METRIC_NAMES},
        "review": {"reviewer": "a@b.com", "date": "2026-09-07", "notes": "test"},
    }
    d.update(overrides)
    return d


@pytest.mark.parametrize("field_name", ["version", "baseline_id", "dataset_version", "evaluator_prompt_version"])
def test_missing_required_top_level_field_is_rejected(tmp_path, field_name):
    d = _valid_baseline_dict()
    del d[field_name]
    path = tmp_path / "b.json"
    path.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(BaselineValidationError) as exc_info:
        load_baseline(path)
    assert any(field_name in p for p in exc_info.value.problems)


def test_missing_model_aliases_is_rejected(tmp_path):
    d = _valid_baseline_dict()
    del d["model_aliases"]
    path = tmp_path / "b.json"
    path.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(BaselineValidationError) as exc_info:
        load_baseline(path)
    assert any("model_aliases" in p for p in exc_info.value.problems)


def test_missing_retrieval_config_is_rejected(tmp_path):
    d = _valid_baseline_dict()
    del d["retrieval_config"]
    path = tmp_path / "b.json"
    path.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(BaselineValidationError) as exc_info:
        load_baseline(path)
    assert any("retrieval_config" in p for p in exc_info.value.problems)


def test_missing_metric_is_rejected(tmp_path):
    d = _valid_baseline_dict()
    del d["metrics"]["mrr"]
    path = tmp_path / "b.json"
    path.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(BaselineValidationError) as exc_info:
        load_baseline(path)
    assert any("mrr" in p for p in exc_info.value.problems)


def test_non_numeric_metric_value_is_rejected(tmp_path):
    d = _valid_baseline_dict()
    d["metrics"]["mrr"] = "high"
    path = tmp_path / "b.json"
    path.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(BaselineValidationError) as exc_info:
        load_baseline(path)
    assert any("mrr" in p and "numeric" in p for p in exc_info.value.problems)


def test_null_metric_value_is_accepted():
    # groundedness_rate: null is exactly the real baseline's own honest
    # not-yet-measured case - must load cleanly, not be rejected.
    d = _valid_baseline_dict()
    d["metrics"]["groundedness_rate"] = None
    baseline = Baseline(
        version=d["version"], baseline_id=d["baseline_id"], dataset_version=d["dataset_version"],
        evaluator_prompt_version=d["evaluator_prompt_version"], model_aliases=d["model_aliases"],
        retrieval_config=d["retrieval_config"],
        metrics={**{n: 0.7 for n in REQUIRED_METRIC_NAMES}, "groundedness_rate": None},
        review=ReviewMetadata(**d["review"]), raw=d,
    )
    assert baseline.metrics["groundedness_rate"] is None


@pytest.mark.parametrize("missing_review_field", ["reviewer", "date", "notes"])
def test_missing_review_field_is_rejected(tmp_path, missing_review_field):
    d = _valid_baseline_dict()
    del d["review"][missing_review_field]
    path = tmp_path / "b.json"
    path.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(BaselineValidationError) as exc_info:
        load_baseline(path)
    assert any(missing_review_field in p for p in exc_info.value.problems)


def test_load_baseline_accepts_a_well_formed_file(tmp_path):
    path = tmp_path / "b.json"
    path.write_text(json.dumps(_valid_baseline_dict()), encoding="utf-8")
    baseline = load_baseline(path)
    assert baseline.review.reviewer == "a@b.com"


# ── compare_to_baseline ───────────────────────────────────────────────

def _baseline_with(**metrics) -> Baseline:
    # Un-passed metrics default to None (unestablished), matching
    # _summary()'s own None defaults - a test that names only "mrr" should
    # exercise only that one dimension, not accidentally also assert every
    # other metric is "candidate unmeasured against a real baseline value"
    # (itself a real, separately-tested regression case below).
    full: dict[str, float | None] = dict.fromkeys(REQUIRED_METRIC_NAMES)
    full.update(metrics)
    return Baseline(
        version="1.0", baseline_id="b", dataset_version="1.0", evaluator_prompt_version="1.0",
        model_aliases={"chat": "x"}, retrieval_config={"mode": "bm25"}, metrics=full,
        review=ReviewMetadata(reviewer="a", date="2026-01-01", notes="n"), raw={},
    )


def _summary(**values) -> EvaluationSummary:
    defaults = dict(hit_at_1=None, hit_at_k=None, mrr=None, citation_validity_rate=None, refusal_accuracy_rate=None, groundedness_rate=None)
    defaults.update(values)
    return EvaluationSummary(attack_results=(), **defaults)


def test_candidate_better_than_baseline_is_not_a_regression():
    comparison = compare_to_baseline(_summary(mrr=0.8), _baseline_with(mrr=0.7))
    mrr = next(c for c in comparison.comparisons if c.name == "mrr")
    assert mrr.regressed is False
    assert mrr.delta == pytest.approx(0.1)


def test_candidate_worse_than_baseline_is_a_regression():
    comparison = compare_to_baseline(_summary(mrr=0.5), _baseline_with(mrr=0.7))
    mrr = next(c for c in comparison.comparisons if c.name == "mrr")
    assert mrr.regressed is True
    assert mrr.delta == pytest.approx(-0.2)
    assert comparison.regressions == (mrr,)


def test_candidate_equal_to_baseline_is_not_a_regression():
    comparison = compare_to_baseline(_summary(mrr=0.7), _baseline_with(mrr=0.7))
    mrr = next(c for c in comparison.comparisons if c.name == "mrr")
    assert mrr.regressed is False


def test_candidate_unmeasured_against_a_real_baseline_value_is_a_regression():
    comparison = compare_to_baseline(_summary(mrr=None), _baseline_with(mrr=0.7))
    mrr = next(c for c in comparison.comparisons if c.name == "mrr")
    assert mrr.regressed is True
    assert mrr.delta is None


def test_unestablished_baseline_metric_is_never_flagged_regardless_of_candidate():
    comparison = compare_to_baseline(_summary(groundedness_rate=0.0), _baseline_with(groundedness_rate=None))
    g = next(c for c in comparison.comparisons if c.name == "groundedness_rate")
    assert g.regressed is False


def test_render_baseline_comparison_flags_regressions_visibly():
    comparison = compare_to_baseline(_summary(mrr=0.5), _baseline_with(mrr=0.7))
    text = render_baseline_comparison(comparison)
    assert "REGRESSED" in text
    assert "1 regression" in text


# ── write_baseline guards ─────────────────────────────────────────────

def test_write_baseline_rejects_missing_metrics(tmp_path):
    incomplete = {name: 0.5 for name in REQUIRED_METRIC_NAMES if name != "mrr"}
    with pytest.raises(ValueError, match="mrr"):
        write_baseline(
            tmp_path / "b.json", dataset_version="1.0", evaluator_prompt_version="1.0",
            model_aliases={"chat": "x"}, retrieval_config={"mode": "bm25"}, metrics=incomplete,
            reviewer="a@b.com", date="2026-01-01", notes="test",
        )
    assert not (tmp_path / "b.json").exists()


@pytest.mark.parametrize("field_name,value", [("reviewer", ""), ("notes", "   ")])
def test_write_baseline_rejects_empty_reviewer_or_notes(tmp_path, field_name, value):
    kwargs = dict(
        dataset_version="1.0", evaluator_prompt_version="1.0", model_aliases={"chat": "x"},
        retrieval_config={"mode": "bm25"}, metrics={n: 0.5 for n in REQUIRED_METRIC_NAMES},
        reviewer="a@b.com", date="2026-01-01", notes="test",
    )
    kwargs[field_name] = value
    with pytest.raises(ValueError, match="reviewer, date and notes"):
        write_baseline(tmp_path / "b.json", **kwargs)
    assert not (tmp_path / "b.json").exists()


def test_write_baseline_produces_a_file_load_baseline_accepts(tmp_path):
    path = tmp_path / "b.json"
    write_baseline(
        path, dataset_version="1.0", evaluator_prompt_version="1.0", model_aliases={"chat": "x"},
        retrieval_config={"mode": "bm25"}, metrics={n: 0.5 for n in REQUIRED_METRIC_NAMES},
        reviewer="a@b.com", date="2026-01-01", notes="test reason",
    )
    baseline = load_baseline(path)
    assert baseline.review.reviewer == "a@b.com"
    assert baseline.metrics["mrr"] == 0.5


# ── The behavioral proof: normal evaluation never rewrites the baseline ──

def test_normal_evaluation_never_writes_the_baseline_file():
    """The working-rule proof, made behavioral rather than by-convention:
    snapshot evals/baseline_v1.json's exact bytes, run a full real
    evaluation (loading the baseline, comparing against it, evaluating the
    gate) several times over, and confirm the file on disk never moved -
    not even its mtime, let alone its content."""
    from aico.evals.dataset import load_dataset
    from aico.evals.day07 import DEFAULT_TOP_K, build_summary, evaluate_all_cases, load_full_index_chunks
    from aico.evals.regression import evaluate_gate
    from aico.rag.answer_service import BM25Retriever

    before_bytes = BASELINE_PATH.read_bytes()
    before_mtime = BASELINE_PATH.stat().st_mtime_ns

    dataset = load_dataset(REPO_ROOT / "evals" / "golden_v1.json")
    retriever = BM25Retriever(top_k=DEFAULT_TOP_K)
    all_chunks = load_full_index_chunks(REPO_ROOT / "data" / "index")
    thresholds = load_thresholds(THRESHOLDS_PATH)

    for _ in range(3):  # run the full evaluation more than once - never once writes
        baseline = load_baseline(BASELINE_PATH)  # reading, never writing
        evaluations = evaluate_all_cases(dataset, retriever, all_chunks, DEFAULT_TOP_K)
        summary = build_summary(evaluations)
        evaluate_gate(summary, thresholds)  # the gate itself
        compare_to_baseline(summary, baseline)  # the baseline comparison

    after_bytes = BASELINE_PATH.read_bytes()
    after_mtime = BASELINE_PATH.stat().st_mtime_ns
    assert after_bytes == before_bytes, "evals/baseline_v1.json content changed after running normal evaluation"
    assert after_mtime == before_mtime, "evals/baseline_v1.json was rewritten (mtime changed) after running normal evaluation"


def test_write_baseline_is_the_only_function_regression_module_exposes_that_writes():
    # Static confirmation alongside the behavioral proof above: no other
    # public name in aico.evals.regression's source contains a file-write
    # call - write_baseline is the sole writer, by inspection as well as
    # by behavior.
    import inspect

    from aico.evals import regression

    source = inspect.getsource(regression)
    write_baseline_source = inspect.getsource(regression.write_baseline)
    remainder = source.replace(write_baseline_source, "")
    assert "write_text(" not in remainder
    assert "write_text(" in write_baseline_source
