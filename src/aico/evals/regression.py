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
nothing about baselines or CI, only about turning numbers into a pass/fail
decision.
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
