"""
Day 8 Task 5 — the bounded memory context window
(`src/aico/memory/context_builder.py`).

Covers Task 5's required behaviors, matching
`data/day08_pack/fixtures/context_compaction_cases.json` (the pack does
not prescribe numeric budget values - "Exact token budget is
developer-defined. Cases describe required behavior."):
    CTX-001 under_budget      -> no_compaction_required
    CTX-002 over_budget       -> older_turns_compacted_and_recent_turns_retained
    CTX-003 summary_provenance -> summary_contains_source_turn_ids
    CTX-004 no_duplicate_history_after_compaction
    CTX-005 memory_and_evidence_separate

Task 6 (actually compacting a session's stored `recent_turns` into a
`summary`) and Task 7 (rendering `MemoryContext` into a labelled,
non-evidence prompt section) get their own test files; this one only
proves the selection this module performs over whatever `SessionState` it
is handed.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aico.memory.context_builder import (
    DEFAULT_MEMORY_BUDGET,
    MemoryBudget,
    MemoryContext,
    build_memory_context,
    estimate_tokens,
)
from aico.memory.models import SESSION_STATE_SCHEMA_VERSION, MemorySummary, SessionState, SessionTurn, TurnRole
from aico.retrieval.chunker import WORD_RE

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)


def _words(n: int, prefix: str = "word") -> str:
    """N whitespace-separated tokens, so `estimate_tokens` on the result
    is exactly `n` - lets budget-boundary tests use exact numbers instead
    of guessing token counts for real sentences."""

    return " ".join(f"{prefix}{i}" for i in range(n))


def _turn(turn_id: str, *, role: TurnRole = TurnRole.USER, n_words: int = 1, minutes_ago: int = 0) -> SessionTurn:
    return SessionTurn(
        turn_id=turn_id,
        role=role,
        timestamp=NOW - timedelta(minutes=minutes_ago),
        content=_words(n_words, prefix=f"{turn_id}-w"),
    )


def _session(turns: list[SessionTurn], summary: MemorySummary | None = None) -> SessionState:
    return SessionState(
        schema_version=SESSION_STATE_SCHEMA_VERSION,
        session_id="SESSION-CTX",
        tenant_id="TENANT-A",
        user_id="USER-1",
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        version=1,
        recent_turns=turns,
        summary=summary,
    )


class TestEstimateTokens:
    def test_counts_whitespace_delimited_words(self) -> None:
        assert estimate_tokens("one two three") == 3

    def test_empty_string_is_zero_tokens(self) -> None:
        assert estimate_tokens("") == 0

    def test_matches_the_chunker_s_own_word_regex(self) -> None:
        # One documented "token" definition project-wide (module
        # docstring) - not a second, incompatible one for memory.
        text = "Supplier Alpha's payment terms are net-30 days!"
        assert estimate_tokens(text) == len(WORD_RE.findall(text))


class TestMemoryBudget:
    def test_rejects_non_positive_max_memory_tokens(self) -> None:
        with pytest.raises(ValueError):
            MemoryBudget(max_memory_tokens=0)

    def test_rejects_non_positive_max_recent_turns(self) -> None:
        with pytest.raises(ValueError):
            MemoryBudget(max_recent_turns=0)


class TestUnderBudget:
    def test_ctx_001_small_session_is_included_whole_with_nothing_omitted(self) -> None:
        turns = [_turn(f"T{i}", n_words=3, minutes_ago=3 - i) for i in range(3)]
        session = _session(turns)

        context = build_memory_context(session)

        assert [t.turn_id for t in context.included_turns] == ["T0", "T1", "T2"]
        assert context.omitted_turns == ()
        assert context.token_count <= context.budget.max_memory_tokens


class TestOverBudgetByTurnCount:
    def test_ctx_002_older_turns_omitted_recent_turns_retained(self) -> None:
        # 12 turns, capped to the 6 most recent by max_recent_turns - a
        # generous per-turn token budget so the *count* limit is what
        # bites, not the token limit.
        turns = [_turn(f"T{i}", n_words=2, minutes_ago=12 - i) for i in range(12)]
        session = _session(turns)
        budget = MemoryBudget(max_memory_tokens=1000, max_recent_turns=6)

        context = build_memory_context(session, budget=budget)

        assert [t.turn_id for t in context.included_turns] == ["T6", "T7", "T8", "T9", "T10", "T11"]
        assert [t.turn_id for t in context.omitted_turns] == ["T0", "T1", "T2", "T3", "T4", "T5"]

    def test_included_window_is_a_contiguous_recent_suffix_not_a_scattered_subset(self) -> None:
        turns = [_turn(f"T{i}", n_words=1, minutes_ago=10 - i) for i in range(10)]
        session = _session(turns)
        budget = MemoryBudget(max_memory_tokens=1000, max_recent_turns=4)

        context = build_memory_context(session, budget=budget)

        included_ids = [t.turn_id for t in context.included_turns]
        # The last 4 original turns, in original order - not e.g. every
        # other turn or an out-of-order selection.
        assert included_ids == [t.turn_id for t in turns[-4:]]


class TestOverBudgetByTokenCount:
    def test_context_never_exceeds_the_configured_token_budget(self) -> None:
        # Each turn costs 50 tokens; a 120-token budget can fit 2, not 3.
        turns = [_turn(f"T{i}", n_words=50, minutes_ago=3 - i) for i in range(3)]
        session = _session(turns)
        budget = MemoryBudget(max_memory_tokens=120, max_recent_turns=100)

        context = build_memory_context(session, budget=budget)

        assert context.token_count <= budget.max_memory_tokens
        assert [t.turn_id for t in context.included_turns] == ["T1", "T2"]
        assert [t.turn_id for t in context.omitted_turns] == ["T0"]

    def test_a_single_turn_larger_than_the_whole_budget_is_omitted_not_partially_included(self) -> None:
        turns = [_turn("HUGE", n_words=500)]
        session = _session(turns)
        budget = MemoryBudget(max_memory_tokens=50, max_recent_turns=10)

        context = build_memory_context(session, budget=budget)

        assert context.included_turns == ()
        assert [t.turn_id for t in context.omitted_turns] == ["HUGE"]
        assert context.token_count == 0

    @pytest.mark.parametrize("n_turns,words_each,budget_tokens", [(5, 10, 37), (20, 3, 100), (7, 25, 60)])
    def test_never_exceeds_budget_across_shapes(self, n_turns: int, words_each: int, budget_tokens: int) -> None:
        turns = [_turn(f"T{i}", n_words=words_each, minutes_ago=n_turns - i) for i in range(n_turns)]
        session = _session(turns)
        budget = MemoryBudget(max_memory_tokens=budget_tokens, max_recent_turns=n_turns)

        context = build_memory_context(session, budget=budget)

        assert context.token_count <= budget_tokens


class TestSummaryPreference:
    def test_summary_is_included_when_it_fits(self) -> None:
        summary = MemorySummary(summary_version=1, text=_words(10), source_turn_ids=["T0", "T1"], created_at=NOW)
        session = _session([], summary=summary)

        context = build_memory_context(session, budget=MemoryBudget(max_memory_tokens=100, max_recent_turns=10))

        assert context.summary is summary
        assert context.token_count == 10

    def test_ctx_003_summary_provenance_survives_selection_unmodified(self) -> None:
        summary = MemorySummary(summary_version=2, text=_words(5), source_turn_ids=["T0", "T1", "T2"], created_at=NOW)
        session = _session([], summary=summary)

        context = build_memory_context(session)

        assert context.summary is not None
        assert context.summary.source_turn_ids == ["T0", "T1", "T2"]
        assert context.summary.summary_version == 2

    def test_a_too_large_summary_is_omitted_not_truncated(self) -> None:
        # The budget is a hard ceiling (module docstring): rather than
        # cut the summary's own words (which could change its meaning),
        # an oversized summary is dropped entirely.
        summary = MemorySummary(summary_version=1, text=_words(200), source_turn_ids=["T0"], created_at=NOW)
        session = _session([], summary=summary)

        context = build_memory_context(session, budget=MemoryBudget(max_memory_tokens=50, max_recent_turns=10))

        assert context.summary is None
        assert context.token_count == 0

    def test_summary_and_recent_turns_can_both_be_included_when_both_fit(self) -> None:
        summary = MemorySummary(summary_version=1, text=_words(10), source_turn_ids=["OLD-1"], created_at=NOW)
        turns = [_turn("T0", n_words=5)]
        session = _session(turns, summary=summary)

        context = build_memory_context(session, budget=MemoryBudget(max_memory_tokens=100, max_recent_turns=10))

        assert context.summary is summary
        assert [t.turn_id for t in context.included_turns] == ["T0"]
        assert context.token_count == 15

    def test_summary_takes_priority_leaving_less_room_for_recent_turns(self) -> None:
        summary = MemorySummary(summary_version=1, text=_words(18), source_turn_ids=["OLD-1"], created_at=NOW)
        turns = [_turn("T0", n_words=10, minutes_ago=1), _turn("T1", n_words=10, minutes_ago=0)]
        session = _session(turns, summary=summary)

        # 18 (summary) + 10 (T1) = 28 <= 28; T0 would push it to 38 > 28.
        context = build_memory_context(session, budget=MemoryBudget(max_memory_tokens=28, max_recent_turns=10))

        assert context.summary is summary
        assert [t.turn_id for t in context.included_turns] == ["T1"]
        assert [t.turn_id for t in context.omitted_turns] == ["T0"]


class TestNoDuplicationAndPartition:
    def test_ctx_004_every_turn_is_included_or_omitted_never_both_never_neither(self) -> None:
        turns = [_turn(f"T{i}", n_words=7, minutes_ago=15 - i) for i in range(15)]
        session = _session(turns)
        budget = MemoryBudget(max_memory_tokens=40, max_recent_turns=6)

        context = build_memory_context(session, budget=budget)

        included_ids = {t.turn_id for t in context.included_turns}
        omitted_ids = {t.turn_id for t in context.omitted_turns}
        assert included_ids.isdisjoint(omitted_ids)
        assert included_ids | omitted_ids == {t.turn_id for t in turns}
        assert len(context.included_turns) + len(context.omitted_turns) == len(turns)


class TestMemoryIsNotEvidence:
    def test_ctx_005_memory_context_has_no_field_that_could_carry_evidence(self) -> None:
        # Structural, not conventional: it is impossible for this type to
        # smuggle retrieved evidence into what is supposed to be
        # conversational context (Day 8's core rule / Task 7 groundwork).
        field_names = {f.name for f in MemoryContext.__dataclass_fields__.values()}
        assert field_names == {"summary", "included_turns", "omitted_turns", "token_count", "budget"}
        for forbidden in ("evidence", "citation", "chunk", "retrieved"):
            assert not any(forbidden in name for name in field_names)


class TestEmptySession:
    def test_no_turns_no_summary_is_empty(self) -> None:
        context = build_memory_context(_session([]))

        assert context.is_empty is True
        assert context.token_count == 0
        assert context.included_turns == ()
        assert context.omitted_turns == ()

    def test_default_budget_is_used_when_none_supplied(self) -> None:
        context = build_memory_context(_session([_turn("T0", n_words=3)]))
        assert context.budget == DEFAULT_MEMORY_BUDGET
