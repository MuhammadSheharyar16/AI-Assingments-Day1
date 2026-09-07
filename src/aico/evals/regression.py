"""
Day 7 Task 7 — thresholds and the safety gate.

Loads `evals/thresholds_v1.json` (developer-authored, no supplied pack —
see `evals/README.md`) into a typed, validated `Thresholds`, and applies it
to one evaluation run's aggregate metrics (`EvaluationSummary`) via
`evaluate_gate()`. This module owns *applying* thresholds — "explicit,
machine-readable, reviewed, and actually applied" (working rule) means the
JSON file alone proves nothing; `evaluate_gate` is what makes the numbers
in it a real gate, and `tests/test_day07_safety_gate.py` proves it fires
correctly rather than only existing to be imported.

Two independent checks, deliberately not merged into one score:

1. **Safety (zero tolerance)** — every `adversarial`/`must_refuse` case's
   own `aico.evals.metrics.AttackCheckResult` is checked *individually*.
   A single failing case fails the gate outright, reported as its own
   `SafetyFailure` list — this is a structurally separate code path from
   the aggregate metric checks below, not merely a `min: 1.0` threshold
   on an aggregate rate, per the working rule "One required safety
   failure must fail the gate regardless of aggregate score": the safety
   check is evaluated and reported independently of whatever the
   aggregate metrics say, never folded into or masked by them.
2. **Aggregate metric thresholds** — Hit@1/Hit@K/MRR/citation validity/
   refusal accuracy/groundedness rate, each checked against its own
   `min` from `thresholds_v1.json`. A metric a run didn't measure at all
   (`None`) fails its check rather than being silently skipped — a
   threshold that can't be evaluated is not a threshold that passed.

Task 9's full regression gate (`aico.evals.day07`) builds the
`EvaluationSummary` this module checks against from a real evaluation run
and a reviewed `baseline_v1.json` (Task 8) comparison; this module knows
nothing about CI, only about turning numbers into a pass/fail decision.

Day 7 Task 8 adds the baseline half: `load_baseline`/`compare_to_baseline`
read `evals/baseline_v1.json` and diff a candidate `EvaluationSummary`
against it (`BaselineComparison`) — read-only, exactly like
`load_thresholds`/`evaluate_gate` above. `write_baseline` is the ONE
function in this entire codebase allowed to write that file, and nothing
above ever calls it: normal evaluation (`evaluate_gate`,
`compare_to_baseline`, everything in `aico.evals.metrics`/
`aico.evals.groundedness`) only ever *reads* the baseline, never rewrites
it (working rule: "normal evaluation must never overwrite the approved
baseline") — `tests/test_day07_baseline_update.py` proves this
behaviorally, not just by convention: it snapshots the file, runs a full
real evaluation, and asserts the bytes on disk never moved.
`write_baseline` is only ever invoked from
`scripts/day07_update_baseline.py`, a separate, deliberate, reviewable
command distinct from any evaluation entry point (see that script and
`evals/README.md`'s Task 8 section for the "separate, documented,
deliberate, reviewable" update workflow).
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

from aico.evals.metrics import AttackCheckResult

REQUIRED_METRIC_NAMES: tuple[str, ...] = (
    "hit_at_1",
    "hit_at_k",
    "mrr",
    "citation_validity_rate",
    "refusal_accuracy_rate",
    "groundedness_rate",
)


class ThresholdValidationError(ValueError):
    """Raised by `load_thresholds` when `thresholds_v1.json` fails
    validation. Carries every problem found, same discipline as
    `aico.evals.dataset.DatasetValidationError`."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        summary = "\n".join(f"- {p}" for p in self.problems)
        super().__init__(f"{len(self.problems)} threshold problem(s):\n{summary}")


@dataclass(frozen=True)
class MetricThreshold:
    name: str
    min: float
    measured_baseline: float | None
    rationale: str


@dataclass(frozen=True)
class Thresholds:
    version: str
    thresholds_id: str
    safety_zero_tolerance: bool
    metrics: dict[str, MetricThreshold]
    raw: dict  # full parsed JSON, for report generation that needs fields this loader doesn't model explicitly


def load_thresholds(path: pathlib.Path) -> Thresholds:
    """Read + fully validate a thresholds JSON file (default:
    `evals/thresholds_v1.json`)."""
    raw = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    problems: list[str] = []

    safety = raw.get("safety")
    if not isinstance(safety, dict) or "zero_tolerance" not in safety:
        problems.append("top-level 'safety.zero_tolerance' is required")
        safety = {}

    metrics_raw = raw.get("metrics")
    if not isinstance(metrics_raw, dict):
        raise ThresholdValidationError(["top-level 'metrics' field is missing or not an object"])

    missing = [name for name in REQUIRED_METRIC_NAMES if name not in metrics_raw]
    if missing:
        problems.append(f"missing threshold definition(s) for required metric(s): {missing}")

    metrics: dict[str, MetricThreshold] = {}
    for name, spec in metrics_raw.items():
        if not isinstance(spec, dict) or "min" not in spec:
            problems.append(f"metrics.{name}.min is required")
            continue
        min_value = spec["min"]
        if not isinstance(min_value, (int, float)) or isinstance(min_value, bool):
            problems.append(f"metrics.{name}.min must be numeric, got {min_value!r}")
            continue
        if not (0.0 <= float(min_value) <= 1.0):
            problems.append(f"metrics.{name}.min must be between 0 and 1, got {min_value!r}")
            continue
        baseline = spec.get("measured_baseline")
        if baseline is not None and (not isinstance(baseline, (int, float)) or isinstance(baseline, bool)):
            problems.append(f"metrics.{name}.measured_baseline must be numeric or null, got {baseline!r}")
            baseline = None
        metrics[name] = MetricThreshold(
            name=name, min=float(min_value),
            measured_baseline=(float(baseline) if baseline is not None else None),
            rationale=spec.get("rationale", ""),
        )

    if problems:
        raise ThresholdValidationError(problems)

    return Thresholds(
        version=raw.get("version", ""),
        thresholds_id=raw.get("thresholds_id", ""),
        safety_zero_tolerance=bool(safety["zero_tolerance"]),
        metrics=metrics,
        raw=raw,
    )


# ── Gate evaluation ───────────────────────────────────────────────────

@dataclass(frozen=True)
class EvaluationSummary:
    """The aggregate numbers one evaluation run produces - what
    `evaluate_gate` checks against `Thresholds`. `attack_results` is the
    per-case list (not a pre-aggregated rate): the safety check needs to
    name *which* case failed, not just know that some rate dropped."""

    hit_at_1: float | None
    hit_at_k: float | None
    mrr: float | None
    citation_validity_rate: float | None
    refusal_accuracy_rate: float | None
    groundedness_rate: float | None
    attack_results: tuple[AttackCheckResult, ...]


@dataclass(frozen=True)
class SafetyFailure:
    case_id: str
    detail: str


@dataclass(frozen=True)
class MetricCheck:
    name: str
    value: float | None
    min: float
    passed: bool


@dataclass(frozen=True)
class GateResult:
    passed: bool
    safety_failures: tuple[SafetyFailure, ...]
    metric_checks: tuple[MetricCheck, ...]

    @property
    def failed_metrics(self) -> tuple[MetricCheck, ...]:
        return tuple(c for c in self.metric_checks if not c.passed)


def evaluate_gate(summary: EvaluationSummary, thresholds: Thresholds) -> GateResult:
    """Apply `thresholds` to `summary`. The safety check and the aggregate
    metric checks are both always evaluated and both always reported
    (never short-circuited) so a `GateResult` shows the complete picture
    even when safety alone already fails the gate - but `passed` is False
    the moment *either* one has a problem, independently."""
    safety_failures: tuple[SafetyFailure, ...] = ()
    if thresholds.safety_zero_tolerance:
        safety_failures = tuple(
            SafetyFailure(case_id=r.case_id, detail=r.detail)
            for r in summary.attack_results
            if not r.passed
        )

    field_values: dict[str, float | None] = {
        "hit_at_1": summary.hit_at_1,
        "hit_at_k": summary.hit_at_k,
        "mrr": summary.mrr,
        "citation_validity_rate": summary.citation_validity_rate,
        "refusal_accuracy_rate": summary.refusal_accuracy_rate,
        "groundedness_rate": summary.groundedness_rate,
    }
    metric_checks = tuple(
        MetricCheck(
            name=name,
            value=field_values.get(name),
            min=threshold.min,
            # A metric this run never measured (None) fails its check -
            # a threshold that was never evaluated did not pass it.
            passed=(field_values.get(name) is not None and field_values[name] >= threshold.min),
        )
        for name, threshold in thresholds.metrics.items()
    )

    gate_passed = not safety_failures and all(c.passed for c in metric_checks)
    return GateResult(passed=gate_passed, safety_failures=safety_failures, metric_checks=metric_checks)


def render_gate_summary(gate: GateResult) -> str:
    """Short, human-readable rendering of one `GateResult` - used by
    Task 9's CLI output and by demonstration scripts; the full
    `evaluation_report.md` layout is Task 11's, not this function's."""
    lines: list[str] = []
    lines.append(f"GATE: {'PASS' if gate.passed else 'FAIL'}")
    if gate.safety_failures:
        lines.append(f"  SAFETY (zero tolerance): {len(gate.safety_failures)} failure(s)")
        for f in gate.safety_failures:
            lines.append(f"    - {f.case_id}: {f.detail}")
    else:
        lines.append("  SAFETY (zero tolerance): all adversarial cases passed")
    for c in gate.metric_checks:
        status = "pass" if c.passed else "FAIL"
        value_str = f"{c.value:.4f}" if c.value is not None else "not measured"
        lines.append(f"  {c.name}: {value_str} (min {c.min:.4f}) [{status}]")
    return "\n".join(lines)


# ── Baseline (Task 8) — read side ────────────────────────────────────

class BaselineValidationError(ValueError):
    """Raised by `load_baseline` when `baseline_v1.json` fails validation.
    Same discipline as `ThresholdValidationError`/`DatasetValidationError` -
    every problem found, not just the first."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        summary = "\n".join(f"- {p}" for p in self.problems)
        super().__init__(f"{len(self.problems)} baseline problem(s):\n{summary}")


@dataclass(frozen=True)
class ReviewMetadata:
    reviewer: str
    date: str
    notes: str


@dataclass(frozen=True)
class Baseline:
    """Everything Task 8 requires a baseline to identify: `dataset_version`
    (which `golden_v1.json` this was measured against), `evaluator_prompt_version`
    (which groundedness prompt version - `aico.evals.groundedness.GROUNDEDNESS_EVALUATOR_PROMPT_VERSION`),
    `model_aliases` (what produced the candidate answers being measured),
    `retrieval_config` (chunking/index parameters), the `metrics`
    themselves, and `review` (who approved this baseline and when - the
    "reviewed" half of "reviewed baseline")."""

    version: str
    baseline_id: str
    dataset_version: str
    evaluator_prompt_version: str
    model_aliases: dict
    retrieval_config: dict
    metrics: dict[str, float | None]
    review: ReviewMetadata
    raw: dict  # full parsed JSON, for report generation that needs fields this loader doesn't model explicitly


def load_baseline(path: pathlib.Path) -> Baseline:
    """Read + fully validate a baseline JSON file (default:
    `evals/baseline_v1.json`). Read-only - see module docstring for why
    this function, `compare_to_baseline`, and `evaluate_gate` never write
    the file they read."""
    raw = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    problems: list[str] = []

    for field_name in ("version", "baseline_id", "dataset_version", "evaluator_prompt_version"):
        if not raw.get(field_name):
            problems.append(f"top-level '{field_name}' is required and must be non-empty")

    model_aliases = raw.get("model_aliases")
    if not isinstance(model_aliases, dict) or not model_aliases:
        problems.append("top-level 'model_aliases' is required and must be a non-empty object")
        model_aliases = {}

    retrieval_config = raw.get("retrieval_config")
    if not isinstance(retrieval_config, dict) or not retrieval_config:
        problems.append("top-level 'retrieval_config' is required and must be a non-empty object")
        retrieval_config = {}

    metrics_raw = raw.get("metrics")
    if not isinstance(metrics_raw, dict):
        problems.append("top-level 'metrics' is required and must be an object")
        metrics_raw = {}
    missing = [name for name in REQUIRED_METRIC_NAMES if name not in metrics_raw]
    if missing:
        problems.append(f"missing baseline metric value(s) for required metric(s): {missing}")

    metrics: dict[str, float | None] = {}
    for name, value in metrics_raw.items():
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            problems.append(f"metrics.{name} must be numeric or null, got {value!r}")
            continue
        metrics[name] = float(value) if value is not None else None

    review_raw = raw.get("review")
    if not isinstance(review_raw, dict):
        problems.append("top-level 'review' is required and must be an object")
        review_raw = {}
    review_missing = [f for f in ("reviewer", "date", "notes") if not review_raw.get(f)]
    if review_missing:
        problems.append(f"review fields {review_missing} are required and must be non-empty")

    if problems:
        raise BaselineValidationError(problems)

    return Baseline(
        version=raw["version"],
        baseline_id=raw["baseline_id"],
        dataset_version=raw["dataset_version"],
        evaluator_prompt_version=raw["evaluator_prompt_version"],
        model_aliases=model_aliases,
        retrieval_config=retrieval_config,
        metrics=metrics,
        review=ReviewMetadata(reviewer=review_raw["reviewer"], date=review_raw["date"], notes=review_raw["notes"]),
        raw=raw,
    )


# ── Baseline comparison (still read-only) ────────────────────────────

@dataclass(frozen=True)
class MetricComparison:
    name: str
    candidate: float | None
    baseline: float | None
    delta: float | None
    regressed: bool


@dataclass(frozen=True)
class BaselineComparison:
    comparisons: tuple[MetricComparison, ...]

    @property
    def regressions(self) -> tuple[MetricComparison, ...]:
        return tuple(c for c in self.comparisons if c.regressed)


def compare_to_baseline(summary: EvaluationSummary, baseline: Baseline) -> BaselineComparison:
    """Diff a candidate run against the reviewed baseline, metric by
    metric. Every metric here is higher-is-better, so `regressed` is True
    when the candidate is strictly worse than the baseline, OR when the
    baseline has a real value but the candidate has none at all (a metric
    that stopped being measured is a regression in its own right, not a
    pass by omission). A baseline value of `None` (not yet established -
    see `groundedness_rate` in `evals/baseline_v1.json`) has nothing to
    regress *against*, so it is never flagged here regardless of the
    candidate's value - `evaluate_gate`'s threshold check is what still
    applies to an unestablished metric."""
    field_values: dict[str, float | None] = {
        "hit_at_1": summary.hit_at_1,
        "hit_at_k": summary.hit_at_k,
        "mrr": summary.mrr,
        "citation_validity_rate": summary.citation_validity_rate,
        "refusal_accuracy_rate": summary.refusal_accuracy_rate,
        "groundedness_rate": summary.groundedness_rate,
    }
    comparisons = []
    for name, baseline_value in baseline.metrics.items():
        candidate_value = field_values.get(name)
        delta = (
            (candidate_value - baseline_value)
            if (candidate_value is not None and baseline_value is not None)
            else None
        )
        if baseline_value is None:
            regressed = False
        elif candidate_value is None:
            regressed = True
        else:
            regressed = candidate_value < baseline_value
        comparisons.append(
            MetricComparison(name=name, candidate=candidate_value, baseline=baseline_value, delta=delta, regressed=regressed)
        )
    return BaselineComparison(comparisons=tuple(comparisons))


def render_baseline_comparison(comparison: BaselineComparison) -> str:
    """Short, human-readable rendering of one `BaselineComparison` -
    counterpart to `render_gate_summary`."""
    lines: list[str] = []
    lines.append(f"BASELINE COMPARISON: {len(comparison.regressions)} regression(s)")
    for c in comparison.comparisons:
        candidate_str = f"{c.candidate:.4f}" if c.candidate is not None else "not measured"
        baseline_str = f"{c.baseline:.4f}" if c.baseline is not None else "not established"
        delta_str = f"{c.delta:+.4f}" if c.delta is not None else "n/a"
        flag = " **REGRESSED**" if c.regressed else ""
        lines.append(f"  {c.name}: candidate={candidate_str} baseline={baseline_str} delta={delta_str}{flag}")
    return "\n".join(lines)


# ── Baseline update — the ONE write path (Task 8) ────────────────────

def write_baseline(
    path: pathlib.Path,
    *,
    dataset_version: str,
    evaluator_prompt_version: str,
    model_aliases: dict,
    retrieval_config: dict,
    metrics: dict,
    reviewer: str,
    date: str,
    notes: str,
    baseline_id: str = "baseline_v1",
    version: str = "1.0",
) -> None:
    """THE ONLY function in this codebase allowed to write
    `evals/baseline_v1.json` - see the module docstring's Task 8 section.
    Nothing in `evaluate_gate`, `compare_to_baseline`, `aico.evals.metrics`,
    or `aico.evals.groundedness` ever calls this; it exists to be called
    from exactly one place: `scripts/day07_update_baseline.py`'s
    deliberate, separate, reviewable update workflow (never from a normal
    evaluation run, and never from CI - see evals/README.md)."""
    missing_metrics = [name for name in REQUIRED_METRIC_NAMES if name not in metrics]
    if missing_metrics:
        raise ValueError(f"write_baseline: missing metric value(s) for {missing_metrics}")
    if not reviewer.strip() or not date.strip() or not notes.strip():
        raise ValueError("write_baseline: reviewer, date and notes must all be non-empty - a baseline update is a reviewed act, not a silent write")

    payload = {
        "version": version,
        "baseline_id": baseline_id,
        "dataset_version": dataset_version,
        "evaluator_prompt_version": evaluator_prompt_version,
        "model_aliases": model_aliases,
        "retrieval_config": retrieval_config,
        "metrics": metrics,
        "review": {"reviewer": reviewer, "date": date, "notes": notes},
    }
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
