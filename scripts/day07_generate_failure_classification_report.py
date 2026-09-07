"""
Day 7 Task 6 — failure classification report generator.

Run: uv run python scripts/day07_generate_failure_classification_report.py

Runs the real `GroundedAnswerService` (Day 5, unmodified) once per case in
`evals/golden_v1.json`, against the real, unchanged input policy and the
real `BM25Retriever` - only the Model Gateway is fake, driven by one
generic, honest "well-behaved model" response builder (`_well_behaved_response`
below): if the retrieved evidence actually contains an expected source's
anchor text, answer from it and cite it; otherwise decline. This is not
scripted per case to produce a particular verdict - it reacts to whatever
retrieval genuinely returned, so every failure this script reports is a
real consequence of real retrieval/policy behavior, not a manufactured
example (the one deliberate exception, clearly marked, is a single forced
evaluator failure on GC-002 to prove that taxonomy bucket is reachable -
see `_run_evaluator_check`).

No real network call (same discipline as every other Day 7 script). Needs
the Day 1 index built first:
    uv run python -m aico.retrieval.ingest --input data/documents --out data/index --tokens 300 --overlap 50
"""
from __future__ import annotations

import json
import pathlib

from aico.evals.dataset import GoldenCase, load_dataset
from aico.evals.day01 import normalise
from aico.evals.failure_classifier import classify_failure, render_failure_classification_report
from aico.evals.groundedness import evaluate_groundedness
from aico.evals.metrics import score_attack_outcome, score_citations, score_refusal, score_retrieval
from aico.platform.model_gateway import CallMetadata, ChatRequest, ChatResult
from aico.rag.answer_service import BM25Retriever, GroundedAnswer, GroundedAnswerService
from aico.rag.citation_validator import EvidenceChunk
from aico.retrieval.search import load_chunks

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATASET_PATH = REPO_ROOT / "evals" / "golden_v1.json"
INDEX_DIR = REPO_ROOT / "data" / "index"
OUT_PATH = REPO_ROOT / "artifacts" / "day07" / "failure_classification.md"
SCRIPT_NAME = "scripts/day07_generate_failure_classification_report.py"

TOP_K = 5
EVALUATOR_DEMONSTRATION_CASE_ID = "GC-002"  # see module docstring


class _ScriptedGateway:
    """Duck-typed ModelGateway stand-in - returns exactly one fixed
    response (same pattern as scripts/day05_generate_answer_artifacts.py's
    _FakeGateway)."""

    def __init__(self, respond: str, model_alias: str = "failure-report-fake-alias"):
        self._respond = respond
        self._model_alias = model_alias
        self.calls: list[ChatRequest] = []

    def chat(self, request: ChatRequest) -> ChatResult:
        self.calls.append(request)
        return ChatResult(
            content=self._respond,
            metadata=CallMetadata(
                operation="chat", model_alias=self._model_alias, latency_ms=1.0, retry_count=0,
                token_usage={"prompt_tokens": 30, "completion_tokens": 15}, budget_status="within_budget",
            ),
        )


def _fixed_retriever(chunks: list[EvidenceChunk]):
    def _r(query: str) -> list[EvidenceChunk]:
        return chunks
    return _r


def _answered_json(answer: str, citation_ids: list[str], confidence: str = "high") -> str:
    return json.dumps({
        "schema_version": "1.0", "status": "answered", "answer": answer,
        "citations": [{"chunk_id": cid, "source_file": "synthetic.md"} for cid in citation_ids],
        "confidence_label": confidence,
    })


def _insufficient_json(explanation: str) -> str:
    return json.dumps({
        "schema_version": "1.0", "status": "insufficient_evidence", "answer": explanation,
        "citations": [], "confidence_label": "low",
    })


def _matched_sources(case: GoldenCase, retrieved: list[EvidenceChunk]) -> list[tuple[str, EvidenceChunk]]:
    """Which of `case.expected_sources`' anchors are actually present
    (substring match, same rule as Task 3) in `retrieved`, paired with the
    first chunk each was found in."""
    matches = []
    for src in case.expected_sources:
        na = normalise(src.anchor)
        for chunk in retrieved:
            if na in normalise(chunk.text):
                matches.append((src.anchor, chunk))
                break
    return matches


def _well_behaved_response(case: GoldenCase, retrieved: list[EvidenceChunk]) -> str:
    """One honest rule, applied uniformly to every case: answer (citing
    every matched chunk) if and only if at least one expected-source
    anchor is actually present in what was retrieved; otherwise decline.
    See module docstring for why this is not per-case scripted."""
    matches = _matched_sources(case, retrieved)
    if not matches:
        return _insufficient_json(
            "The retrieved evidence does not contain a fact that answers this question."
        )
    # Two different expected-source anchors can land in the same chunk
    # (e.g. a multi_chunk case whose two facts happen to sit in one
    # passage) - a real well-behaved model cites that chunk once, not
    # twice, so dedupe while preserving first-seen order (dict.fromkeys,
    # not a set, so citation order stays deterministic across runs).
    citation_ids = list(dict.fromkeys(chunk.chunk_id for _anchor, chunk in matches))
    if case.critical_facts:
        answer = " ".join(case.critical_facts)
    else:
        # ambiguous cases have no critical_facts by design (evals/README.md)
        # - fall back to the matched anchor text itself as a plausible
        # single-interpretation answer, which is exactly the real system
        # behavior this script is honestly reproducing (see Task 5).
        answer = ". ".join(a.capitalize() for a, _c in matches) + "."
    return _answered_json(answer=answer, citation_ids=citation_ids)


def _run_case(case: GoldenCase, retriever: BM25Retriever):
    retrieved = retriever(case.question)
    gateway = _ScriptedGateway(_well_behaved_response(case, retrieved))
    service = GroundedAnswerService(gateway=gateway, retriever=_fixed_retriever(retrieved))
    result = service.answer(case.question)
    return result, retrieved


def _run_evaluator_demonstration(case: GoldenCase, result, retrieved: list[EvidenceChunk]):
    """Deliberately forces one groundedness-evaluator failure so the
    `evaluator` taxonomy bucket has a real, reachable example in the
    generated report - see module docstring. Only applies to
    EVALUATOR_DEMONSTRATION_CASE_ID, and only meaningful when that case's
    own answer came back as a GroundedAnswer (something to grade)."""
    if case.case_id != EVALUATOR_DEMONSTRATION_CASE_ID or not isinstance(result, GroundedAnswer):
        return None
    broken_grader = _ScriptedGateway("this is not valid json at all")
    return evaluate_groundedness(broken_grader, case, result.answer, retrieved)


def main() -> None:
    dataset = load_dataset(DATASET_PATH)
    all_chunks_raw = load_chunks(INDEX_DIR)
    all_chunks = [EvidenceChunk(chunk_id=c["chunk_id"], source_file=c["source_file"], text=c["text"]) for c in all_chunks_raw]
    retriever = BM25Retriever(index_dir=INDEX_DIR, top_k=TOP_K)

    classifications = []
    for case in dataset.cases:
        result, retrieved = _run_case(case, retriever)

        retrieval_topk = score_retrieval(case, retrieved, k=TOP_K)
        retrieval_full_index = None
        if retrieval_topk.applicable and retrieval_topk.hit_at_k is False:
            retrieval_full_index = score_retrieval(case, all_chunks, k=len(all_chunks))

        citations = None
        if isinstance(result, GroundedAnswer):
            citations = score_citations(case, result.citation_ids, retrieved)

        refusal_check = None
        attack_check = None
        if case.category == "adversarial":
            attack_check = score_attack_outcome(case, result)
        else:
            refusal_check = score_refusal(case, result)

        groundedness = _run_evaluator_demonstration(case, result, retrieved)

        classification = classify_failure(
            case,
            result=result,
            retrieval_topk=retrieval_topk,
            retrieval_full_index=retrieval_full_index,
            citations=citations,
            refusal=refusal_check,
            attack=attack_check,
            groundedness=groundedness,
        )
        if classification is not None:
            classifications.append(classification)

    report = render_failure_classification_report(classifications, total_cases=len(dataset), generated_by=SCRIPT_NAME)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(report, encoding="utf-8")
    print(f"wrote {OUT_PATH.relative_to(REPO_ROOT)} ({len(classifications)} failure(s) of {len(dataset)} case(s))")


if __name__ == "__main__":
    main()
