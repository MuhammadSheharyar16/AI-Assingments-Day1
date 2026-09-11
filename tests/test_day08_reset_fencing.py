"""
Day 8 Task 8/9 — reset fencing: an in-flight request's answer cannot
silently repopulate a session the user just cleared.

A prior review round found this gap: `MemorySessionService.update_session`
(Task 9) reloads the session's CURRENT state on every attempt and appends
onto it - correct for making two ordinary concurrent writers additive,
but it has no way, on its own, to distinguish "another request also
appended a turn since I loaded" (fine, still append) from "the user
cleared this session since I loaded" (must NOT append this request's own,
now-stale, pre-clear-computed turn back onto it). Without a fence, a
request that:

    1. loads session state (Task 3, `resolve_session`)
    2. builds its answer from memory context that reflects THAT state
    3. finishes AFTER the user calls `clear()` in the meantime
    4. calls `record_turn` (`api/session_flow.py`) to persist its turn

would have its own new turn (question + answer, computed from pre-clear
context) appended onto the just-cleared session, immediately undoing the
clear from the user's point of view - even though no `SessionConflictError`
ever fires (the reload-then-append is genuinely additive, not a lost
update in Task 9's sense) and no cleared content is literally restored
(the OLD, pre-clear turns stay gone; only this request's NEW turn lands).

The fix: `SessionState.reset_count` (`memory/models.py`) is incremented
only by `SessionStore.clear()`, never by an ordinary turn-append save.
`record_turn` captures the `reset_count` it observed when its `session`
argument was loaded and compares it, on every retry attempt, against the
freshly reloaded session's current value - a mismatch means a reset
happened in between, and the turn is discarded rather than saved.

This file proves both directions: the reset case is fenced (this file's
own point), and two genuinely concurrent writers who never hit a reset
are still additive afterward (Task 9's own guarantee must not regress).
"""
from __future__ import annotations

from datetime import UTC, datetime

from aico.api.identity import TrustedIdentity
from aico.api.session_flow import record_turn
from aico.memory.service import MemorySessionService
from aico.memory.store import InMemorySessionStore, SessionStore
from aico.memory.summarizer import FakeSummarizer

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
IDENTITY = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1")


def _record(service: MemorySessionService, session, *, request_id: str, question: str, answer: str) -> None:
    record_turn(
        IDENTITY,
        session,
        question=question,
        blocked=False,
        answer_text=answer,
        memory_service=service,
        summarizer=FakeSummarizer(),
        request_id=request_id,
        correlation_id=f"corr-{request_id}",
    )


class TestInFlightRequestDiscardedAfterReset:
    def test_a_turn_computed_before_a_clear_is_not_persisted_after_it(self) -> None:
        store: SessionStore = InMemorySessionStore(clock=lambda: NOW)
        service = MemorySessionService(store)
        created = service.create_session(IDENTITY)

        # This request loads the session and (elsewhere, not modeled
        # here) builds an answer from it - the exact `session` snapshot
        # it will later hand to `record_turn`.
        in_flight_snapshot = service.load_session(IDENTITY, created.session_id)

        # The user clears the session while that request is still running.
        service.clear_session(IDENTITY, created.session_id)
        after_clear = service.load_session(IDENTITY, created.session_id)
        assert after_clear.recent_turns == []

        # The in-flight request now finishes and tries to persist its
        # (pre-clear-computed) turn.
        _record(service, in_flight_snapshot, request_id="req-stale", question="Q-STALE", answer="A-STALE")

        final = service.load_session(IDENTITY, created.session_id)
        assert final.recent_turns == []  # discarded, not appended
        assert final.version == after_clear.version  # no write happened at all

    def test_a_turn_loaded_after_the_clear_is_persisted_normally(self) -> None:
        # Positive control - a request that loads its session AFTER the
        # clear (an ordinary fresh conversation) must still work exactly
        # as before; fencing must not turn into "nothing can ever be
        # saved again."
        store: SessionStore = InMemorySessionStore(clock=lambda: NOW)
        service = MemorySessionService(store)
        created = service.create_session(IDENTITY)
        service.clear_session(IDENTITY, created.session_id)

        fresh_snapshot = service.load_session(IDENTITY, created.session_id)
        _record(service, fresh_snapshot, request_id="req-fresh", question="Q-FRESH", answer="A-FRESH")

        final = service.load_session(IDENTITY, created.session_id)
        assert [t.content for t in final.recent_turns] == ["Q-FRESH", "A-FRESH"]


class TestOrdinaryConcurrencyStillAdditive:
    def test_two_writers_with_no_reset_between_them_both_land(self) -> None:
        # Task 9's own guarantee, proven again here so a future change to
        # the fencing check above cannot silently turn every concurrent
        # writer into a "discarded" one - only a genuine reset must do
        # that, never an ordinary second in-flight request.
        store: SessionStore = InMemorySessionStore(clock=lambda: NOW)
        service = MemorySessionService(store)
        created = service.create_session(IDENTITY)

        loaded_a = service.load_session(IDENTITY, created.session_id)
        loaded_b = service.load_session(IDENTITY, created.session_id)

        _record(service, loaded_a, request_id="req-a", question="QA", answer="AA")
        _record(service, loaded_b, request_id="req-b", question="QB", answer="AB")

        final = service.load_session(IDENTITY, created.session_id)
        assert {t.content for t in final.recent_turns} == {"QA", "AA", "QB", "AB"}
