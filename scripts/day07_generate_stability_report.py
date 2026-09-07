"""
Day 7 Task 5 — stability report generator.

Run: uv run python scripts/day07_generate_stability_report.py

Runs the real `aico.rag.answer_service.GroundedAnswerService` (Day 5,
unmodified) and the real `aico.evals.groundedness.evaluate_groundedness`
(Task 4, unmodified) `aico.evals.stability.STABILITY_REPEAT_COUNT` times
each, for every case in `aico.evals.stability.STABILITY_SUBSET_CASE_IDS` -
against a fake Model Gateway, never a real network call (same discipline
as `scripts/day05_generate_answer_artifacts.py`). Retrieval is the real
`BM25Retriever` over the real Day 1 index - only the model's own
completion is scripted.

**What "scripted" means here, honestly**: the fake gateway's response for
each run index is a fixed, hand-written plan per case (`_PLANS` below),
not live model sampling and not pseudo-random noise dressed up as if it
were. This script demonstrates the stability *mechanism* (repeated calls,
aggregation, categorical/numeric summaries) deterministically and
reproducibly; it does not claim to characterize a real model's actual
sampling variance. Swapping in a real `ModelGateway.from_config()` (real
endpoint, temperature > 0) instead of `_ScriptedGateway` would make the
same code path measure genuine live variance - the harness doesn't change,
only which gateway it's given.

Needs the Day 1 index built first (same precondition as the two existing
"real index" tests - see README "Run the tests"):
    uv run python -m aico.retrieval.ingest --input data/documents --out data/index --tokens 300 --overlap 50
"""
from __future__ import annotations

import json
import pathlib

from aico.evals.dataset import GoldenDataset, load_dataset
from aico.evals.groundedness import evaluate_groundedness
from aico.evals.stability import (
    STABILITY_REPEAT_COUNT,
    STABILITY_SUBSET_CASE_IDS,
    build_stability_summary,
    observe_groundedness_run,
    observe_refusal_run,
    render_stability_report,
    run_repeated,
)
from aico.platform.model_gateway import CallMetadata, ChatRequest, ChatResult
from aico.rag.answer_service import BM25Retriever, GroundedAnswerService
from aico.rag.citation_validator import EvidenceChunk

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATASET_PATH = REPO_ROOT / "evals" / "golden_v1.json"
OUT_PATH = REPO_ROOT / "artifacts" / "day07" / "stability_report.md"
SUMMARY_OUT_PATH = REPO_ROOT / "artifacts" / "day07" / "stability_summary.json"
SCRIPT_NAME = "scripts/day07_generate_stability_report.py"


class _ScriptedGateway:
    """Duck-typed ModelGateway stand-in (same pattern as
    scripts/day05_generate_answer_artifacts.py's `_FakeGateway`) that
    returns the run-indexed response from a fixed plan - `responses[i]`
    for the i-th `.chat()` call made against this instance."""

    def __init__(self, responses: list[str], model_alias: str = "stability-report-fake-alias"):
        self._responses = responses
        self._model_alias = model_alias
        self._next = 0
        self.calls: list[ChatRequest] = []

    def chat(self, request: ChatRequest) -> ChatResult:
        self.calls.append(request)
        content = self._responses[self._next]
        self._next += 1
        return ChatResult(
            content=content,
            metadata=CallMetadata(
                operation="chat", model_alias=self._model_alias, latency_ms=1.0 + self._next * 0.3,
                retry_count=0, token_usage={"prompt_tokens": 30, "completion_tokens": 15},
                budget_status="within_budget",
            ),
        )


def _cited_answer_json(*, answer: str, citation_ids: list[str], confidence: str = "high") -> str:
    return json.dumps({
        "schema_version": "1.0",
        "status": "answered",
        "answer": answer,
        "citations": [{"chunk_id": cid, "source_file": "synthetic.md"} for cid in citation_ids],
        "confidence_label": confidence,
    })


def _insufficient_json(explanation: str) -> str:
    return json.dumps({
        "schema_version": "1.0",
        "status": "insufficient_evidence",
        "answer": explanation,
        "citations": [],
        "confidence_label": "low",
    })


def _verdict_json(*, grounded: bool, confidence: str, reasoning: str, covered: list[str] | None = None, missing: list[str] | None = None, prohibited: list[str] | None = None) -> str:
    return json.dumps({
        "grounded": grounded,
        "critical_facts_covered": covered or [],
        "critical_facts_missing": missing or [],
        "prohibited_claims_present": prohibited or [],
        "confidence": confidence,
        "reasoning": reasoning,
    })


def _retrieve(question: str) -> list[EvidenceChunk]:
    return BM25Retriever(top_k=5)(question)


def _fixed_retriever(chunks: list[EvidenceChunk]):
    def _r(query: str) -> list[EvidenceChunk]:
        return chunks
    return _r


def _plan_gc002(chunks: list[EvidenceChunk]) -> list[str]:
    answer = ("Either party may terminate for convenience by giving sixty days written notice. "
              "Notice runs from the date of receipt, not the date of issue.")
    confidences = ["high", "high", "medium", "high", "high"]
    return [_cited_answer_json(answer=answer, citation_ids=[chunks[0].chunk_id], confidence=c) for c in confidences]


def _plan_gc009(chunks: list[EvidenceChunk]) -> list[str]:
    # chunks[0]=sixty days (DOC-002), chunks[1]=thirty days remedy (DOC-002), chunks[2]=ninety days (DOC-001)
    variants = [
        ("Either party may terminate for convenience by giving sixty days written notice.", chunks[0].chunk_id),
        ("A supplier removed from the approved list is entitled to ninety days written notice.", chunks[2].chunk_id),
        ("Either party may terminate for convenience by giving sixty days written notice.", chunks[0].chunk_id),
        ("A breach must be remedied within thirty days of a written remedy notice.", chunks[1].chunk_id),
        ("A supplier removed from the approved list is entitled to ninety days written notice.", chunks[2].chunk_id),
    ]
    return [_cited_answer_json(answer=a, citation_ids=[cid], confidence="high") for a, cid in variants]


def _plan_gc017(chunks: list[EvidenceChunk]) -> list[str]:
    # chunks[1]=DOC-002 "supersedes" anchor, chunks[3]=DOC-001 "ninety days" anchor
    both = (
        "The sixty day termination-for-convenience notice in the contract terms supersedes any notice "
        "period stated in the sourcing policy; the sourcing policy separately provides ninety days notice "
        "for removal from the approved supplier list, a different event."
    )
    only_doc002 = "The sixty day termination-for-convenience notice supersedes any notice period stated in the sourcing policy."
    only_doc001 = "The sourcing policy provides ninety days notice for removal from the approved supplier list."
    plan = [
        (both, [chunks[1].chunk_id, chunks[3].chunk_id]),
        (both, [chunks[1].chunk_id, chunks[3].chunk_id]),
        (only_doc002, [chunks[1].chunk_id]),
        (both, [chunks[1].chunk_id, chunks[3].chunk_id]),
        (only_doc001, [chunks[3].chunk_id]),
    ]
    return [_cited_answer_json(answer=a, citation_ids=cids, confidence="high") for a, cids in plan]


def _plan_gc019(chunks: list[EvidenceChunk]) -> list[str]:
    # chunks[1] = DOC-004 "treated as a suspected fraud attempt"
    answered = _cited_answer_json(
        answer="A request to change bank details received by email alone is treated as a suspected fraud attempt and is reported to Finance Operations before any action is taken.",
        citation_ids=[chunks[1].chunk_id],
        confidence="high",
    )
    answered_medium = _cited_answer_json(
        answer="A request to change bank details received by email alone is treated as a suspected fraud attempt and is reported to Finance Operations before any action is taken.",
        citation_ids=[chunks[1].chunk_id],
        confidence="medium",
    )
    insufficient = _insufficient_json("The retrieved evidence does not appear to address a phone number changed via email.")
    return [answered, insufficient, answered, answered_medium, insufficient]


def _plan_gc024() -> list[str]:
    explanation = "The retrieved evidence defines only an early settlement discount; it does not define any bulk-order discount."
    return [_insufficient_json(explanation) for _ in range(STABILITY_REPEAT_COUNT)]


def _run_refusal_stability(dataset: GoldenDataset):
    cases_by_id = {c.case_id: c for c in dataset.cases}
    results = []

    for case_id, plan_fn in [
        ("GC-002", _plan_gc002),
        ("GC-009", _plan_gc009),
        ("GC-017", _plan_gc017),
        ("GC-019", _plan_gc019),
    ]:
        case = cases_by_id[case_id]
        chunks = _retrieve(case.question)
        responses = plan_fn(chunks)
        gateway = _ScriptedGateway(responses)
        service = GroundedAnswerService(gateway=gateway, retriever=_fixed_retriever(chunks))

        def _run_once(i: int, service=service, case=case) -> dict:
            result = service.answer(case.question)
            return observe_refusal_run(case, result)

        results.append(run_repeated(case_id, _run_once, STABILITY_REPEAT_COUNT))

    case = cases_by_id["GC-024"]
    gateway = _ScriptedGateway(_plan_gc024())
    service = GroundedAnswerService(gateway=gateway, retriever=_fixed_retriever([]))

    def _run_once_gc024(i: int) -> dict:
        result = service.answer(case.question)
        return observe_refusal_run(case, result)

    results.append(run_repeated("GC-024", _run_once_gc024, STABILITY_REPEAT_COUNT))
    return results, cases_by_id


def _run_groundedness_stability(dataset: GoldenDataset, cases_by_id: dict):
    results = []

    case = cases_by_id["GC-002"]
    chunks = _retrieve(case.question)
    answer = "Either party may terminate for convenience by giving sixty days written notice."
    verdicts = [
        _verdict_json(grounded=True, confidence=c, reasoning="Matches the sixty day notice period stated in the evidence.", covered=list(case.critical_facts[:1]))
        for c in ["high", "high", "medium", "high", "high"]
    ]
    results.append(_repeat_groundedness("GC-002", case, answer, chunks, verdicts))

    case = cases_by_id["GC-009"]
    chunks = _retrieve(case.question)
    answer = "Either party may terminate for convenience by giving sixty days written notice."
    verdicts = [
        _verdict_json(grounded=True, confidence="high", reasoning="The single fact stated is itself supported by the evidence, even though it silently picks one of several defined notice periods.")
        for _ in range(STABILITY_REPEAT_COUNT)
    ]
    results.append(_repeat_groundedness("GC-009", case, answer, chunks, verdicts))

    case = cases_by_id["GC-017"]
    chunks = _retrieve(case.question)
    answer = (
        "The sixty day termination-for-convenience notice in the contract terms supersedes any notice "
        "period stated in the sourcing policy; the sourcing policy separately provides ninety days notice "
        "for removal from the approved supplier list, a different event."
    )
    verdicts = [
        _verdict_json(grounded=True, confidence=c, reasoning="Both notice periods are stated and correctly attributed to their source document.", covered=list(case.critical_facts))
        for c in ["high", "medium", "high", "high", "high"]
    ]
    results.append(_repeat_groundedness("GC-017", case, answer, chunks, verdicts))

    case = cases_by_id["GC-019"]
    chunks = _retrieve(case.question)
    answer = (
        "A request to change bank details received by email alone is treated as a suspected fraud attempt "
        "and is reported to Finance Operations before any action is taken."
    )
    grounded_flags = [True, False, True, True, False]
    verdicts = [
        _verdict_json(
            grounded=g, confidence="high",
            reasoning="Matches the evidence." if g else "Grader flagged insufficient lexical overlap with the retrieved chunk on this pass.",
            covered=list(case.critical_facts) if g else [],
            missing=[] if g else list(case.critical_facts),
        )
        for g in grounded_flags
    ]
    results.append(_repeat_groundedness("GC-019", case, answer, chunks, verdicts))

    case = cases_by_id["GC-024"]
    answer = "The retrieved evidence defines only an early settlement discount; it does not define any bulk-order discount."
    verdicts = [
        _verdict_json(grounded=True, confidence="high", reasoning="The explanation states only what the evidence contains and invents no discount figure.")
        for _ in range(STABILITY_REPEAT_COUNT)
    ]
    results.append(_repeat_groundedness("GC-024", case, answer, [], verdicts))

    return results


def _repeat_groundedness(case_id, case, answer, chunks, verdict_jsons):
    gateway = _ScriptedGateway(verdict_jsons)

    def _run_once(i: int) -> dict:
        outcome = evaluate_groundedness(gateway, case, answer, chunks)
        return observe_groundedness_run(outcome)

    return run_repeated(case_id, _run_once, STABILITY_REPEAT_COUNT)


def main() -> None:
    dataset = load_dataset(DATASET_PATH)
    missing = set(STABILITY_SUBSET_CASE_IDS) - {c.case_id for c in dataset.cases}
    if missing:
        raise SystemExit(f"stability subset references unknown case id(s): {sorted(missing)}")

    refusal_results, cases_by_id = _run_refusal_stability(dataset)
    groundedness_results = _run_groundedness_stability(dataset, cases_by_id)

    report = render_stability_report(refusal_results, groundedness_results, cases_by_id, generated_by=SCRIPT_NAME)
    summary = build_stability_summary(refusal_results, groundedness_results)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(report, encoding="utf-8")
    SUMMARY_OUT_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT_PATH.relative_to(REPO_ROOT)}")
    print(f"wrote {SUMMARY_OUT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
