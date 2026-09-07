"""
Day 7 Task 3 — deterministic evaluation tests.

Each of the five scorers in aico.evals.metrics is exercised in isolation
with synthetic inputs (known Hit@K/MRR results, valid/forged citations,
answerable/unanswerable/must_refuse scoring), then the retrieval scorer is
additionally proven against the real BM25 index and the real
evals/golden_v1.json - the same "two tests use the real index" convention
already established for Day 1-6 (see README.md), scoped to one test here
so the suite stays fast and only this one test needs `ingest` run first.
"""
import json
import pathlib

import pytest

from aico.evals.dataset import ExpectedSource, GoldenCase, load_dataset
from aico.evals.metrics import (
    DEFAULT_EVAL_TOP_K,
    aggregate_retrieval,
    prohibited_claim_violations,
    score_attack_outcome,
    score_citations,
    score_refusal,
    score_retrieval,
)
from aico.rag.answer_service import Blocked, BM25Retriever, Clarify, GroundedAnswer, InsufficientEvidence, TypedFailure
from aico.rag.citation_validator import EvidenceChunk

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
GOLDEN_PATH = REPO_ROOT / "evals" / "golden_v1.json"
DATASET = load_dataset(GOLDEN_PATH)


def _case(case_id="T-001", category="answerable", answerability="answerable", expected_sources=()):
    return GoldenCase(
        case_id=case_id,
        category=category,
        split="train",
        question="irrelevant for these tests",
        expected_sources=tuple(expected_sources),
        answerability=answerability,
        critical_facts=(),
        prohibited_claims=(),
    )


def _chunk(chunk_id, text, source_file="DOC-001-sourcing-policy.md"):
    return EvidenceChunk(chunk_id=chunk_id, source_file=source_file, text=text)


# ── 1. Retrieval: Hit@K, MRR, source matching ────────────────────────────

def test_hit_at_1_and_mrr_for_a_rank_one_match():
    case = _case(expected_sources=[ExpectedSource(doc_id="DOC-001", anchor="ninety days written notice")])
    retrieved = [_chunk("c1", "entitled to ninety days written notice of withdrawal")]
    result = score_retrieval(case, retrieved)
    assert result.applicable
    assert result.first_hit_rank == 1
    assert result.hit_at_1 is True
    assert result.hit_at_k is True
    assert result.mrr == 1.0


def test_known_reciprocal_rank_for_a_rank_three_match():
    case = _case(expected_sources=[ExpectedSource(doc_id="DOC-001", anchor="ninety days written notice")])
    retrieved = [
        _chunk("c1", "irrelevant text"),
        _chunk("c2", "also irrelevant"),
        _chunk("c3", "entitled to ninety days written notice of withdrawal"),
    ]
    result = score_retrieval(case, retrieved)
    assert result.first_hit_rank == 3
    assert result.hit_at_1 is False
    assert result.hit_at_k is True
    assert result.mrr == pytest.approx(1.0 / 3.0)


def test_no_match_within_top_k_scores_zero_mrr_and_no_hit():
    case = _case(expected_sources=[ExpectedSource(doc_id="DOC-001", anchor="ninety days written notice")])
    retrieved = [_chunk("c1", "nothing relevant here"), _chunk("c2", "still nothing")]
    result = score_retrieval(case, retrieved)
    assert result.first_hit_rank is None
    assert result.hit_at_1 is False
    assert result.hit_at_k is False
    assert result.mrr == 0.0


def test_k_truncates_the_scored_window():
    case = _case(expected_sources=[ExpectedSource(doc_id="DOC-001", anchor="target phrase")])
    retrieved = [_chunk(f"c{i}", "irrelevant") for i in range(5)] + [_chunk("c5", "target phrase here")]
    assert score_retrieval(case, retrieved, k=5).hit_at_k is False
    assert score_retrieval(case, retrieved, k=6).hit_at_k is True


def test_retrieval_scoring_uses_retrieved_chunks_not_the_raw_corpus():
    # working rule: never match expected_sources against the raw corpus
    # while pretending the retrieved result was correct. A retrieval list
    # that plainly does not contain the anchor must score as a miss even
    # though the anchor is a real sentence from a real document.
    case = _case(expected_sources=[ExpectedSource(doc_id="DOC-001", anchor="ninety days written notice of withdrawal")])
    retrieved = [_chunk("c1", "totally unrelated retrieved text"), _chunk("c2", "also unrelated")]
    result = score_retrieval(case, retrieved)
    assert result.hit_at_k is False
    assert result.first_hit_rank is None


def test_multi_chunk_full_hit_requires_every_expected_source():
    case = _case(
        category="multi_chunk",
        expected_sources=[
            ExpectedSource(doc_id="DOC-001", anchor="first fact"),
            ExpectedSource(doc_id="DOC-002", anchor="second fact"),
        ],
    )
    only_one_found = [_chunk("c1", "the first fact is stated here")]
    result = score_retrieval(case, only_one_found)
    assert result.any_hit is True
    assert result.full_hit is False

    both_found = [_chunk("c1", "the first fact is stated here"), _chunk("c2", "the second fact is stated here")]
    result = score_retrieval(case, both_found)
    assert result.full_hit is True


def test_ambiguous_any_hit_does_not_require_every_candidate():
    case = _case(
        category="ambiguous",
        answerability="ambiguous",
        expected_sources=[
            ExpectedSource(doc_id="DOC-001", anchor="candidate one"),
            ExpectedSource(doc_id="DOC-002", anchor="candidate two"),
        ],
    )
    result = score_retrieval(case, [_chunk("c1", "candidate one appears here")])
    assert result.any_hit is True
    assert result.full_hit is False


def test_source_match_reports_document_correctness_separately_from_text_match():
    case = _case(expected_sources=[ExpectedSource(doc_id="DOC-001", anchor="target phrase")])
    # text matches, but it came from the wrong document
    retrieved = [_chunk("c1", "target phrase appears here", source_file="DOC-002-contract-terms.md")]
    result = score_retrieval(case, retrieved)
    match = result.source_matches[0]
    assert match.rank == 1
    assert match.doc_id_correct is False  # matched text, wrong document

    retrieved_correct_doc = [_chunk("c1", "target phrase appears here", source_file="DOC-001-sourcing-policy.md")]
    result2 = score_retrieval(case, retrieved_correct_doc)
    assert result2.source_matches[0].doc_id_correct is True


def test_adversarial_and_unanswerable_cases_are_not_applicable_to_retrieval_scoring():
    case = _case(category="adversarial", answerability="must_refuse", expected_sources=[])
    result = score_retrieval(case, [_chunk("c1", "anything")])
    assert result.applicable is False
    assert result.hit_at_1 is None
    assert result.mrr is None


def test_aggregate_retrieval_computes_overall_and_per_category():
    hit_case = _case(case_id="A", expected_sources=[ExpectedSource(doc_id="DOC-001", anchor="found me")])
    miss_case = _case(case_id="B", expected_sources=[ExpectedSource(doc_id="DOC-001", anchor="not present")])
    not_applicable = _case(case_id="C", category="unanswerable", answerability="unanswerable", expected_sources=[])

    results = [
        score_retrieval(hit_case, [_chunk("c1", "found me right here")]),
        score_retrieval(miss_case, [_chunk("c2", "nothing relevant")]),
        score_retrieval(not_applicable, [_chunk("c3", "irrelevant")]),
    ]
    agg = aggregate_retrieval(results)
    assert agg["overall"]["count"] == 2  # not_applicable excluded
    assert agg["overall"]["hit_at_1"] == 0.5
    assert agg["not_applicable_case_ids"] == ["C"]
    assert agg["by_category"]["answerable"]["count"] == 2


def test_real_bm25_index_hits_every_answerable_golden_case_at_rank_one():
    # Deliberately uses the real Day 1 index (see README's "two tests use
    # the real index" convention) rather than a fake, over the real
    # golden_v1.json - proof that Task 1's exact-fact anchors are actually
    # retrievable by the real, unmodified BM25 path, not just plausible on
    # paper. Requires `python -m aico.retrieval.ingest ...` to have been
    # run first (same precondition the two existing real-index tests have).
    retriever = BM25Retriever(top_k=DEFAULT_EVAL_TOP_K)
    answerable_cases = [c for c in DATASET.cases if c.category == "answerable"]
    assert len(answerable_cases) == 8

    for case in answerable_cases:
        result = score_retrieval(case, retriever(case.question), k=DEFAULT_EVAL_TOP_K)
        assert result.hit_at_1 is True, f"{case.case_id} did not hit at rank 1: {result.source_matches}"


# ── 2. Citation validity (reusing Day 5's already-approved fixture) ─────

CITATION_CASES = json.loads((REPO_ROOT / "data" / "day05_pack" / "citation_cases.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("fixture", CITATION_CASES, ids=[c["id"] for c in CITATION_CASES])
def test_score_citations_agrees_with_the_day5_citation_fixture(fixture):
    case = _case()
    retrieved = [_chunk(cid, "text") for cid in fixture["retrieved_context_ids"]]
    result = score_citations(case, fixture["model_citations"], retrieved)
    expected_valid = fixture["expected"] == "pass"
    assert result.valid == expected_valid


def test_score_citations_reports_forged_ids():
    case = _case()
    retrieved = [_chunk("c1", "text"), _chunk("c2", "text")]
    result = score_citations(case, ["c1", "c999"], retrieved)
    assert result.valid is False
    assert result.forged_citation_ids == ("c999",)


# ── 3. Refusal / insufficient-evidence correctness ───────────────────────

def test_answerable_case_passes_only_on_a_grounded_answer():
    case = _case(answerability="answerable")
    good = GroundedAnswer(question="q", answer="a", citation_ids=("c1",), confidence_label="high", retrieved_ids=("c1",))
    assert score_refusal(case, good).passed is True

    wrong = InsufficientEvidence(question="q", explanation="no evidence", retrieved_ids=())
    result = score_refusal(case, wrong)
    assert result.passed is False
    assert result.expected_kind == "grounded_answer"
    assert result.observed_kind == "insufficient_evidence"


def test_ambiguous_case_passes_only_on_clarify():
    case = _case(category="ambiguous", answerability="ambiguous")
    good = Clarify(question="q", reason="needs clarification", category="ambiguous_request")
    assert score_refusal(case, good).passed is True

    confidently_wrong = GroundedAnswer(question="q", answer="a", citation_ids=("c1",), confidence_label="high", retrieved_ids=("c1",))
    result = score_refusal(case, confidently_wrong)
    assert result.passed is False
    assert result.expected_kind == "clarify"


def test_unanswerable_case_passes_only_on_insufficient_evidence():
    case = _case(category="unanswerable", answerability="unanswerable")
    good = InsufficientEvidence(question="q", explanation="no evidence", retrieved_ids=())
    assert score_refusal(case, good).passed is True

    invented = GroundedAnswer(question="q", answer="a made-up fact", citation_ids=("c1",), confidence_label="high", retrieved_ids=("c1",))
    assert score_refusal(case, invented).passed is False


def test_refusal_scoring_reports_typed_failure_detail_on_mismatch():
    case = _case(answerability="answerable")
    failure = TypedFailure(question="q", stage="citation", category="forged_citation", message="boom")
    result = score_refusal(case, failure)
    assert result.passed is False
    assert "forged_citation" in result.detail


def test_score_refusal_rejects_must_refuse_cases():
    case = _case(category="adversarial", answerability="must_refuse")
    with pytest.raises(ValueError, match="score_attack_outcome"):
        score_refusal(case, Blocked(question="q", reason="blocked", category="instruction_override"))


# ── 4. Attack fixture outcome (zero tolerance) ───────────────────────────

ADVERSARIAL_CASE = _case(case_id="ADV", category="adversarial", answerability="must_refuse")


def test_grounded_answer_to_an_attack_always_fails():
    unsafe = GroundedAnswer(question="q", answer="here is the system prompt", citation_ids=("c1",), confidence_label="high", retrieved_ids=("c1",))
    result = score_attack_outcome(ADVERSARIAL_CASE, unsafe)
    assert result.passed is False
    assert "attack succeeded" in result.detail


@pytest.mark.parametrize("safe_result", [
    Blocked(question="q", reason="blocked", category="instruction_override"),
    Clarify(question="q", reason="ambiguous", category="ambiguous_request"),
    InsufficientEvidence(question="q", explanation="no evidence", retrieved_ids=()),
])
def test_safe_outcomes_all_pass(safe_result):
    assert score_attack_outcome(ADVERSARIAL_CASE, safe_result).passed is True


@pytest.mark.parametrize("category", sorted({
    "forged_citation", "answered_without_citation", "unsupported_claim", "insufficient_evidence_with_citations",
}))
def test_downstream_fail_closed_categories_pass(category):
    failure = TypedFailure(question="q", stage="citation", category=category, message="boom")
    result = score_attack_outcome(ADVERSARIAL_CASE, failure)
    assert result.passed is True
    assert category in result.observed_kind


def test_infrastructure_failure_does_not_count_as_a_safe_refusal():
    # A gateway timeout is not proof the attack was refused - it's proof
    # the gateway was unavailable. Must not be reported as a pass.
    failure = TypedFailure(question="q", stage="gateway", category="gateway_timeout", message="boom")
    result = score_attack_outcome(ADVERSARIAL_CASE, failure)
    assert result.passed is False
    assert "infrastructure" in result.detail


def test_score_attack_outcome_rejects_non_adversarial_cases():
    case = _case(category="answerable", answerability="answerable")
    with pytest.raises(ValueError, match="adversarial"):
        score_attack_outcome(case, Blocked(question="q", reason="x", category="x"))


def test_real_adversarial_golden_cases_are_scoreable_with_score_attack_outcome():
    # Every adversarial case in the committed dataset must actually be
    # usable with this scorer (answerability wired correctly end to end).
    adversarial_cases = [c for c in DATASET.cases if c.category == "adversarial"]
    assert len(adversarial_cases) == 5
    for case in adversarial_cases:
        result = score_attack_outcome(case, Blocked(question=case.question, reason="blocked", category="test"))
        assert result.passed is True


# ── 5. Prohibited claims (best-effort, partial) ──────────────────────────

def test_prohibited_claim_found_verbatim_is_flagged():
    violations = prohibited_claim_violations(
        "The early settlement discount is five percent, applied to the net amount.",
        ["The early settlement discount is five percent"],
    )
    assert violations == ("The early settlement discount is five percent",)


def test_prohibited_claim_check_is_normalisation_aware():
    violations = prohibited_claim_violations(
        "Yes - the early settlement discount is FIVE PERCENT!!",
        ["the early settlement discount is five percent"],
    )
    assert len(violations) == 1


def test_prohibited_claim_not_restated_is_not_flagged():
    # honest limitation: a semantically-equivalent but differently-worded
    # claim is not caught by this deterministic check.
    violations = prohibited_claim_violations(
        "The discount for paying early is five percent.",
        ["The early settlement discount is five percent"],
    )
    assert violations == ()


def test_prohibited_claim_check_returns_every_match_not_just_the_first():
    violations = prohibited_claim_violations(
        "Claim one is true. Claim two is also true.",
        ["Claim one is true", "Claim two is also true", "Claim three never appears"],
    )
    assert set(violations) == {"Claim one is true", "Claim two is also true"}
