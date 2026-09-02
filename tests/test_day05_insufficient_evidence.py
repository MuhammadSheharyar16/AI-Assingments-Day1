"""
Day 5 Task 4 — insufficient-evidence behavior.

Proves the case grounding_rules.md #6-7 names directly: retrieval can
return real, on-topic content that still does not support the requested
fact, and the correct outcome is an explicit `InsufficientEvidence`
result - never an invented fact, never an invented citation ("a nearest
neighbor existing is not proof that the answer is supported").

Every gateway call here goes through a small local `FakeGateway` (no
network, ever - same pattern as test_day05_grounding.py/test_day05_citations.py)
so this file is independently runnable and does not depend on the other
Day 5 test modules.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from aico.platform.model_gateway import CallMetadata, ChatRequest, ChatResult
from aico.rag.answer_service import GroundedAnswer, GroundedAnswerService, InsufficientEvidence, TypedFailure
from aico.rag.citation_validator import EvidenceChunk

PACK_DIR = pathlib.Path(__file__).resolve().parent.parent / "day05_pack"
ANSWER_CASES = json.loads((PACK_DIR / "answer_cases.json").read_text(encoding="utf-8"))["cases"]
INSUFFICIENT_CASES = [c for c in ANSWER_CASES if c["expected_result"] == "insufficient_evidence"]


class FakeGateway:
    def __init__(self, respond):
        self._respond = respond
        self.calls: list[ChatRequest] = []

    def chat(self, request: ChatRequest) -> ChatResult:
        self.calls.append(request)
        content = self._respond(request) if callable(self._respond) else self._respond
        return ChatResult(
            content=content,
            metadata=CallMetadata(
                operation="chat",
                model_alias="fake-chat-alias",
                latency_ms=1.0,
                retry_count=0,
                token_usage=None,
                budget_status="within_budget",
            ),
        )


def _fixed_retriever(chunks: list[EvidenceChunk]):
    def _retrieve(query: str) -> list[EvidenceChunk]:
        return chunks

    return _retrieve


def _insufficient_evidence_json(explanation: str) -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "status": "insufficient_evidence",
            "answer": explanation,
            "citations": [],
            "confidence_label": "low",
        }
    )


def _answered_json(*, answer: str, citation_ids: list[str]) -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "status": "answered",
            "answer": answer,
            "citations": [{"chunk_id": cid, "source_file": "synthetic.md"} for cid in citation_ids],
            "confidence_label": "high",
        }
    )


# ── Supplied fixture pack (day05_pack/answer_cases.json) ────────────────

def test_answer_case_pack_has_at_least_one_insufficient_evidence_case():
    # Task 4 requirement: "Implement at least one query where retrieval
    # returns content but the content does not support the requested fact."
    assert len(INSUFFICIENT_CASES) >= 1


@pytest.mark.parametrize("case", INSUFFICIENT_CASES, ids=[c["id"] for c in INSUFFICIENT_CASES])
def test_supplied_insufficient_evidence_cases_are_never_answered_or_cited(case):
    retrieved = [EvidenceChunk(chunk_id=c["chunk_id"], source_file="synthetic.md", text=c["text"]) for c in case["retrieved"]]
    gateway = FakeGateway(_insufficient_evidence_json("The retrieved evidence does not support the requested fact."))
    service = GroundedAnswerService(gateway=gateway, retriever=_fixed_retriever(retrieved))

    result = service.answer(case["question"])

    assert isinstance(result, InsufficientEvidence)
    assert case.get("must_not_invent_fact", True)
    assert case.get("must_not_invent_citation", True)
    # retrieval genuinely ran (the "nearest neighbor" chunk was returned)
    # even though it does not ground an answer.
    assert set(result.retrieved_ids) == {c["chunk_id"] for c in case["retrieved"]}


def test_ans_002_ceo_dob_case_matches_the_pack_exactly():
    case = next(c for c in ANSWER_CASES if c["id"] == "ANS-002")
    retrieved = [EvidenceChunk(chunk_id=c["chunk_id"], source_file="synthetic.md", text=c["text"]) for c in case["retrieved"]]
    gateway = FakeGateway(
        _insufficient_evidence_json("The retrieved evidence states payment terms, not a date of birth.")
    )
    service = GroundedAnswerService(gateway=gateway, retriever=_fixed_retriever(retrieved))

    result = service.answer(case["question"])

    assert isinstance(result, InsufficientEvidence)
    assert result.retrieved_ids == ("CHUNK-102",)
    assert result.explanation  # a non-empty, model-supplied explanation is present


# ── Structural guarantees (not just model good behavior) ────────────────

def test_insufficient_evidence_result_type_has_no_citation_field_at_all():
    # The strongest version of "no invented citation": InsufficientEvidence
    # is not merely *populated* with an empty citation list, its dataclass
    # shape has no citation field to invent one into in the first place.
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(InsufficientEvidence)}
    assert "citation_ids" not in field_names
    assert "citations" not in field_names


def test_nearest_neighbor_retrieval_does_not_force_a_grounded_answer():
    # A topically-related chunk is retrieved (real BM25/vector behavior:
    # the index always returns *something* close), but it does not
    # actually contain the requested fact. The service must not treat
    # "retrieval returned a chunk" as "the question is answered."
    near_miss_chunk = EvidenceChunk(
        chunk_id="CHUNK-201",
        source_file="synthetic.md",
        text="The synthetic supplier's registered office is in the same city as its main warehouse.",
    )
    gateway = FakeGateway(
        _insufficient_evidence_json("The retrieved evidence does not state the supplier's founding year.")
    )
    service = GroundedAnswerService(gateway=gateway, retriever=_fixed_retriever([near_miss_chunk]))

    result = service.answer("What year was the synthetic supplier founded?")

    assert isinstance(result, InsufficientEvidence)
    assert result.retrieved_ids == ("CHUNK-201",)  # retrieval ran and found something


def test_insufficient_evidence_with_a_citation_fails_closed_even_if_the_citation_is_real():
    # Contract abuse: status admits insufficiency but still attaches a
    # citation. Fails closed even when that citation happens to name a
    # chunk that really was retrieved - a status/citation contradiction is
    # never resolved by trusting whichever half looks more plausible.
    chunk = EvidenceChunk(chunk_id="CHUNK-102", source_file="synthetic.md", text="Payment terms are net 30.")
    gateway = FakeGateway(
        json.dumps(
            {
                "schema_version": "1.0",
                "status": "insufficient_evidence",
                "answer": "Not enough evidence.",
                "citations": [{"chunk_id": "CHUNK-102", "source_file": "synthetic.md"}],
                "confidence_label": "low",
            }
        )
    )
    service = GroundedAnswerService(gateway=gateway, retriever=_fixed_retriever([chunk]))

    result = service.answer("What is the CEO's date of birth?")

    assert isinstance(result, TypedFailure)
    assert result.category == "insufficient_evidence_with_citations"


def test_answered_status_with_zero_citations_fails_closed_as_ungrounded():
    # The symmetric case: status claims "answered" but backs it with no
    # citation at all - an unsupported claim, not a grounded answer, even
    # though contract/schema validation alone would let it through.
    chunk = EvidenceChunk(chunk_id="CHUNK-001", source_file="synthetic.md", text="Some real evidence.")
    gateway = FakeGateway(_answered_json(answer="It is 30 days.", citation_ids=[]))
    service = GroundedAnswerService(gateway=gateway, retriever=_fixed_retriever([chunk]))

    result = service.answer("What is the submission window?")

    assert isinstance(result, TypedFailure)
    assert result.stage == "contract"
    assert result.category == "answered_without_citation"


def test_answered_status_with_a_real_citation_still_grounds_normally():
    # Sanity check the new guard doesn't over-fire on the legitimate path.
    chunk = EvidenceChunk(chunk_id="CHUNK-001", source_file="synthetic.md", text="Net 30 days.")
    gateway = FakeGateway(_answered_json(answer="It is 30 days.", citation_ids=["CHUNK-001"]))
    service = GroundedAnswerService(gateway=gateway, retriever=_fixed_retriever([chunk]))

    result = service.answer("What is the submission window?")

    assert isinstance(result, GroundedAnswer)
    assert result.citation_ids == ("CHUNK-001",)


def test_insufficient_evidence_explanation_is_the_models_own_text_not_synthesized():
    # The service passes the model's explanation through as-is (Task 4:
    # "safe explanation that available evidence does not support the
    # requested fact") - it does not synthesize or rewrite one itself.
    chunk = EvidenceChunk(chunk_id="CHUNK-102", source_file="synthetic.md", text="Payment terms are net 30.")
    explanation = "The retrieved evidence covers payment terms only and does not mention a date of birth."
    gateway = FakeGateway(_insufficient_evidence_json(explanation))
    service = GroundedAnswerService(gateway=gateway, retriever=_fixed_retriever([chunk]))

    result = service.answer("What is the CEO's date of birth?")

    assert isinstance(result, InsufficientEvidence)
    assert result.explanation == explanation


def test_insufficient_evidence_decision_is_deterministic_across_repeated_calls():
    chunk = EvidenceChunk(chunk_id="CHUNK-102", source_file="synthetic.md", text="Payment terms are net 30.")
    gateway = FakeGateway(_insufficient_evidence_json("Not supported by the retrieved evidence."))
    service = GroundedAnswerService(gateway=gateway, retriever=_fixed_retriever([chunk]))

    first = service.answer("What is the CEO's date of birth?")
    second = service.answer("What is the CEO's date of birth?")

    assert isinstance(first, InsufficientEvidence) and isinstance(second, InsufficientEvidence)
    assert first == second
