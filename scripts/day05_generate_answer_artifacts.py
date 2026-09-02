"""
Day 5 Task 9 — supported / insufficient-evidence artifact generator.

Run: python scripts/day05_generate_answer_artifacts.py
(needs PYTHONPATH=src - see README Setup, or `uv run python scripts/...`)

Runs the real `aico.rag.answer_service.GroundedAnswerService` (Task 1) -
the exact same class the test suite exercises - against `ANS-001`
(supported) and `ANS-002` (insufficient evidence) from the supplied
`day05_pack/answer_cases.json`, and writes `artifacts/day05/
supported_answer.md` / `artifacts/day05/insufficient_evidence.md` straight
from those typed results. Same discipline as
`scripts/day04_generate_validation_report.py` and
`scripts/day05_generate_attack_report.py`: never a hand-transcribed
example, always the actual pipeline output.

The Model Gateway is a fake in this script - never a real network call
(working rule: "do not create avoidable cloud cost") - scripted to answer
exactly what `day05_pack/answer_cases.json` documents as each case's
expected fact/insufficiency, so a real model happened to behave well is
never required to regenerate these artifacts deterministically.

Both artifacts are synthetic and sanitized by construction: every string
in them comes from `day05_pack/` (synthetic training material per its own
README) or from this codebase's own typed result objects.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from aico.platform.model_gateway import CallMetadata, ChatRequest, ChatResult
from aico.rag.answer_service import GroundedAnswer, GroundedAnswerService, InsufficientEvidence
from aico.rag.citation_validator import EvidenceChunk, validate_citations

REPO_ROOT = Path(__file__).resolve().parent.parent
ANSWER_CASES_PATH = REPO_ROOT / "day05_pack" / "answer_cases.json"
SUPPORTED_PATH = REPO_ROOT / "artifacts" / "day05" / "supported_answer.md"
INSUFFICIENT_PATH = REPO_ROOT / "artifacts" / "day05" / "insufficient_evidence.md"

SUPPORTED_CASE_ID = "ANS-001"
INSUFFICIENT_CASE_ID = "ANS-002"


class _FakeGateway:
    """Duck-typed `ModelGateway` stand-in (same pattern as the test suite)
    that always returns the one scripted response it was built with -
    never a real network call."""

    def __init__(self, respond: str) -> None:
        self._respond = respond
        self.calls: list[ChatRequest] = []

    def chat(self, request: ChatRequest) -> ChatResult:
        self.calls.append(request)
        return ChatResult(
            content=self._respond,
            metadata=CallMetadata(
                operation="chat", model_alias="report-fake-alias", latency_ms=0.1, retry_count=0,
                token_usage={"prompt_tokens": 42, "completion_tokens": 17}, budget_status="within_budget",
            ),
        )


def _load_case(case_id: str) -> dict:
    cases = json.loads(ANSWER_CASES_PATH.read_text(encoding="utf-8"))["cases"]
    return next(c for c in cases if c["id"] == case_id)


def _chunks_for(case: dict) -> list[EvidenceChunk]:
    return [
        EvidenceChunk(chunk_id=c["chunk_id"], source_file="synthetic.md", text=c["text"])
        for c in case["retrieved"]
    ]


def _fixed_retriever(chunks: list[EvidenceChunk]):
    def _retrieve(query: str) -> list[EvidenceChunk]:
        return chunks

    return _retrieve


# ── run the real pipeline for each case ──────────────────────────────────

def run_supported_case() -> tuple[dict, list[EvidenceChunk], GroundedAnswer]:
    case = _load_case(SUPPORTED_CASE_ID)
    chunks = _chunks_for(case)
    response = json.dumps(
        {
            "schema_version": "1.0",
            "status": "answered",
            "answer": case["retrieved"][0]["text"],
            "citations": [{"chunk_id": cid, "source_file": "synthetic.md"} for cid in case["expected_citation_ids"]],
            "confidence_label": "high",
        }
    )
    gateway = _FakeGateway(response)
    service = GroundedAnswerService(gateway=gateway, retriever=_fixed_retriever(chunks))
    result = service.answer(case["question"])
    assert isinstance(result, GroundedAnswer), f"expected GroundedAnswer, got {type(result).__name__}: {result}"
    return case, chunks, result


def run_insufficient_case() -> tuple[dict, list[EvidenceChunk], InsufficientEvidence]:
    case = _load_case(INSUFFICIENT_CASE_ID)
    chunks = _chunks_for(case)
    explanation = (
        "The retrieved evidence states the synthetic supplier's payment terms only. It does not contain, "
        "and this system will not infer, a date of birth for any individual."
    )
    response = json.dumps(
        {
            "schema_version": "1.0",
            "status": "insufficient_evidence",
            "answer": explanation,
            "citations": [],
            "confidence_label": "low",
        }
    )
    gateway = _FakeGateway(response)
    service = GroundedAnswerService(gateway=gateway, retriever=_fixed_retriever(chunks))
    result = service.answer(case["question"])
    assert isinstance(result, InsufficientEvidence), f"expected InsufficientEvidence, got {type(result).__name__}: {result}"
    return case, chunks, result


# ── render markdown ──────────────────────────────────────────────────────

def render_supported(case: dict, chunks: list[EvidenceChunk], result: GroundedAnswer) -> str:
    citation_result = validate_citations(list(result.citation_ids), chunks)

    lines: list[str] = []
    lines.append("# Day 5 — Supported Answer Example")
    lines.append("")
    lines.append(
        f"Generated by `scripts/day05_generate_answer_artifacts.py` from fixture `{case['id']}` "
        f"(`{case['name']}`) in `day05_pack/answer_cases.json`, run through the real "
        f"`GroundedAnswerService` against a fake Model Gateway (no real network call). Synthetic data only."
    )
    lines.append("")

    lines.append("## User question")
    lines.append("")
    lines.append(f"> {case['question']}")
    lines.append("")

    lines.append("## Retrieved chunk IDs")
    lines.append("")
    for chunk in chunks:
        lines.append(f"- `{chunk.chunk_id}` ({chunk.source_file}): \"{chunk.text}\"")
    lines.append("")

    lines.append("## Final typed answer")
    lines.append("")
    lines.append(f"- **Result type:** `GroundedAnswer`")
    lines.append(f"- **Answer:** {result.answer}")
    lines.append(f"- **Confidence label:** `{result.confidence_label}`")
    lines.append("")

    lines.append("## Citations")
    lines.append("")
    for cid in result.citation_ids:
        lines.append(f"- `{cid}`")
    lines.append("")

    lines.append("## Citation-validation result")
    lines.append("")
    lines.append(f"- `valid`: `{citation_result.valid}`")
    lines.append(f"- `cited_ids`: `{list(citation_result.cited_ids)}`")
    lines.append(f"- `retrieved_ids`: `{list(citation_result.retrieved_ids)}`")
    lines.append(f"- `forged_citation_ids`: `{list(citation_result.forged_citation_ids)}`")
    lines.append("")
    lines.append(
        "Every cited chunk ID is a member of the chunk IDs actually retrieved and supplied to the model "
        "this turn (`cited_ids ⊆ retrieved_context_ids`) - see `aico.rag.citation_validator`."
    )
    lines.append("")

    return "\n".join(lines) + "\n"


def render_insufficient(case: dict, chunks: list[EvidenceChunk], result: InsufficientEvidence) -> str:
    lines: list[str] = []
    lines.append("# Day 5 — Insufficient-Evidence Example")
    lines.append("")
    lines.append(
        f"Generated by `scripts/day05_generate_answer_artifacts.py` from fixture `{case['id']}` "
        f"(`{case['name']}`) in `day05_pack/answer_cases.json`, run through the real "
        f"`GroundedAnswerService` against a fake Model Gateway (no real network call). Synthetic data only."
    )
    lines.append("")

    lines.append("## User question")
    lines.append("")
    lines.append(f"> {case['question']}")
    lines.append("")

    lines.append("## Retrieved chunk IDs")
    lines.append("")
    lines.append(
        "Retrieval ran and returned real, on-topic content - it is simply content that does not support "
        "the requested fact (\"a nearest neighbor existing is not proof that the answer is supported\", "
        "`grounding_rules.md`):"
    )
    lines.append("")
    for chunk in chunks:
        lines.append(f"- `{chunk.chunk_id}` ({chunk.source_file}): \"{chunk.text}\"")
    lines.append("")

    lines.append("## Insufficient-evidence result")
    lines.append("")
    lines.append(f"- **Result type:** `InsufficientEvidence`")
    lines.append(f"- **Explanation:** {result.explanation}")
    lines.append(f"- **Retrieved IDs carried on the result:** `{list(result.retrieved_ids)}`")
    lines.append("")

    lines.append("## Confirmation: no unsupported fact or citation was produced")
    lines.append("")
    citation_field_names = {f.name for f in dataclasses.fields(InsufficientEvidence)}
    lines.append(
        f"- The `InsufficientEvidence` dataclass has no citation field at all "
        f"(fields: `{sorted(citation_field_names)}`) - there is no field to invent a citation into, by "
        f"construction, not by convention."
    )
    lines.append(
        f"- `must_not_invent_fact`: the explanation above states only what the retrieved evidence *does* "
        f"contain (payment terms) - it never asserts an answer to the actual question asked "
        f"(`{case['question']}`); the requested fact was never generated, invented or guessed at."
    )
    lines.append(
        "- `must_not_invent_citation`: confirmed above - no citation field exists on this result type, so "
        "none was produced, forged or otherwise."
    )
    lines.append("")

    return "\n".join(lines) + "\n"


def main() -> None:
    supported_case, supported_chunks, supported_result = run_supported_case()
    insufficient_case, insufficient_chunks, insufficient_result = run_insufficient_case()

    SUPPORTED_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUPPORTED_PATH.write_text(render_supported(supported_case, supported_chunks, supported_result), encoding="utf-8")
    INSUFFICIENT_PATH.write_text(
        render_insufficient(insufficient_case, insufficient_chunks, insufficient_result), encoding="utf-8"
    )
    print(f"wrote {SUPPORTED_PATH.relative_to(REPO_ROOT)}")
    print(f"wrote {INSUFFICIENT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
