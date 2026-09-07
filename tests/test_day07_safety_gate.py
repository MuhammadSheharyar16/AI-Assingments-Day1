"""
Day 7 Task 7 — thresholds and safety-gate tests.

Two groups: `load_thresholds` validation against both the real committed
`evals/thresholds_v1.json` and hand-built broken dicts (proving each
documented failure mode is actually rejected), and `evaluate_gate`'s two
independent checks - safety (zero tolerance, "regardless of aggregate
score") and per-metric thresholds - against synthetic `EvaluationSummary`
values with known correct verdicts. A final real end-to-end test builds an
`EvaluationSummary` from the real pipeline (same honest, non-per-case-
scripted approach as `scripts/day07_generate_failure_classification_report.py`)
and checks it against the real committed thresholds file - the gate this
repository would actually see today.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from aico.evals.metrics import AttackCheckResult
from aico.evals.regression import (
    REQUIRED_METRIC_NAMES,
    EvaluationSummary,
    GateResult,
    MetricThreshold,
    Thresholds,
    ThresholdValidationError,
    evaluate_gate,
    load_thresholds,
    render_gate_summary,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
THRESHOLDS_PATH = REPO_ROOT / "evals" / "thresholds_v1.json"


# ── load_thresholds: real committed file ─────────────────────────────

def test_real_thresholds_file_loads_without_raising():
    thresholds = load_thresholds(THRESHOLDS_PATH)
    assert thresholds.thresholds_id == "thresholds_v1"


def test_real_thresholds_file_has_every_required_metric():
    thresholds = load_thresholds(THRESHOLDS_PATH)
    assert set(thresholds.metrics) >= set(REQUIRED_METRIC_NAMES)


def test_real_thresholds_file_has_safety_zero_tolerance_enabled():
    thresholds = load_thresholds(THRESHOLDS_PATH)
    assert thresholds.safety_zero_tolerance is True


def test_real_thresholds_have_valid_min_values_and_rationale():
    thresholds = load_thresholds(THRESHOLDS_PATH)
    for name, t in thresholds.metrics.items():
        assert 0.0 <= t.min <= 1.0, f"{name}: min out of range"
        assert t.rationale.strip(), f"{name}: missing rationale"


def test_citation_validity_threshold_requires_perfection():
    # Deliberate design choice, documented in the file itself - a forged
    # citation is a factual-integrity failure, never given headroom.
    thresholds = load_thresholds(THRESHOLDS_PATH)
    assert thresholds.metrics["citation_validity_rate"].min == 1.0


# ── load_thresholds: validation against hand-built dicts ────────────

def _valid_thresholds_dict(**overrides) -> dict:
    d = {
        "version": "1.0",
        "thresholds_id": "test",
        "safety": {"zero_tolerance": True},
        "metrics": {
            name: {"min": 0.5, "measured_baseline": 0.6, "rationale": "test"}
            for name in REQUIRED_METRIC_NAMES
        },
    }
    d.update(overrides)
    return d


def test_missing_safety_block_is_rejected(tmp_path):
    d = _valid_thresholds_dict()
    del d["safety"]
    path = tmp_path / "t.json"
    path.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(ThresholdValidationError) as exc_info:
        load_thresholds(path)
    assert any("safety" in p for p in exc_info.value.problems)


def test_missing_required_metric_is_rejected(tmp_path):
    d = _valid_thresholds_dict()
    del d["metrics"]["mrr"]
    path = tmp_path / "t.json"
    path.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(ThresholdValidationError) as exc_info:
        load_thresholds(path)
    assert any("mrr" in p for p in exc_info.value.problems)


def test_non_numeric_min_is_rejected(tmp_path):
    d = _valid_thresholds_dict()
    d["metrics"]["mrr"]["min"] = "high"
    path = tmp_path / "t.json"
    path.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(ThresholdValidationError) as exc_info:
        load_thresholds(path)
    assert any("mrr" in p and "numeric" in p for p in exc_info.value.problems)


def test_out_of_range_min_is_rejected(tmp_path):
    d = _valid_thresholds_dict()
    d["metrics"]["mrr"]["min"] = 1.5
    path = tmp_path / "t.json"
    path.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(ThresholdValidationError) as exc_info:
        load_thresholds(path)
    assert any("mrr" in p and "between 0 and 1" in p for p in exc_info.value.problems)


def test_missing_min_field_is_rejected(tmp_path):
    d = _valid_thresholds_dict()
    del d["metrics"]["mrr"]["min"]
    path = tmp_path / "t.json"
    path.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(ThresholdValidationError) as exc_info:
        load_thresholds(path)
    assert any("mrr" in p for p in exc_info.value.problems)


def test_load_thresholds_accepts_a_well_formed_file(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps(_valid_thresholds_dict()), encoding="utf-8")
    thresholds = load_thresholds(path)
    assert thresholds.safety_zero_tolerance is True
    assert set(thresholds.metrics) == set(REQUIRED_METRIC_NAMES)


# ── evaluate_gate: synthetic thresholds/summaries ────────────────────

def _thresholds(**mins) -> Thresholds:
    return Thresholds(
        version="1.0", thresholds_id="test", safety_zero_tolerance=True,
        metrics={name: MetricThreshold(name=name, min=min_v, measured_baseline=None, rationale="") for name, min_v in mins.items()},
        raw={},
    )


def _summary(*, attack_results=(), **values) -> EvaluationSummary:
    defaults = dict(hit_at_1=None, hit_at_k=None, mrr=None, citation_validity_rate=None, refusal_accuracy_rate=None, groundedness_rate=None)
    defaults.update(values)
    return EvaluationSummary(attack_results=tuple(attack_results), **defaults)


def _attack(case_id, passed):
    return AttackCheckResult(case_id=case_id, passed=passed, observed_kind="blocked" if passed else "grounded_answer", detail="")


def test_gate_passes_when_everything_clears():
    thresholds = _thresholds(hit_at_1=0.5)
    summary = _summary(hit_at_1=0.6, attack_results=[_attack("A", True), _attack("B", True)])
    gate = evaluate_gate(summary, thresholds)
    assert gate.passed is True
    assert gate.safety_failures == ()
    assert all(c.passed for c in gate.metric_checks)


def test_a_single_safety_failure_fails_the_gate_regardless_of_aggregate_score():
    # Every metric comfortably clears its floor; one adversarial case
    # still fails. This is the literal "zero tolerance ... regardless of
    # aggregate score" requirement.
    thresholds = _thresholds(hit_at_1=0.1, mrr=0.1)
    summary = _summary(
        hit_at_1=0.99, mrr=0.99,
        attack_results=[_attack("A", True), _attack("B", True), _attack("C", False)],
    )
    gate = evaluate_gate(summary, thresholds)
    assert gate.passed is False
    assert len(gate.safety_failures) == 1
    assert gate.safety_failures[0].case_id == "C"
    # and the aggregate metrics are still reported as passing - the two
    # checks are independent, not merged into one number.
    assert all(c.passed for c in gate.metric_checks)


def test_multiple_safety_failures_are_all_reported_by_case_id():
    thresholds = _thresholds(hit_at_1=0.0)
    summary = _summary(hit_at_1=1.0, attack_results=[_attack("A", False), _attack("B", True), _attack("C", False)])
    gate = evaluate_gate(summary, thresholds)
    assert {f.case_id for f in gate.safety_failures} == {"A", "C"}


def test_metric_below_floor_fails_the_gate():
    thresholds = _thresholds(hit_at_1=0.7)
    summary = _summary(hit_at_1=0.6, attack_results=[_attack("A", True)])
    gate = evaluate_gate(summary, thresholds)
    assert gate.passed is False
    assert gate.safety_failures == ()
    assert gate.failed_metrics[0].name == "hit_at_1"
    assert gate.failed_metrics[0].value == 0.6


def test_unmeasured_metric_fails_its_check_rather_than_being_skipped():
    thresholds = _thresholds(groundedness_rate=0.5)
    summary = _summary(groundedness_rate=None, attack_results=[])
    gate = evaluate_gate(summary, thresholds)
    assert gate.passed is False
    assert gate.failed_metrics[0].name == "groundedness_rate"
    assert gate.failed_metrics[0].value is None


def test_metric_exactly_at_the_floor_passes():
    thresholds = _thresholds(mrr=0.65)
    summary = _summary(mrr=0.65, attack_results=[])
    gate = evaluate_gate(summary, thresholds)
    assert gate.metric_checks[0].passed is True


def test_safety_check_is_skipped_when_zero_tolerance_is_disabled():
    thresholds = Thresholds(
        version="1.0", thresholds_id="test", safety_zero_tolerance=False,
        metrics={"hit_at_1": MetricThreshold(name="hit_at_1", min=0.0, measured_baseline=None, rationale="")},
        raw={},
    )
    summary = _summary(hit_at_1=1.0, attack_results=[_attack("A", False)])
    gate = evaluate_gate(summary, thresholds)
    assert gate.safety_failures == ()
    assert gate.passed is True  # the disabled safety check never contributes a failure


def test_failed_metrics_property_returns_only_failing_checks():
    thresholds = _thresholds(hit_at_1=0.9, mrr=0.1)
    summary = _summary(hit_at_1=0.5, mrr=0.9, attack_results=[])
    gate = evaluate_gate(summary, thresholds)
    names = {c.name for c in gate.failed_metrics}
    assert names == {"hit_at_1"}


# ── render_gate_summary ───────────────────────────────────────────────

def test_render_gate_summary_shows_pass_and_fail_clearly():
    thresholds = _thresholds(hit_at_1=0.5)
    passing = evaluate_gate(_summary(hit_at_1=0.9, attack_results=[_attack("A", True)]), thresholds)
    failing = evaluate_gate(_summary(hit_at_1=0.9, attack_results=[_attack("A", False)]), thresholds)

    pass_text = render_gate_summary(passing)
    fail_text = render_gate_summary(failing)
    assert "GATE: PASS" in pass_text
    assert "GATE: FAIL" in fail_text
    assert "A" in fail_text
    assert "hit_at_1" in pass_text


# ── Real end-to-end: the gate this repository would see today ───────

def test_real_pipeline_summary_passes_the_real_committed_thresholds():
    # Uses aico.evals.day07's real, shared per-case evaluation (Task 9) -
    # the exact same computation `python -m aico.evals.day07` itself runs,
    # including the real aico.evals.groundedness call (Task 9 populated
    # groundedness_rate for real; it is no longer permanently unmeasured -
    # see evals/README.md Task 9). This test doubles as the regression
    # lock: if a future change drops real performance below these floors,
    # this test fails along with it.
    from aico.evals.dataset import load_dataset
    from aico.evals.day07 import DEFAULT_TOP_K, build_summary, evaluate_all_cases, load_full_index_chunks
    from aico.rag.answer_service import BM25Retriever

    dataset = load_dataset(REPO_ROOT / "evals" / "golden_v1.json")
    retriever = BM25Retriever(top_k=DEFAULT_TOP_K)
    all_chunks = load_full_index_chunks(REPO_ROOT / "data" / "index")

    evaluations = evaluate_all_cases(dataset, retriever, all_chunks, DEFAULT_TOP_K)
    summary = build_summary(evaluations)

    thresholds = load_thresholds(THRESHOLDS_PATH)
    gate = evaluate_gate(summary, thresholds)

    assert gate.safety_failures == (), "real adversarial cases must all pass safety"
    assert gate.failed_metrics == (), f"unexpected metric failure(s): {[c.name for c in gate.failed_metrics]}"
    assert gate.passed is True
