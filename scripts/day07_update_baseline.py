"""
Day 7 Task 8 — deliberate baseline update workflow.

Run (dry run — prints the computed metrics and a diff against the current
`evals/baseline_v1.json`, writes nothing):
    uv run python scripts/day07_update_baseline.py --reviewer "you@example.com" --notes "why"

Run for real (writes `evals/baseline_v1.json`):
    uv run python scripts/day07_update_baseline.py --reviewer "you@example.com" --notes "why" --confirm

This is the "separate, documented, deliberate, and reviewable" update path
the brief requires, standing in for `uv run python -m aico.evals.day07
--update-baseline` (which Task 9's full harness will wire to the exact
same `aico.evals.regression.write_baseline` call this script makes — the
"or equivalent" the brief allows). It is a distinct command from every
evaluation entry point: nothing in `aico.evals.metrics`,
`aico.evals.groundedness`, or `aico.evals.regression`'s `evaluate_gate`/
`compare_to_baseline` ever calls `write_baseline` — only this script does,
and only when explicitly run by a person, never from CI (Task 13's
workflow must never invoke this script — see evals/README.md).

Deliberate, concretely:
- `--reviewer` and `--notes` are required, not defaulted — an update
  with no stated reviewer or reason is refused before anything is
  computed (see `write_baseline`'s own guard, defense in depth).
- Without `--confirm`, this is a dry run: it prints exactly what would be
  written and exits 0 without touching the file. A baseline update is
  never a side effect of running this script by accident.
- The metrics it locks in come from the same honest, non-per-case-
  scripted pipeline `scripts/day07_generate_failure_classification_report.py`
  uses — no model call, but not scripted per case toward a chosen number
  either (see that script's module docstring).
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from day07_generate_failure_classification_report import TOP_K, _run_case  # noqa: E402

from aico.evals.dataset import load_dataset  # noqa: E402
from aico.evals.groundedness import GROUNDEDNESS_EVALUATOR_PROMPT_VERSION  # noqa: E402
from aico.evals.metrics import (  # noqa: E402
    aggregate_retrieval,
    score_attack_outcome,
    score_citations,
    score_refusal,
    score_retrieval,
)
from aico.evals.regression import (  # noqa: E402
    REQUIRED_METRIC_NAMES,
    EvaluationSummary,
    compare_to_baseline,
    load_baseline,
    render_baseline_comparison,
    write_baseline,
)
from aico.rag.answer_service import BM25Retriever, GroundedAnswer  # noqa: E402

DATASET_PATH = REPO_ROOT / "evals" / "golden_v1.json"
INDEX_DIR = REPO_ROOT / "data" / "index"
BASELINE_PATH = REPO_ROOT / "evals" / "baseline_v1.json"

# Honest about what actually produced these numbers: a scripted fake
# gateway (see module docstring), not a live deployment. Recorded as such
# rather than invented to look like a real Azure alias.
MODEL_ALIASES = {
    "chat": "fake:well-behaved-response-builder (scripts/day07_generate_failure_classification_report.py)",
    "embedding": "n/a - retrieval is BM25 only, no embedding call in this baseline",
}


def _retrieval_config() -> dict:
    manifest = json.loads((INDEX_DIR / "index.json").read_text(encoding="utf-8"))["manifest"]
    return {
        "mode": "bm25",
        "top_k": TOP_K,
        "chunk_tokens": manifest["tokens"],
        "chunk_overlap": manifest["overlap"],
        "chunk_count": manifest["chunk_count"],
        "ingestion_version": "day1-chunker-1.0",
    }


def compute_summary() -> tuple[EvaluationSummary, dict]:
    dataset = load_dataset(DATASET_PATH)
    retriever = BM25Retriever(index_dir=INDEX_DIR, top_k=TOP_K)

    retrieval_results = []
    refusal_pass = refusal_total = 0
    citation_valid = citation_checked = 0
    attack_results = []

    for case in dataset.cases:
        result, retrieved = _run_case(case, retriever)
        retrieval_results.append(score_retrieval(case, retrieved, k=TOP_K))
        if case.category == "adversarial":
            attack_results.append(score_attack_outcome(case, result))
        else:
            refusal_total += 1
            refusal_pass += int(score_refusal(case, result).passed)
        if isinstance(result, GroundedAnswer):
            citation_checked += 1
            citation_valid += int(score_citations(case, result.citation_ids, retrieved).valid)

    agg = aggregate_retrieval(retrieval_results)
    summary = EvaluationSummary(
        hit_at_1=agg["overall"]["hit_at_1"],
        hit_at_k=agg["overall"]["hit_at_k"],
        mrr=agg["overall"]["mrr"],
        citation_validity_rate=(citation_valid / citation_checked if citation_checked else None),
        refusal_accuracy_rate=(refusal_pass / refusal_total if refusal_total else None),
        groundedness_rate=None,  # not measured by this run - see evals/thresholds_v1.json rationale
        attack_results=tuple(attack_results),
    )
    return summary, {"dataset_version": dataset.version, "case_count": len(dataset)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reviewer", required=True, help="who is approving this baseline (e.g. an email)")
    parser.add_argument("--notes", required=True, help="why this baseline is being set/updated now")
    parser.add_argument("--confirm", action="store_true", help="actually write evals/baseline_v1.json (default: dry run)")
    args = parser.parse_args()

    summary, dataset_meta = compute_summary()
    metrics = {name: getattr(summary, name) for name in REQUIRED_METRIC_NAMES}

    print("Computed candidate metrics (same honest pipeline as the failure-classification report):")
    for name in REQUIRED_METRIC_NAMES:
        value = metrics[name]
        print(f"  {name}: {value:.4f}" if value is not None else f"  {name}: not measured")

    if BASELINE_PATH.exists():
        current = load_baseline(BASELINE_PATH)
        print(f"\nDiff against the current reviewed baseline ({BASELINE_PATH.relative_to(REPO_ROOT)}):")
        print(render_baseline_comparison(compare_to_baseline(summary, current)))
    else:
        print(f"\nNo existing {BASELINE_PATH.relative_to(REPO_ROOT)} - this would be the first reviewed baseline.")

    if not args.confirm:
        print("\nDry run only - nothing written. Pass --confirm to write evals/baseline_v1.json.")
        return 0

    write_baseline(
        BASELINE_PATH,
        dataset_version=dataset_meta["dataset_version"],
        evaluator_prompt_version=GROUNDEDNESS_EVALUATOR_PROMPT_VERSION,
        model_aliases=MODEL_ALIASES,
        retrieval_config=_retrieval_config(),
        metrics=metrics,
        reviewer=args.reviewer,
        date=datetime.date.today().isoformat(),
        notes=args.notes,
    )
    print(f"\nWrote {BASELINE_PATH.relative_to(REPO_ROOT)} - reviewed by {args.reviewer}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
