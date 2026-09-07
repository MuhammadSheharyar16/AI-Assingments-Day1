"""
Day 7 Task 6 — failure classification tests.

Each of the six required taxonomy types (`chunking`, `retrieval`, `prompt`,
`citation`, `refusal`, `evaluator`) is exercised in isolation with
synthetic Task 3/4 result objects, plus the "case passed" (`None`) path
and the deterministic precedence rule when more than one check would
otherwise have something to say. The generated artifact
(`artifacts/day07/failure_classification.md`) is produced by
`scripts/day07_generate_failure_classification_report.py` against the
real pipeline - exercised by running that script (see evals/README.md),
not re-simulated here; these tests cover the classifier library it calls.
"""
from __future__ import annotations

import pytest

from aico.evals.dataset import GoldenCase
from aico.evals.failure_classifier import FAILURE_TAXONOMY, classify_failure, render_failure_classification_report
from aico.evals.groundedness import GroundednessEvaluation, GroundednessEvaluationFailure, GroundednessVerdict
from aico.evals.metrics import AttackCheckResult, CitationCheckResult, RefusalCheckResult, RetrievalCaseResult, SourceMatch
from aico.rag.answer_service import GroundedAnswer, TypedFailure


def _case(**overrides) -> GoldenCase:
    defaults = dict(
        case_id="T-1", category="answerable", split="train", question="q",
        expected_sources=(), answerability="answerable", critical_facts=(), prohibited_claims=(),
    )
    defaults.update(overrides)
    return GoldenCase(**defaults)


def _passing_refusal(case_id="T-1") -> RefusalCheckResult:
    return RefusalCheckResult(case_id=case_id, expected_kind="grounded_answer", observed_kind="grounded_answer", passed=True, detail="")


def _failing_refusal(case_id="T-1", expected="grounded_answer", observed="insufficient_evidence") -> RefusalCheckResult:
    return RefusalCheckResult(case_id=case_id, expected_kind=expected, observed_kind=observed, passed=False, detail="")


def _retrieval_result(*, applicable=True, hit_at_k=True, source_matches=()) -> RetrievalCaseResult:
    return RetrievalCaseResult(
        case_id="T-1", category="answerable", split="train", applicable=applicable,
        source_matches=source_matches, first_hit_rank=(1 if hit_at_k else None),
        hit_at_1=hit_at_k, hit_at_k=hit_at_k, mrr=(1.0 if hit_at_k else 0.0),
        full_hit=hit_at_k, any_hit=hit_at_k,
    )


def test_taxonomy_has_exactly_the_six_required_types():
    assert set(FAILURE_TAXONOMY) == {"chunking", "retrieval", "prompt", "citation", "refusal", "evaluator"}


# ── Passing cases -> None ────────────────────────────────────────────

def test_passing_case_with_no_groundedness_check_returns_none():
    case = _case()
    result = classify_failure(case, result=GroundedAnswer(question="q", answer="a", citation_ids=("c1",), confidence_label="high", retrieved_ids=("c1",)), refusal=_passing_refusal())
    assert result is None


def test_passing_case_with_a_passing_groundedness_check_returns_none():
    case = _case()
    verdict = GroundednessVerdict(grounded=True, confidence="high", reasoning="ok")
    evaluation = GroundednessEvaluation(case_id="T-1", verdict=verdict, evaluator_model_alias="a", evaluator_prompt_version="1.0", latency_ms=1.0)
    result = classify_failure(case, refusal=_passing_refusal(), groundedness=evaluation)
    assert result is None


# ── evaluator ─────────────────────────────────────────────────────────

def test_evaluator_failure_only_classified_when_system_passed():
    case = _case()
    failure = GroundednessEvaluationFailure(case_id="T-1", failure_type="evaluator", stage="parse", category="malformed_json", message="response body is not valid JSON")
    result = classify_failure(case, refusal=_passing_refusal(), groundedness=failure)
    assert result is not None
    assert result.primary_type == "evaluator"
    assert result.case_id == "T-1"
    assert "malformed_json" in result.observed_failure


def test_evaluator_failure_never_masks_a_real_system_failure():
    # If the system itself failed, an ALSO-broken grader must not steal
    # the classification - the system-under-test failure comes first.
    case = _case()
    failure = GroundednessEvaluationFailure(case_id="T-1", failure_type="evaluator", stage="parse", category="malformed_json", message="boom")
    result = classify_failure(case, result=GroundedAnswer(question="q", answer="a", citation_ids=("c1",), confidence_label="high", retrieved_ids=("c1",)), refusal=_failing_refusal(), groundedness=failure)
    assert result.primary_type != "evaluator"


# ── chunking vs retrieval ────────────────────────────────────────────

def test_chunking_when_anchor_missing_from_the_full_index_too():
    case = _case()
    topk = _retrieval_result(hit_at_k=False, source_matches=(SourceMatch(doc_id="DOC-001", anchor="x", rank=None, matched_source_file=None),))
    full = _retrieval_result(hit_at_k=False, source_matches=(SourceMatch(doc_id="DOC-001", anchor="x", rank=None, matched_source_file=None),))
    result = classify_failure(case, refusal=_failing_refusal(), retrieval_topk=topk, retrieval_full_index=full)
    assert result.primary_type == "chunking"
    assert "not found in ANY chunk" in result.reason


def test_retrieval_when_anchor_exists_in_full_index_but_not_topk():
    case = _case()
    topk = _retrieval_result(hit_at_k=False, source_matches=(SourceMatch(doc_id="DOC-001", anchor="x", rank=None, matched_source_file=None),))
    full = _retrieval_result(hit_at_k=True, source_matches=(SourceMatch(doc_id="DOC-001", anchor="x", rank=9, matched_source_file="DOC-001-sourcing-policy.md"),))
    result = classify_failure(case, refusal=_failing_refusal(), retrieval_topk=topk, retrieval_full_index=full)
    assert result.primary_type == "retrieval"
    assert "ranked outside the top-k" in result.reason


def test_retrieval_not_applicable_cases_skip_straight_past_chunking_retrieval():
    case = _case(category="unanswerable", answerability="unanswerable")
    topk = _retrieval_result(applicable=False, hit_at_k=None)
    result = classify_failure(case, refusal=_failing_refusal(expected="insufficient_evidence", observed="grounded_answer"), retrieval_topk=topk)
    assert result.primary_type == "refusal"


# ── citation ──────────────────────────────────────────────────────────

def test_citation_failure_from_membership_check():
    case = _case()
    topk = _retrieval_result(hit_at_k=True)
    citations = CitationCheckResult(case_id="T-1", valid=False, forged_citation_ids=("c999",), cited_ids=("c999",), retrieved_ids=("c1",))
    result = classify_failure(case, refusal=_failing_refusal(), retrieval_topk=topk, citations=citations)
    assert result.primary_type == "citation"
    assert "c999" in result.reason


def test_citation_failure_from_typed_failure_stage():
    case = _case()
    typed_failure = TypedFailure(question="q", stage="citation", category="forged_citation", message="forged")
    result = classify_failure(case, result=typed_failure, refusal=_failing_refusal(), retrieval_topk=_retrieval_result(hit_at_k=True))
    assert result.primary_type == "citation"


# ── prompt ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("stage,category", [
    ("gateway", "timeout"),
    ("parse", "malformed_json"),
    ("contract", "missing_field"),
    ("semantic", "s3_duplicate_citation"),
])
def test_prompt_failure_for_non_citation_typed_failure_stages(stage, category):
    case = _case()
    typed_failure = TypedFailure(question="q", stage=stage, category=category, message="boom")
    result = classify_failure(case, result=typed_failure, refusal=_failing_refusal(), retrieval_topk=_retrieval_result(hit_at_k=True))
    assert result.primary_type == "prompt"
    assert category in result.observed_failure


# ── refusal ───────────────────────────────────────────────────────────

def test_refusal_failure_when_nothing_upstream_explains_it():
    case = _case(category="ambiguous", answerability="ambiguous")
    result = classify_failure(case, refusal=_failing_refusal(expected="clarify", observed="grounded_answer"))
    assert result.primary_type == "refusal"
    assert "clarify" in result.observed_failure


def test_refusal_failure_for_adversarial_uses_attack_check():
    case = _case(category="adversarial", answerability="must_refuse")
    attack = AttackCheckResult(case_id="T-1", passed=False, observed_kind="grounded_answer", detail="attack succeeded")
    result = classify_failure(case, attack=attack)
    assert result.primary_type == "refusal"
    assert result.reason == "attack succeeded"


# ── argument validation ──────────────────────────────────────────────

def test_classify_failure_requires_refusal_for_non_adversarial_cases():
    case = _case(category="answerable")
    with pytest.raises(ValueError, match="refusal"):
        classify_failure(case)


def test_classify_failure_requires_attack_for_adversarial_cases():
    case = _case(category="adversarial", answerability="must_refuse")
    with pytest.raises(ValueError, match="attack"):
        classify_failure(case, refusal=_passing_refusal())  # wrong kwarg for this category


# ── precedence: retrieval/chunking checked before citation/prompt ──────

def test_retrieval_miss_takes_precedence_over_a_citation_problem():
    case = _case()
    topk = _retrieval_result(hit_at_k=False, source_matches=(SourceMatch(doc_id="DOC-001", anchor="x", rank=None, matched_source_file=None),))
    full = _retrieval_result(hit_at_k=True, source_matches=(SourceMatch(doc_id="DOC-001", anchor="x", rank=20, matched_source_file="DOC-001-sourcing-policy.md"),))
    citations = CitationCheckResult(case_id="T-1", valid=False, forged_citation_ids=("c999",), cited_ids=("c999",), retrieved_ids=())
    result = classify_failure(case, refusal=_failing_refusal(), retrieval_topk=topk, retrieval_full_index=full, citations=citations)
    assert result.primary_type == "retrieval"  # not "citation" - the upstream cause wins


# ── report rendering ──────────────────────────────────────────────────

def test_render_report_with_no_failures():
    report = render_failure_classification_report([], total_cases=32, generated_by="test")
    assert "No failures to classify" in report
    assert "32" in report


def test_render_report_includes_summary_and_every_case():
    case = _case(case_id="GC-009", category="ambiguous", split="train")
    classification = classify_failure(case, refusal=_failing_refusal(case_id="GC-009", expected="clarify", observed="grounded_answer"))
    report = render_failure_classification_report([classification], total_cases=32, generated_by="test")
    assert "`GC-009`" in report
    assert "**refusal**" in report
    assert "| refusal | 1 |" in report
    assert "| chunking | 0 |" in report


def test_render_report_sorts_cases_by_id():
    c_b = classify_failure(_case(case_id="GC-020", category="ambiguous", answerability="ambiguous"), refusal=_failing_refusal(case_id="GC-020", expected="clarify", observed="grounded_answer"))
    c_a = classify_failure(_case(case_id="GC-005", category="ambiguous", answerability="ambiguous"), refusal=_failing_refusal(case_id="GC-005", expected="clarify", observed="grounded_answer"))
    report = render_failure_classification_report([c_b, c_a], total_cases=32, generated_by="test")
    assert report.index("GC-005") < report.index("GC-020")
