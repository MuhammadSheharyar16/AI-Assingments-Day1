"""
Day 8 Task 7 — memory is not evidence.

Validates every item in the assignment's own checklist:
    - memory is separately labelled
    - memory cannot replace retrieval
    - memory chunk/turn IDs are not accepted by citation validator as
      evidence citations
    - a false fact remembered from an earlier user message does not
      become a supported supplier fact
    - current answer remains governed by Day 5 citation validation

Two levels: `prompt_builder.build_prompt` directly (structural boundary
proofs - no gateway involved), and the full `GroundedAnswerService.answer()`
pipeline with a `FakeGateway` (same duck-typed pattern as
`test_day05_grounding.py`), so the guarantees are proven both for the
prompt shape itself and for what a model response is allowed to get away
with when a session has memory attached.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from aico.memory.context_builder import MemoryBudget, build_memory_context, estimate_tokens
from aico.memory.models import SESSION_STATE_SCHEMA_VERSION, MemorySummary, SessionState, SessionTurn, TurnRole
from aico.platform.model_gateway import CallMetadata, ChatRequest, ChatResult
from aico.rag.answer_service import GroundedAnswer, GroundedAnswerService, TypedFailure
from aico.rag.citation_validator import EvidenceChunk, validate_citations
from aico.rag.prompt_builder import build_prompt

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)


def _turn(turn_id: str, *, role: TurnRole = TurnRole.USER, content: str, minutes_ago: int = 0) -> SessionTurn:
    return SessionTurn(turn_id=turn_id, role=role, timestamp=NOW - timedelta(minutes=minutes_ago), content=content)


def _session(turns: list[SessionTurn], summary: MemorySummary | None = None) -> SessionState:
    return SessionState(
        schema_version=SESSION_STATE_SCHEMA_VERSION,
        session_id="SESSION-MEM",
        tenant_id="TENANT-A",
        user_id="USER-1",
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        version=1,
        recent_turns=turns,
        summary=summary,
    )


class FakeGateway:
    """Duck-typed `ModelGateway` stand-in, same pattern as
    `test_day05_grounding.py`'s - captures every request it received so a
    test can inspect exactly what was sent."""

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
                token_usage={"prompt_tokens": 10, "completion_tokens": 5},
                budget_status="within_budget",
            ),
        )


def _fixed_retriever(chunks: list[EvidenceChunk]):
    def _retrieve(query: str) -> list[EvidenceChunk]:
        return chunks

    return _retrieve


def _cited_answer_json(**overrides: object) -> str:
    payload = {
        "schema_version": "1.0",
        "status": "answered",
        "answer": "Supplier Alpha's payment terms are net 30 days.",
        "citations": [{"chunk_id": "CHUNK-ALPHA-001", "source_file": "DOC-alpha.md"}],
        "confidence_label": "high",
    }
    payload.update(overrides)
    return json.dumps(payload)


_TRUE_EVIDENCE = EvidenceChunk(chunk_id="CHUNK-ALPHA-001", source_file="DOC-alpha.md", text="Supplier Alpha's payment terms are net 30 days from invoice date.")


# ── Memory is separately labelled ─────────────────────────────────────────


class TestMemoryIsSeparatelyLabelled:
    def test_memory_message_is_its_own_message_with_its_own_label(self) -> None:
        session = _session([_turn("T0", content="What are Supplier Alpha's payment terms?")])
        context = build_memory_context(session)

        prompt = build_prompt("What about its invoice window?", [_TRUE_EVIDENCE], context)

        assert prompt.memory_message is not None
        assert prompt.memory_message.role == "user"
        assert "SESSION MEMORY" in prompt.memory_message.content
        assert "NOT evidence" in prompt.memory_message.content

    def test_memory_content_never_leaks_into_system_user_or_evidence_sections(self) -> None:
        session = _session([_turn("T0", content="MARKER-MEMORY-CONTENT")])
        context = build_memory_context(session)

        prompt = build_prompt("current question", [_TRUE_EVIDENCE], context)

        assert "MARKER-MEMORY-CONTENT" in prompt.memory_message.content
        assert "MARKER-MEMORY-CONTENT" not in prompt.system_message.content
        assert "MARKER-MEMORY-CONTENT" not in prompt.user_message.content
        assert "MARKER-MEMORY-CONTENT" not in prompt.evidence_message.content

    def test_evidence_and_question_never_leak_into_memory_section(self) -> None:
        session = _session([_turn("T0", content="prior turn")])
        context = build_memory_context(session)
        evidence = EvidenceChunk(chunk_id="CHUNK-X", source_file="doc.md", text="MARKER-EVIDENCE-TEXT")

        prompt = build_prompt("MARKER-CURRENT-QUESTION", [evidence], context)

        assert "MARKER-EVIDENCE-TEXT" not in prompt.memory_message.content
        assert "MARKER-CURRENT-QUESTION" not in prompt.memory_message.content

    def test_message_order_is_system_memory_input_evidence(self) -> None:
        session = _session([_turn("T0", content="prior turn")])
        context = build_memory_context(session)

        prompt = build_prompt("question", [_TRUE_EVIDENCE], context)
        request = prompt.to_chat_request()

        assert [m.content[:14] for m in request.messages][:2] == ["SYSTEM INSTRUC", "SESSION MEMORY"]
        assert "USER INPUT" in request.messages[2].content
        assert "RETRIEVED EVIDENCE" in request.messages[3].content

    def test_sections_exposes_session_memory_only_when_present(self) -> None:
        with_memory = build_prompt("q", [_TRUE_EVIDENCE], build_memory_context(_session([_turn("T0", content="x")])))
        without_memory = build_prompt("q", [_TRUE_EVIDENCE], None)

        assert "session_memory" in with_memory.sections()
        assert "session_memory" not in without_memory.sections()

    def test_no_session_history_produces_the_unchanged_three_message_prompt(self) -> None:
        # A brand-new/empty session's context must never change Day 5's
        # baseline prompt shape.
        empty_context = build_memory_context(_session([]))
        prompt = build_prompt("question", [_TRUE_EVIDENCE], empty_context)

        assert prompt.memory_message is None
        assert len(prompt.to_chat_request().messages) == 3

    def test_no_turn_id_or_summary_version_is_ever_rendered(self) -> None:
        summary = MemorySummary(summary_version=7, text="earlier discussion", source_turn_ids=["TURN-777"], created_at=NOW)
        session = _session([_turn("TURN-42", content="hello")], summary=summary)
        context = build_memory_context(session)

        prompt = build_prompt("q", [_TRUE_EVIDENCE], context)

        assert "TURN-42" not in prompt.memory_message.content
        assert "TURN-777" not in prompt.memory_message.content
        assert "7" not in prompt.memory_message.content  # no stray summary_version digit rendered

    def test_rendered_memory_section_carries_a_fixed_framing_overhead_beyond_the_token_budget(self) -> None:
        # A prior review round flagged that the fully-rendered SESSION
        # MEMORY section is noticeably larger than `MemoryContext.token_count`
        # (the selected-content figure `max_memory_tokens` actually bounds -
        # `context_builder.py`'s `MemoryBudget` docstring). This documents
        # and pins that gap rather than leaving it as an undocumented
        # surprise: the difference is the section's own fixed labelling/
        # safety framing text, which does not grow with session size and
        # is not the "unbounded raw history" the budget exists to guard
        # against - it must never shrink or disappear to fit a budget,
        # since it is what keeps memory from being treated as a trusted
        # instruction or as evidence at all (Task 7).
        session = _session([_turn("T0", content="word " * 30)])
        budget = MemoryBudget(max_memory_tokens=40, max_recent_turns=4)
        context = build_memory_context(session, budget=budget)

        prompt = build_prompt("current question", [_TRUE_EVIDENCE], context)
        rendered_tokens = estimate_tokens(prompt.memory_message.content)

        assert context.token_count <= budget.max_memory_tokens  # the selector's own promise still holds
        assert rendered_tokens > context.token_count  # the rendered section is larger - the framing overhead
        # The overhead is a small, fixed cost of the two labelling/safety
        # sentences `_memory_block` always prepends - not a per-turn cost
        # that would scale with session size.
        assert rendered_tokens - context.token_count < 100


# ── Memory chunk/turn IDs cannot satisfy citation validation ──────────────


class TestCitationValidatorImmuneToMemoryIds:
    def test_a_turn_id_used_as_a_citation_is_rejected_as_forged(self) -> None:
        result = validate_citations(["TURN-42"], [_TRUE_EVIDENCE])
        assert result.valid is False
        assert "TURN-42" in result.forged_citation_ids

    def test_full_pipeline_rejects_a_model_that_cites_a_memory_turn_id(self) -> None:
        session = _session([_turn("TURN-42", content="What are Supplier Alpha's payment terms?")])
        context = build_memory_context(session)
        gateway = FakeGateway(_cited_answer_json(citations=[{"chunk_id": "TURN-42", "source_file": "DOC-alpha.md"}]))
        service = GroundedAnswerService(gateway=gateway, retriever=_fixed_retriever([_TRUE_EVIDENCE]))

        result = service.answer("What about its invoice window?", memory_context=context)

        assert isinstance(result, TypedFailure)
        assert result.stage == "citation"
        assert result.category == "forged_citation"

    def test_full_pipeline_rejects_a_model_that_cites_a_summary_looking_id(self) -> None:
        summary = MemorySummary(summary_version=3, text="prior discussion of Supplier Alpha", source_turn_ids=["T0"], created_at=NOW)
        session = _session([], summary=summary)
        context = build_memory_context(session)
        gateway = FakeGateway(_cited_answer_json(citations=[{"chunk_id": "SUMMARY-3", "source_file": "memory"}]))
        service = GroundedAnswerService(gateway=gateway, retriever=_fixed_retriever([_TRUE_EVIDENCE]))

        result = service.answer("question", memory_context=context)

        assert isinstance(result, TypedFailure)
        assert result.stage == "citation"


# ── Memory cannot replace retrieval ────────────────────────────────────────


class TestMemoryCannotReplaceRetrieval:
    def test_rich_memory_with_zero_retrieved_evidence_cannot_produce_a_grounded_answer(self) -> None:
        session = _session(
            [
                _turn("T0", role=TurnRole.USER, content="What are Supplier Alpha's payment terms?"),
                _turn("T1", role=TurnRole.ASSISTANT, content="Supplier Alpha's payment terms are net 30 days."),
            ]
        )
        context = build_memory_context(session)
        # A model attempting to answer straight from memory, citing
        # nothing retrieved (there is nothing to cite - retrieval is empty).
        gateway = FakeGateway(_cited_answer_json(citations=[]))
        service = GroundedAnswerService(gateway=gateway, retriever=_fixed_retriever([]))  # nothing retrieved

        result = service.answer("What about its invoice window?", memory_context=context)

        assert isinstance(result, TypedFailure)
        assert result.category == "answered_without_citation"

    def test_a_forged_citation_against_empty_retrieval_is_also_rejected(self) -> None:
        session = _session([_turn("T0", content="What are Supplier Alpha's payment terms?")])
        context = build_memory_context(session)
        gateway = FakeGateway(_cited_answer_json(citations=[{"chunk_id": "CHUNK-ALPHA-001", "source_file": "DOC-alpha.md"}]))
        service = GroundedAnswerService(gateway=gateway, retriever=_fixed_retriever([]))  # nothing retrieved this turn

        result = service.answer("What about its invoice window?", memory_context=context)

        assert isinstance(result, TypedFailure)
        assert result.stage == "citation"
        assert result.category == "forged_citation"

    def test_memory_alone_does_not_change_policy_retrieval_or_validation_stages(self) -> None:
        # answer() with memory_context still calls the retriever with the
        # current question, and validates exactly like it would with no
        # memory at all - memory only ever reaches prompt construction.
        session = _session([_turn("T0", content="unrelated prior turn")])
        context = build_memory_context(session)
        calls: list[str] = []

        def _tracking_retriever(query: str) -> list[EvidenceChunk]:
            calls.append(query)
            return [_TRUE_EVIDENCE]

        gateway = FakeGateway(_cited_answer_json())
        service = GroundedAnswerService(gateway=gateway, retriever=_tracking_retriever)

        result = service.answer("What are Supplier Alpha's payment terms?", memory_context=context)

        assert isinstance(result, GroundedAnswer)
        assert calls == ["What are Supplier Alpha's payment terms?"]  # retrieval still runs on the real current question


# ── A false remembered fact does not become a supported supplier fact ────


_FALSE_REMEMBERED_CLAIM = "Supplier Alpha offers a fifteen percent discount for early payment."


class TestFalseRememberedFactIsNotSupportedEvidence:
    def test_repeating_a_false_remembered_fact_fails_support_validation(self) -> None:
        # An earlier turn (session memory) states a claim that has nothing
        # to do with what the real retrieved evidence actually says. A
        # model that answers with the memory's claim instead of the
        # evidence's - even while citing the real, genuinely-retrieved
        # chunk_id - must not be trusted: citation membership alone does
        # not prove the answer's claim is actually supported by that
        # chunk's content (support_validator's lexical-overlap check).
        false_memory = _session([_turn("T0", role=TurnRole.USER, content=_FALSE_REMEMBERED_CLAIM)])
        context = build_memory_context(false_memory)
        gateway = FakeGateway(_cited_answer_json(answer=_FALSE_REMEMBERED_CLAIM))
        service = GroundedAnswerService(gateway=gateway, retriever=_fixed_retriever([_TRUE_EVIDENCE]))

        result = service.answer("What are Supplier Alpha's payment terms?", memory_context=context)

        assert isinstance(result, TypedFailure)
        assert result.stage == "support"
        assert result.category == "unsupported_claim"

    def test_the_true_evidence_backed_answer_still_succeeds_with_the_same_memory_present(self) -> None:
        # Positive control: the false-memory scenario above isn't rejecting
        # everything indiscriminately - an answer that actually matches
        # retrieved evidence still succeeds with the identical memory context.
        false_memory = _session([_turn("T0", role=TurnRole.USER, content=_FALSE_REMEMBERED_CLAIM)])
        context = build_memory_context(false_memory)
        gateway = FakeGateway(_cited_answer_json())  # states the TRUE, evidence-backed figure
        service = GroundedAnswerService(gateway=gateway, retriever=_fixed_retriever([_TRUE_EVIDENCE]))

        result = service.answer("What are Supplier Alpha's payment terms?", memory_context=context)

        assert isinstance(result, GroundedAnswer)
        assert result.answer == "Supplier Alpha's payment terms are net 30 days."


# ── Current answer remains governed by Day 5 citation validation ─────────


class TestDay5ValidationStillGovernsWithMemoryPresent:
    def test_a_genuinely_grounded_answer_with_memory_present_still_passes_every_stage(self) -> None:
        session = _session([_turn("T0", content="What are Supplier Alpha's payment terms?")])
        context = build_memory_context(session)
        gateway = FakeGateway(_cited_answer_json())
        service = GroundedAnswerService(gateway=gateway, retriever=_fixed_retriever([_TRUE_EVIDENCE]))

        result = service.answer("What about its invoice window?", memory_context=context)

        assert isinstance(result, GroundedAnswer)
        assert result.citation_ids == ("CHUNK-ALPHA-001",)
        assert result.retrieved_ids == ("CHUNK-ALPHA-001",)

    def test_answering_without_memory_context_is_unaffected_default_behavior(self) -> None:
        gateway = FakeGateway(_cited_answer_json())
        service = GroundedAnswerService(gateway=gateway, retriever=_fixed_retriever([_TRUE_EVIDENCE]))

        result = service.answer("What are Supplier Alpha's payment terms?")  # memory_context omitted entirely

        assert isinstance(result, GroundedAnswer)
        # No memory message was added - only the fixed system-rule text
        # mentions "SESSION MEMORY" at all, and the request is still the
        # plain 3-message Day 5 shape.
        assert len(gateway.calls[0].messages) == 3
        assert all(m.role != "user" or "SESSION MEMORY" not in m.content for m in gateway.calls[0].messages)
