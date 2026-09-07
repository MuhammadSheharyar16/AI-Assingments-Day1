"""
Day 7 Task 9 — the one complete evaluation command.

Tests `aico.evals.day07.main()` end to end via its CLI surface
(`parse_args`/`main(argv)`), against real fixtures on disk (a temp
artifacts dir, so a test run never overwrites the committed
`artifacts/day07/*`), proving all 8 required steps actually happen:
dataset validation (and its failure path), running evaluation, writing
both reports, comparing against thresholds/baseline, the safety gate,
failure classification, and the exit code contract.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from aico.evals import day07

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DATASET_PATH = REPO_ROOT / "evals" / "golden_v1.json"
THRESHOLDS_PATH = REPO_ROOT / "evals" / "thresholds_v1.json"
BASELINE_PATH = REPO_ROOT / "evals" / "baseline_v1.json"
INDEX_DIR = REPO_ROOT / "data" / "index"


def _run(tmp_path, **overrides) -> tuple[int, pathlib.Path]:
    artifacts_dir = tmp_path / "artifacts"
    argv = [
        "--dataset", str(overrides.pop("dataset", DATASET_PATH)),
        "--thresholds", str(overrides.pop("thresholds", THRESHOLDS_PATH)),
        "--baseline", str(overrides.pop("baseline", BASELINE_PATH)),
        "--index", str(overrides.pop("index", INDEX_DIR)),
        "--artifacts-dir", str(artifacts_dir),
    ]
    if "top_k" in overrides:
        argv += ["--top-k", str(overrides.pop("top_k"))]
    assert not overrides, f"unhandled overrides: {overrides}"
    exit_code = day07.main(argv)
    return exit_code, artifacts_dir


# ── Step 1: validate dataset ──────────────────────────────────────────

def test_invalid_dataset_exits_2_with_no_artifacts_written(tmp_path):
    bad_dataset = tmp_path / "bad.json"
    bad_dataset.write_text(json.dumps({"version": "1.0", "cases": []}), encoding="utf-8")
    exit_code, artifacts_dir = _run(tmp_path, dataset=bad_dataset)
    assert exit_code == 2
    assert not artifacts_dir.exists()


def test_missing_index_exits_2(tmp_path):
    exit_code, _ = _run(tmp_path, index=tmp_path / "no-such-index")
    assert exit_code == 2


def test_invalid_thresholds_file_exits_2(tmp_path):
    bad_thresholds = tmp_path / "bad_thresholds.json"
    bad_thresholds.write_text(json.dumps({"version": "1.0", "metrics": {}}), encoding="utf-8")
    exit_code, _ = _run(tmp_path, thresholds=bad_thresholds)
    assert exit_code == 2


# ── Steps 2-4, 5-6, 8: a real, passing run ────────────────────────────

def test_real_run_passes_and_writes_all_three_required_artifacts(tmp_path):
    exit_code, artifacts_dir = _run(tmp_path)
    assert exit_code == 0

    json_path = artifacts_dir / "evaluation_report.json"
    md_path = artifacts_dir / "evaluation_report.md"
    fc_path = artifacts_dir / "failure_classification.md"
    assert json_path.exists()
    assert md_path.exists()
    assert fc_path.exists()

    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["gate_verdict"]["passed"] is True
    assert report["gate_verdict"]["exit_code"] == 0
    assert report["dataset"]["total_cases"] == 32
    assert report["safety_gate"]["failures"] == []


def test_json_report_has_every_required_task11_section(tmp_path):
    _, artifacts_dir = _run(tmp_path)
    report = json.loads((artifacts_dir / "evaluation_report.json").read_text(encoding="utf-8"))

    # dataset version, total case count, split counts, category counts
    assert report["dataset"]["version"]
    assert report["dataset"]["total_cases"] == 32
    assert set(report["dataset"]["split_counts"]) == {"train", "development", "holdout"}
    assert len(report["dataset"]["category_counts"]) == 6

    # Hit@K, MRR, citation validity, refusal accuracy, groundedness, attack pass rate
    det = report["deterministic_metrics"]
    for key in ("hit_at_1", "hit_at_k", "mrr", "citation_validity_rate", "refusal_accuracy_rate", "attack_pass_rate"):
        assert key in det

    # deterministic/model-based separation
    assert "groundedness_rate" in report["model_based_metrics"]
    assert set(report["model_based_metrics"]) & set(det) == set()  # never merged into one dict/score

    # train/development/holdout results
    assert set(report["split_breakdown"]) == {"train", "development", "holdout"}

    # safety gate, threshold comparison, baseline comparison
    assert "zero_tolerance" in report["safety_gate"]
    assert len(report["threshold_comparison"]) == 6
    assert report["baseline_comparison"] is not None

    # stability summary
    assert "note" in report["stability"]
    assert "summary" in report["stability"]

    # failed cases, failure classification summary, final gate verdict
    assert isinstance(report["failed_cases"], list)
    assert set(report["failure_classification_summary"]) == {
        "chunking", "retrieval", "prompt", "citation", "refusal", "evaluator",
    }
    assert report["gate_verdict"]["passed"] in (True, False)


def test_no_unexplained_ai_score_merges_deterministic_and_model_based_results(tmp_path):
    # Working rule, verified structurally: deterministic and model-based
    # results must never collapse into one combined/blended number.
    _, artifacts_dir = _run(tmp_path)
    report = json.loads((artifacts_dir / "evaluation_report.json").read_text(encoding="utf-8"))

    assert "deterministic_metrics" in report
    assert "model_based_metrics" in report
    # no top-level or nested key anywhere suggests a merged score
    forbidden_substrings = ("ai_score", "overall_score", "combined_score", "blended_score")
    serialized_keys = json.dumps(report)
    for forbidden in forbidden_substrings:
        assert forbidden not in serialized_keys


def test_stability_summary_is_embedded_when_the_file_exists(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True)
    fake_summary = {
        "repeat_count": 5, "subset_case_ids": ["GC-002"],
        "system_under_test": [{"case_id": "GC-002", "pass_rate": 1.0, "stable": True}],
        "evaluator": [{"case_id": "GC-002", "grounded_rate": 1.0, "stable": True}],
    }
    (artifacts_dir / "stability_summary.json").write_text(json.dumps(fake_summary), encoding="utf-8")

    exit_code = day07.main([
        "--dataset", str(DATASET_PATH), "--thresholds", str(THRESHOLDS_PATH), "--baseline", str(BASELINE_PATH),
        "--index", str(INDEX_DIR), "--artifacts-dir", str(artifacts_dir),
    ])
    assert exit_code == 0
    report = json.loads((artifacts_dir / "evaluation_report.json").read_text(encoding="utf-8"))
    assert report["stability"]["summary"] == fake_summary

    md = (artifacts_dir / "evaluation_report.md").read_text(encoding="utf-8")
    assert "GC-002" in md.split("## Stability")[1].split("## Failed cases")[0]


def test_stability_summary_is_none_when_the_file_is_absent(tmp_path):
    _, artifacts_dir = _run(tmp_path)
    report = json.loads((artifacts_dir / "evaluation_report.json").read_text(encoding="utf-8"))
    assert report["stability"]["summary"] is None
    md = (artifacts_dir / "evaluation_report.md").read_text(encoding="utf-8")
    assert "No `stability_summary.json` found" in md


def test_markdown_report_mirrors_the_json_report(tmp_path):
    _, artifacts_dir = _run(tmp_path)
    report = json.loads((artifacts_dir / "evaluation_report.json").read_text(encoding="utf-8"))
    md = (artifacts_dir / "evaluation_report.md").read_text(encoding="utf-8")

    assert "# Day 7 — Evaluation Report" in md
    assert "## Deterministic metrics" in md
    assert "## Model-based metrics" in md
    assert "## Split breakdown" in md
    assert "## Safety gate" in md
    assert "## Threshold comparison" in md
    assert "## Baseline comparison" in md
    assert "## Failed cases and classification" in md
    verdict = "PASS" if report["gate_verdict"]["passed"] else "FAIL"
    assert f"Final gate verdict: {verdict}" in md


def test_failure_classification_artifact_is_well_formed(tmp_path):
    _, artifacts_dir = _run(tmp_path)
    fc = (artifacts_dir / "failure_classification.md").read_text(encoding="utf-8")
    assert "# Day 7 — Failure Classification" in fc
    assert "chunking" in fc and "evaluator" in fc  # every taxonomy type named in the summary table


def test_normal_run_never_touches_the_committed_artifacts(tmp_path):
    # The temp --artifacts-dir isolation above is what makes this true -
    # confirm the actual committed files were not written to.
    before = {
        p.name: p.read_bytes()
        for p in (REPO_ROOT / "artifacts" / "day07").glob("*")
        if p.name in ("evaluation_report.json", "evaluation_report.md", "failure_classification.md")
    }
    _run(tmp_path)
    after = {
        p.name: p.read_bytes()
        for p in (REPO_ROOT / "artifacts" / "day07").glob("*")
        if p.name in ("evaluation_report.json", "evaluation_report.md", "failure_classification.md")
    }
    for name in before:
        assert before[name] == after[name], f"{name} changed after a test run with an isolated --artifacts-dir"


# ── The gate can fail, and the exit code reflects it ──────────────────

def test_weakened_retrieval_top_k_makes_the_gate_fail(tmp_path):
    # Task 10's own required proof, exercised here as a regression-gate
    # behavior test: a much smaller retrieval window predictably degrades
    # Hit@K/refusal accuracy enough to trip at least one threshold.
    exit_code, artifacts_dir = _run(tmp_path, top_k=1)
    report = json.loads((artifacts_dir / "evaluation_report.json").read_text(encoding="utf-8"))
    assert exit_code != 0
    assert report["gate_verdict"]["passed"] is False
    assert any(not c["passed"] for c in report["threshold_comparison"])


def test_controlled_regression_full_sequence_pass_fail_pass(tmp_path):
    # Task 10's full required sequence, driven directly (not via the
    # demonstration script): the exact same approved configuration must
    # pass, a deliberately weakened one must fail with a non-zero exit,
    # and restoring the exact same approved configuration must pass again
    # - proving the weakening was reversible, not a change to what
    # "approved" means.
    normal_exit_1, normal_artifacts_1 = _run(tmp_path / "normal_1", top_k=day07.DEFAULT_TOP_K)
    weakened_exit, weakened_artifacts = _run(tmp_path / "weakened", top_k=1)
    normal_exit_2, normal_artifacts_2 = _run(tmp_path / "normal_2", top_k=day07.DEFAULT_TOP_K)

    normal_report_1 = json.loads((normal_artifacts_1 / "evaluation_report.json").read_text(encoding="utf-8"))
    weakened_report = json.loads((weakened_artifacts / "evaluation_report.json").read_text(encoding="utf-8"))
    normal_report_2 = json.loads((normal_artifacts_2 / "evaluation_report.json").read_text(encoding="utf-8"))

    assert normal_exit_1 == 0 and normal_report_1["gate_verdict"]["passed"] is True
    assert weakened_exit != 0 and weakened_report["gate_verdict"]["passed"] is False
    assert normal_exit_2 == 0 and normal_report_2["gate_verdict"]["passed"] is True

    # restoring the identical configuration reproduces the identical
    # verdict, not just "some" pass - the deterministic pipeline means
    # the two normal runs' metrics match exactly.
    assert normal_report_1["deterministic_metrics"] == normal_report_2["deterministic_metrics"]


def test_a_stricter_threshold_file_fails_the_gate_even_when_the_run_is_unchanged(tmp_path):
    strict = json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))
    strict["metrics"]["hit_at_1"]["min"] = 0.999
    strict_path = tmp_path / "strict_thresholds.json"
    strict_path.write_text(json.dumps(strict), encoding="utf-8")

    exit_code, artifacts_dir = _run(tmp_path, thresholds=strict_path)
    assert exit_code != 0
    report = json.loads((artifacts_dir / "evaluation_report.json").read_text(encoding="utf-8"))
    failed = {c["name"] for c in report["threshold_comparison"] if not c["passed"]}
    assert "hit_at_1" in failed


def test_no_baseline_file_still_runs_the_gate_with_no_comparison(tmp_path):
    exit_code, artifacts_dir = _run(tmp_path, baseline=tmp_path / "no-such-baseline.json")
    assert exit_code == 0
    report = json.loads((artifacts_dir / "evaluation_report.json").read_text(encoding="utf-8"))
    assert report["baseline_comparison"] is None


# ── --update-baseline: separate path, never runs the gate ───────────

def test_update_baseline_without_reviewer_or_notes_is_rejected(tmp_path):
    exit_code = day07.main(["--update-baseline", "--dataset", str(DATASET_PATH), "--index", str(INDEX_DIR), "--baseline", str(tmp_path / "b.json")])
    assert exit_code == 2
    assert not (tmp_path / "b.json").exists()


def test_update_baseline_dry_run_writes_nothing(tmp_path):
    target = tmp_path / "b.json"
    exit_code = day07.main([
        "--update-baseline", "--dataset", str(DATASET_PATH), "--index", str(INDEX_DIR),
        "--baseline", str(target), "--reviewer", "test@example.com", "--notes", "dry run test",
    ])
    assert exit_code == 0
    assert not target.exists()


def test_update_baseline_with_confirm_writes_a_valid_baseline(tmp_path):
    target = tmp_path / "b.json"
    exit_code = day07.main([
        "--update-baseline", "--dataset", str(DATASET_PATH), "--index", str(INDEX_DIR),
        "--baseline", str(target), "--reviewer", "test@example.com", "--notes", "test update", "--confirm",
    ])
    assert exit_code == 0
    assert target.exists()
    from aico.evals.regression import load_baseline
    baseline = load_baseline(target)
    assert baseline.review.reviewer == "test@example.com"


def test_update_baseline_never_writes_the_evaluation_reports(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    day07.main([
        "--update-baseline", "--dataset", str(DATASET_PATH), "--index", str(INDEX_DIR),
        "--baseline", str(tmp_path / "b.json"), "--artifacts-dir", str(artifacts_dir),
        "--reviewer", "test@example.com", "--notes", "test",
    ])
    assert not artifacts_dir.exists()  # --update-baseline is a separate path - it never runs steps 3/4/7
