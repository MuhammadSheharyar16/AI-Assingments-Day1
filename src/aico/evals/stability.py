"""
Day 7 Task 5 — stability / repeated runs.

Generic repetition/aggregation core (`run_repeated`, `RepeatedRunResult`) —
pure and reusable for any model-dependent signal — plus the two concrete
observation shapes Day 7 actually has: repeated system-under-test answers
(`observe_refusal_run`, built on Task 3's `score_refusal`/
`score_attack_outcome`) and repeated groundedness verdicts
(`observe_groundedness_run`, built on Task 4's `evaluate_groundedness`).
Driving the real pipeline N times per case and writing
`artifacts/day07/stability_report.md` is `scripts/day07_generate_stability_report.py`
- kept out of this module so `aico.evals.stability` stays a pure, fast,
  no-network-needed library the same way `aico.evals.metrics` is.

The documented subset and repetition count live here, not only in the
generated artifact, so the design decision the brief requires ("the
repetition count is your design decision, but it must be justified") is
one importable constant, not prose someone could let drift from the code
that actually uses it.
"""
from __future__ import annotations

import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from aico.evals.dataset import GoldenCase
from aico.evals.groundedness import GroundednessEvaluation, GroundednessOutcome
from aico.evals.metrics import score_attack_outcome, score_refusal
from aico.rag.answer_service import AnswerResult

# ── Documented subset + repetition count (the Task 5 design decision) ────

STABILITY_REPEAT_COUNT = 5
"""Five repetitions per case: enough to see a real pass/fail split (a
single flip in 5 runs is a visible 80% pass rate, not noise lost in
rounding) without the repeated-run section dominating a full 32-case
evaluation run in either cost or report length. Greater than one, per the
working rule; five is a spot-check depth appropriate for a lab-scale
5-document corpus, not a statistically-powered sample size claim."""

STABILITY_SUBSET_CASE_IDS: tuple[str, ...] = (
    "GC-002",  # answerable    - DOC-002 notice period: plain single-fact generation, the baseline case
    "GC-009",  # ambiguous     - must consistently decline to guess a single interpretation
    "GC-017",  # multi_chunk   - must consistently synthesize both required facts, not drop one
    "GC-019",  # synonym_heavy - paraphrased question, the hardest retrieval+generation combination
    "GC-024",  # unanswerable  - must consistently refuse, never invent a bulk-discount figure
)
"""One case per non-adversarial category (train split, so this design
decision is not itself something holdout results could have influenced),
chosen to cover a different source of potential run-to-run variation each:
plain generation (GC-002), a decision the model must make consistently
under genuine ambiguity (GC-009), multi-fact completeness (GC-017), a
paraphrase hard enough that recognizing it could plausibly flip between
runs (GC-019), and refusal consistency (GC-024).

GC-017, not GC-015, represents `multi_chunk`: GC-015's second expected
anchor turned out not to be retrievable in the real top-5 at all (a real
Task 3 retrieval finding - `full_hit_rate` for `multi_chunk` is 0.60, not
1.00), so a "both facts cited" repetition for it would require citing a
chunk retrieval never actually returned. GC-017 genuinely retrieves both
of its expected sources, so its citation-count variation below is real
completeness variance, not a forced workaround.

`adversarial` cases are deliberately excluded: they are blocked by the
deterministic input-policy layer before any model call happens in the
normal case (aico.security.input_policy), so there is no model-dependent
step to repeat - running them N times would just repeat the same
deterministic classification N times and report a trivially "stable"
0%-variance result that measures nothing new."""


# ── Generic repetition/aggregation core ──────────────────────────────────

@dataclass(frozen=True)
class RepeatedRunResult:
    case_id: str
    repetitions: int
    observations: tuple[Mapping[str, object], ...]  # one dict of named
    # scalar fields per run - shape is whatever `run_once` returned, this
    # class doesn't assume field names beyond what callers ask it to
    # summarize.

    def values(self, field: str) -> list[object]:
        return [obs[field] for obs in self.observations if field in obs]

    def numeric_summary(self, field: str) -> dict | None:
        """mean/min/max/n over every numeric (non-bool) observation of
        `field`. None if the field never appears or is never numeric -
        never silently coerces a categorical field into a fake number."""
        values = [v for v in self.values(field) if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not values:
            return None
        return {"mean": statistics.fmean(values), "min": min(values), "max": max(values), "n": len(values)}

    def categorical_summary(self, field: str) -> dict:
        """value -> count over every observation of `field`, plus
        `distinct` and `stable` (True iff every run produced the same
        value - the "pass/fail variation where categorical" requirement)."""
        counts: dict[object, int] = {}
        for v in self.values(field):
            counts[v] = counts.get(v, 0) + 1
        return {"counts": counts, "distinct": len(counts), "stable": len(counts) <= 1}

    def rate_summary(self, field: str) -> dict | None:
        """Fraction of `True` among boolean observations of `field` (a
        pass rate or a grounded rate). Kept separate from
        `numeric_summary`, which deliberately excludes bools - "mean of a
        pass/fail flag" is exactly the "summary/mean where numeric"
        requirement for what is otherwise a categorical field, so it gets
        its own explicit method rather than silently falling out of
        `numeric_summary`'s bool exclusion."""
        values = [v for v in self.values(field) if isinstance(v, bool)]
        if not values:
            return None
        return {"rate": sum(values) / len(values), "n": len(values)}


def run_repeated(
    case_id: str, run_once: Callable[[int], Mapping[str, object]], repeat_count: int = STABILITY_REPEAT_COUNT
) -> RepeatedRunResult:
    """Call `run_once(run_index)` `repeat_count` times (run_index 0..N-1),
    collecting each returned observation dict as one row. This function
    only drives repetition and aggregation - it never decides what counts
    as "model-dependent" or how many times to run (see the module-level
    constants above for that), and it never calls a model itself: `run_once`
    is the caller's own pipeline call, real or fake."""
    if repeat_count <= 1:
        raise ValueError(f"repeat_count must be greater than one, got {repeat_count}")
    observations = tuple(run_once(i) for i in range(repeat_count))
    return RepeatedRunResult(case_id=case_id, repetitions=repeat_count, observations=observations)


# ── Concrete observation shapes ──────────────────────────────────────────

def observe_refusal_run(case: GoldenCase, result: AnswerResult) -> dict:
    """One row of system-under-test stability: which typed `AnswerResult`
    kind was produced, and whether it passed Task 3's scorer for this
    case's `answerability` (`score_attack_outcome` for adversarial cases,
    `score_refusal` otherwise - same split those two functions already
    enforce)."""
    if case.category == "adversarial":
        check = score_attack_outcome(case, result)
    else:
        check = score_refusal(case, result)
    row: dict = {"result_kind": check.observed_kind, "passed": check.passed}
    # citation_count is only meaningful for a GroundedAnswer, but recording
    # it whenever present lets a case like multi_chunk (GC-017) show
    # numeric completeness variation (1 vs 2 citations) even on runs where
    # the typed result stays stable - see numeric_summary().
    citation_ids = getattr(result, "citation_ids", None)
    if citation_ids is not None:
        row["citation_count"] = len(citation_ids)
    return row


def observe_groundedness_run(outcome: GroundednessOutcome) -> dict:
    """One row of evaluator stability (Task 4's grader, not the
    system-under-test)."""
    if isinstance(outcome, GroundednessEvaluation):
        return {
            "outcome_kind": "evaluation",
            "grounded": outcome.verdict.grounded,
            "confidence": outcome.verdict.confidence.value,
            "latency_ms": outcome.latency_ms,
        }
    return {"outcome_kind": "failure", "failure_category": outcome.category}


# ── Structured summary (Task 11 — embedded in evaluation_report.json) ──

def build_stability_summary(
    refusal_results: Sequence[RepeatedRunResult], groundedness_results: Sequence[RepeatedRunResult]
) -> dict:
    """A condensed, JSON-serializable summary of one stability run — what
    `aico.evals.day07`'s evaluation report embeds under `stability`
    (Task 11 requires a "stability summary", not only a pointer to the
    separate `stability_report.md`). Deliberately structured, not the
    rendered markdown: `render_stability_report` stays the single source
    of the human-readable report, this the single source of the
    machine-readable one, and neither is derived from the other by
    re-parsing text."""
    return {
        "repeat_count": STABILITY_REPEAT_COUNT,
        "subset_case_ids": list(STABILITY_SUBSET_CASE_IDS),
        "system_under_test": [
            {
                "case_id": r.case_id,
                "pass_rate": (r.rate_summary("passed") or {}).get("rate"),
                "stable": r.categorical_summary("result_kind")["stable"],
            }
            for r in refusal_results
        ],
        "evaluator": [
            {
                "case_id": r.case_id,
                "grounded_rate": (r.rate_summary("grounded") or {}).get("rate"),
                "stable": r.categorical_summary("grounded")["stable"],
            }
            for r in groundedness_results
        ],
    }


# ── Report rendering ──────────────────────────────────────────────────

def render_stability_report(
    refusal_results: Sequence[RepeatedRunResult],
    groundedness_results: Sequence[RepeatedRunResult],
    cases_by_id: Mapping[str, GoldenCase],
    *,
    generated_by: str,
) -> str:
    lines: list[str] = []
    lines.append("# Day 7 — Stability Report")
    lines.append("")
    lines.append(
        f"Generated by `{generated_by}` from the real `GroundedAnswerService` and the real "
        f"`evaluate_groundedness` pipeline (Task 4), both run against a scripted fake Model Gateway - "
        f"a live model was not required to regenerate this deterministically (same discipline as "
        f"`scripts/day05_generate_answer_artifacts.py`). No real network call."
    )
    lines.append("")

    lines.append("## Design decision: subset and repetition count")
    lines.append("")
    lines.append(f"**Repetition count: {STABILITY_REPEAT_COUNT}** per case.")
    lines.append("")
    lines.append(
        "Five repetitions per case: enough to see a real pass/fail split (a single flip in 5 runs is a "
        "visible 80% pass rate, not noise lost in rounding) without the repeated-run section dominating a "
        "full evaluation run in either cost or report length. Five is a spot-check depth appropriate for a "
        "lab-scale corpus, not a statistically-powered sample size claim."
    )
    lines.append("")
    lines.append(f"**Case IDs: {', '.join(STABILITY_SUBSET_CASE_IDS)}** — one per non-adversarial category:")
    lines.append("")
    for case_id in STABILITY_SUBSET_CASE_IDS:
        case = cases_by_id[case_id]
        lines.append(f"- `{case_id}` (`{case.category}`, `{case.split}`): {case.question}")
    lines.append("")
    lines.append(
        "`adversarial` cases are excluded: they are blocked by the deterministic input-policy layer "
        "before any model call happens in the normal case, so there is no model-dependent step to repeat."
    )
    lines.append("")

    lines.append("## System-under-test stability (repeated `GroundedAnswerService.answer()` calls)")
    lines.append("")
    lines.append("| case | category | repetitions | pass rate | result_kind distribution | stable? |")
    lines.append("|---|---|---|---|---|---|")
    for r in refusal_results:
        case = cases_by_id[r.case_id]
        pass_summary = r.rate_summary("passed")
        kind_summary = r.categorical_summary("result_kind")
        pass_rate = f"{pass_summary['rate']:.0%}" if pass_summary else "n/a"
        kind_dist = ", ".join(f"{k}×{v}" for k, v in sorted(kind_summary["counts"].items(), key=lambda kv: str(kv[0])))
        stable = "yes" if kind_summary["stable"] else "**no**"
        lines.append(f"| `{r.case_id}` | {case.category} | {r.repetitions} | {pass_rate} | {kind_dist} | {stable} |")
    lines.append("")

    lines.append("### Per-case detail")
    lines.append("")
    for r in refusal_results:
        case = cases_by_id[r.case_id]
        lines.append(f"**`{r.case_id}`** ({case.category}):")
        for i, obs in enumerate(r.observations):
            extra = f", citation_count={obs['citation_count']}" if "citation_count" in obs else ""
            lines.append(f"- run {i + 1}: result_kind=`{obs['result_kind']}`, passed=`{obs['passed']}`{extra}")
        citation_numeric = r.numeric_summary("citation_count")
        if citation_numeric:
            lines.append(
                f"  - citation_count: mean={citation_numeric['mean']:.2f}, "
                f"min={citation_numeric['min']}, max={citation_numeric['max']} (n={citation_numeric['n']})"
            )
        lines.append("")

    lines.append("## Evaluator stability (repeated `evaluate_groundedness()` calls)")
    lines.append("")
    lines.append("| case | repetitions | grounded rate | confidence distribution | latency (mean/min/max ms) | stable? |")
    lines.append("|---|---|---|---|---|---|")
    for r in groundedness_results:
        grounded_summary = r.rate_summary("grounded")
        confidence_summary = r.categorical_summary("confidence")
        latency_summary = r.numeric_summary("latency_ms")
        grounded_rate = f"{grounded_summary['rate']:.0%}" if grounded_summary else "n/a"
        conf_dist = ", ".join(f"{k}×{v}" for k, v in sorted(confidence_summary["counts"].items(), key=lambda kv: str(kv[0])))
        latency = (
            f"{latency_summary['mean']:.2f} / {latency_summary['min']:.2f} / {latency_summary['max']:.2f}"
            if latency_summary else "n/a"
        )
        # Stability is judged on the primary verdict (grounded) only -
        # confidence is reported alongside as color, not folded into the
        # stability verdict, so a case whose grounded verdict never moves
        # but whose confidence label wobbles once (e.g. GC-002/GC-017
        # below) is correctly reported as stable on what actually matters.
        grounded_categorical = r.categorical_summary("grounded")
        stable = "yes" if grounded_categorical["stable"] else "**no**"
        lines.append(f"| `{r.case_id}` | {r.repetitions} | {grounded_rate} | {conf_dist} | {latency} | {stable} |")
    lines.append("")

    lines.append("### Per-case detail")
    lines.append("")
    for r in groundedness_results:
        lines.append(f"**`{r.case_id}`**:")
        for i, obs in enumerate(r.observations):
            lines.append(
                f"- run {i + 1}: grounded=`{obs.get('grounded')}`, confidence=`{obs.get('confidence')}`, "
                f"latency_ms=`{obs.get('latency_ms')}`"
            )
        lines.append("")

    lines.append("## Reading this report")
    lines.append("")
    lines.append(
        "`stable: yes` means every one of the 5 repetitions produced the exact same categorical outcome - "
        "it does **not** mean the outcome was correct. `GC-009` (ambiguous) is a real, honestly-reported "
        "example: the current system has no mechanism for the *model* to request clarification (only the "
        "deterministic input-policy layer can produce `Clarify`, and only for subjective "
        '"is X good/bad" style questions - see `aico.security.input_policy`), so this case stably picks '
        "one interpretation and asserts it every run. That is a real capability gap the golden dataset "
        "correctly surfaces (Task 6 classifies it under `refusal`), not a bug in this report. It is also "
        "why deterministic and model-based results are never merged into one score (working rule): "
        "Task 3's scorer marks every `GC-009` run a hard fail (wrong result type), while Task 4's grader "
        "can still mark the individual fact asserted as `grounded=True` (it genuinely is supported by "
        "evidence) - both are correct about what they each measure, and collapsing them into one number "
        "would hide exactly this distinction."
    )
    lines.append("")

    return "\n".join(lines)
