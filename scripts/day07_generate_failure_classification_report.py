"""
Day 7 Task 6 — failure classification report generator (demonstration).

Run: uv run python scripts/day07_generate_failure_classification_report.py

**Superseded for normal use by Task 9's `python -m aico.evals.day07`**,
which regenerates this exact file (`artifacts/day07/failure_classification.md`)
as part of the one complete evaluation command, using the same honest,
non-per-case-scripted pipeline this script pioneered for Task 6
(`aico.evals.day07.run_case`/`well_behaved_response`, imported from there
rather than duplicated here). Run `python -m aico.evals.day07` for the
real, canonical, CI-facing artifact.

This script now exists for one narrower purpose Task 9's real gate
deliberately does not do: forcing one demonstration evaluator failure (on
`EVALUATOR_DEMONSTRATION_CASE_ID`) so the `evaluator` taxonomy bucket has
a visibly reachable example even when nothing in a given real run happens
to break grading on its own. Task 9's own gate must never fabricate a
failure like this — a real regression gate can't inject fake failures
into what's meant to be a genuine pass/fail signal — so it doesn't; this
script still does, clearly marked, purely to illustrate the classifier.

No real network call. Needs the Day 1 index built first:
    uv run python -m aico.retrieval.ingest --input data/documents --out data/index --tokens 300 --overlap 50
"""
from __future__ import annotations

import pathlib

from aico.evals.dataset import load_dataset
from aico.evals.day07 import DEFAULT_TOP_K as TOP_K
from aico.evals.day07 import ScriptedGateway as _ScriptedGateway
from aico.evals.day07 import evaluate_all_cases, load_full_index_chunks
from aico.evals.failure_classifier import classify_failure, render_failure_classification_report
from aico.evals.groundedness import evaluate_groundedness
from aico.rag.answer_service import BM25Retriever, GroundedAnswer
from aico.rag.citation_validator import EvidenceChunk

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATASET_PATH = REPO_ROOT / "evals" / "golden_v1.json"
INDEX_DIR = REPO_ROOT / "data" / "index"
OUT_PATH = REPO_ROOT / "artifacts" / "day07" / "failure_classification.md"
SCRIPT_NAME = "scripts/day07_generate_failure_classification_report.py"

EVALUATOR_DEMONSTRATION_CASE_ID = "GC-002"  # see module docstring


def _run_evaluator_demonstration(case, result, retrieved: list[EvidenceChunk]):
    """Deliberately forces one groundedness-evaluator failure so the
    `evaluator` taxonomy bucket has a real, reachable example in *this*
    demonstration script's report - see module docstring. Only applies to
    EVALUATOR_DEMONSTRATION_CASE_ID, and only meaningful when that case's
    own answer came back as a GroundedAnswer (something to grade)."""
    if case.case_id != EVALUATOR_DEMONSTRATION_CASE_ID or not isinstance(result, GroundedAnswer):
        return None
    broken_grader = _ScriptedGateway("this is not valid json at all")
    return evaluate_groundedness(broken_grader, case, result.answer, retrieved)


def main() -> None:
    dataset = load_dataset(DATASET_PATH)
    all_chunks = load_full_index_chunks(INDEX_DIR)
    retriever = BM25Retriever(index_dir=INDEX_DIR, top_k=TOP_K)

    # Reuse Task 9's shared per-case evaluation (retrieval/citation/
    # refusal/attack checks + real groundedness + classification), then
    # overwrite just the one demonstration case's classification with the
    # forced-evaluator-failure version described above.
    evaluations = {e.case.case_id: e for e in evaluate_all_cases(dataset, retriever, all_chunks, TOP_K)}

    demo_eval = evaluations[EVALUATOR_DEMONSTRATION_CASE_ID]
    forced_groundedness = _run_evaluator_demonstration(demo_eval.case, demo_eval.result, list(demo_eval.retrieved))
    if forced_groundedness is not None:
        forced_classification = classify_failure(
            demo_eval.case, result=demo_eval.result, retrieval_topk=demo_eval.retrieval_topk,
            retrieval_full_index=demo_eval.retrieval_full_index, citations=demo_eval.citations,
            refusal=demo_eval.refusal, attack=demo_eval.attack, groundedness=forced_groundedness,
        )
        evaluations[EVALUATOR_DEMONSTRATION_CASE_ID] = demo_eval.__class__(
            **{**demo_eval.__dict__, "groundedness": forced_groundedness, "classification": forced_classification}
        )

    classifications = [e.classification for e in evaluations.values() if e.classification is not None]
    report = render_failure_classification_report(classifications, total_cases=len(dataset), generated_by=SCRIPT_NAME)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(report, encoding="utf-8")
    print(f"wrote {OUT_PATH.relative_to(REPO_ROOT)} ({len(classifications)} failure(s) of {len(dataset)} case(s))")


if __name__ == "__main__":
    main()
