"""
Day 7 Task 6 — failure classification.

Every failed case gets exactly one primary failure type, from the fixed
six-value taxonomy the brief requires:

    chunking | retrieval | prompt | citation | refusal | evaluator

`classify_failure()` is the single entry point: given a case plus the
already-computed Task 3 (`aico.evals.metrics`) / Task 4
(`aico.evals.groundedness`) check results for it, it returns `None` (the
case passed every applicable check) or a `FailureClassification` naming
exactly one primary type and short, evidence-based reasoning. It never
invents a seventh category, and it never assigns more than one type to
the same case - see `PRECEDENCE` below for the deterministic order this
module applies when more than one check would otherwise have something
to say about the same failure.

This module makes no model call and needs no index - it is a pure
classifier over already-computed results, the same "library stays fast,
a script drives the real pipeline" split `aico.evals.stability` uses.
`scripts/day07_generate_failure_classification_report.py` is what
actually runs cases and writes `artifacts/day07/failure_classification.md`.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from aico.evals.dataset import GoldenCase
from aico.evals.groundedness import GroundednessEvaluationFailure, GroundednessOutcome
from aico.evals.metrics import AttackCheckResult, CitationCheckResult, RefusalCheckResult, RetrievalCaseResult
from aico.rag.answer_service import AnswerResult, TypedFailure

FAILURE_TAXONOMY: tuple[str, ...] = ("chunking", "retrieval", "prompt", "citation", "refusal", "evaluator")

# The deterministic order this module checks for a cause, most-upstream
# pipeline stage first: a corpus-build defect (chunking) or a ranking miss
# (retrieval) explains a downstream symptom (the model had nothing to cite
# so it refused, or invented) better than blaming the symptom itself, so
# those are checked before citation/prompt/refusal. `evaluator` is
# orthogonal to the system-under-test pipeline entirely (it's about
# whether *grading* succeeded, not the answer being graded), so it is only
# ever assigned when the system-under-test's own behavior was correct -
# a broken grader never masks a genuine system-under-test failure, and a
# real system-under-test failure is never re-attributed to the grader.
PRECEDENCE = ("evaluator (only if system passed)", "chunking", "retrieval", "citation", "prompt", "refusal")


@dataclass(frozen=True)
class FailureClassification:
    case_id: str
    split: str
    category: str
    observed_failure: str  # short, machine-oriented description of what went wrong
    primary_type: str  # exactly one of FAILURE_TAXONOMY
    reason: str  # short, evidence-based justification


def classify_failure(
    case: GoldenCase,
    *,
    result: AnswerResult | None = None,
    retrieval_topk: RetrievalCaseResult | None = None,
    retrieval_full_index: RetrievalCaseResult | None = None,
    citations: CitationCheckResult | None = None,
    refusal: RefusalCheckResult | None = None,
    attack: AttackCheckResult | None = None,
    groundedness: GroundednessOutcome | None = None,
) -> FailureClassification | None:
    """Classify one case's evaluation outcome. Exactly one of `refusal`/
    `attack` must be given, matching Task 3's own split (`attack` for
    `category == "adversarial"`, `refusal` otherwise) - both are optional
    parameters only so a caller need not import both result types when it
    only has one on hand, not because either is skippable for its case
    type.

    Returns `None` when the case passed - both its refusal/attack-outcome
    check and (if attempted) its groundedness evaluation."""
    behavior_check = attack if case.category == "adversarial" else refusal
    if behavior_check is None:
        raise ValueError(
            f"{case.case_id}: classify_failure needs {'an attack' if case.category == 'adversarial' else 'a refusal'} "
            f"check result for category={case.category!r}"
        )

    system_failed = not behavior_check.passed

    if not system_failed:
        # The system-under-test behaved correctly. The only thing left
        # that could still have failed is grading itself.
        if isinstance(groundedness, GroundednessEvaluationFailure):
            return FailureClassification(
                case_id=case.case_id,
                split=case.split,
                category=case.category,
                observed_failure=f"groundedness evaluator failed ({groundedness.stage}:{groundedness.category})",
                primary_type="evaluator",
                reason=groundedness.message,
            )
        return None

    # The system-under-test failed. Find the most upstream cause.

    # 1. chunking / retrieval - only meaningful when this case has
    #    expected_sources to score retrieval against and the actual
    #    retrieval window missed all of them.
    if retrieval_topk is not None and retrieval_topk.applicable and retrieval_topk.hit_at_k is False:
        missing_anchors = [f"{m.doc_id}: {m.anchor!r}" for m in retrieval_topk.source_matches if m.rank is None]
        if retrieval_full_index is not None and retrieval_full_index.applicable and retrieval_full_index.hit_at_k is False:
            return FailureClassification(
                case_id=case.case_id, split=case.split, category=case.category,
                observed_failure="expected source not retrievable at all, in or out of the scored window",
                primary_type="chunking",
                reason=(
                    f"anchor(s) not found in ANY chunk of the full index, not just outside the retrieved "
                    f"top-k - the source text did not survive chunking as one coherent chunk: {missing_anchors}"
                ),
            )
        return FailureClassification(
            case_id=case.case_id, split=case.split, category=case.category,
            observed_failure="expected source not retrieved within the scored window",
            primary_type="retrieval",
            reason=f"anchor(s) exist correctly chunked elsewhere in the index but ranked outside the top-k: {missing_anchors}",
        )

    # 2. citation - a forged/invalid citation, whether caught by Task 3's
    #    membership check directly or surfaced as a citation-stage
    #    TypedFailure from the pipeline itself.
    if citations is not None and not citations.valid:
        return FailureClassification(
            case_id=case.case_id, split=case.split, category=case.category,
            observed_failure="citation validation failed",
            primary_type="citation",
            reason=f"forged citation id(s) not present in retrieved evidence: {list(citations.forged_citation_ids)}",
        )
    if isinstance(result, TypedFailure) and result.stage == "citation":
        return FailureClassification(
            case_id=case.case_id, split=case.split, category=case.category,
            observed_failure=f"typed failure at citation stage ({result.category})",
            primary_type="citation",
            reason=result.message,
        )

    # 3. prompt - the model call didn't complete, or its output didn't
    #    follow the prompt's required JSON shape/semantic rules. Not a
    #    perfect taxonomy fit for a raw gateway failure (timeout, auth) -
    #    the required six categories have no "infrastructure" bucket - but
    #    it is the closest one: the failure happened while executing this
    #    case's prompt/model-call step and produced no citation-stage or
    #    refusal-decision problem of its own to blame instead.
    if isinstance(result, TypedFailure):
        return FailureClassification(
            case_id=case.case_id, split=case.split, category=case.category,
            observed_failure=f"typed failure at {result.stage} stage ({result.category})",
            primary_type="prompt",
            reason=result.message,
        )

    # 4. refusal - retrieval and citation/prompt above found nothing to
    #    blame, so what's left is the answer/refuse decision itself: the
    #    wrong AnswerResult *type* was produced for this case's expected
    #    behavior (invented an answer instead of refusing, refused instead
    #    of answering, answered instead of asking for clarification, or an
    #    attack got a confident answer instead of being safely declined).
    expected = getattr(behavior_check, "expected_kind", "a safe outcome")
    return FailureClassification(
        case_id=case.case_id, split=case.split, category=case.category,
        observed_failure=f"expected {expected}, observed {behavior_check.observed_kind}",
        primary_type="refusal",
        reason=behavior_check.detail or "wrong result type for this case's expected behavior",
    )


# ── Report rendering ──────────────────────────────────────────────────

def render_failure_classification_report(
    classifications: Sequence[FailureClassification], total_cases: int, *, generated_by: str
) -> str:
    lines: list[str] = []
    lines.append("# Day 7 — Failure Classification")
    lines.append("")
    lines.append(
        f"Generated by `{generated_by}`. {len(classifications)} of {total_cases} evaluated case(s) failed; "
        f"every failure below carries exactly one primary type from the required taxonomy "
        f"(`{'`, `'.join(FAILURE_TAXONOMY)}`)."
    )
    lines.append("")

    if not classifications:
        lines.append("No failures to classify - every evaluated case passed.")
        lines.append("")
        return "\n".join(lines)

    lines.append("## Summary by primary type")
    lines.append("")
    lines.append("| primary type | count |")
    lines.append("|---|---|")
    counts: dict[str, int] = {t: 0 for t in FAILURE_TAXONOMY}
    for c in classifications:
        counts[c.primary_type] = counts.get(c.primary_type, 0) + 1
    for t in FAILURE_TAXONOMY:
        lines.append(f"| {t} | {counts[t]} |")
    lines.append("")

    lines.append("## Every failed case")
    lines.append("")
    lines.append("| case ID | split | category | observed failure | primary type | reason |")
    lines.append("|---|---|---|---|---|---|")
    for c in sorted(classifications, key=lambda c: c.case_id):
        lines.append(
            f"| `{c.case_id}` | {c.split} | {c.category} | {c.observed_failure} | **{c.primary_type}** | {c.reason} |"
        )
    lines.append("")

    return "\n".join(lines)
