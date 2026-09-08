"""
Day 8 Task 9 — concurrency / lost-update protection
(`MemorySessionService.update_session`, `src/aico/memory/service.py`).

The raw store (Task 2, `test_day08_session_lifecycle.py::TestSave`)
already proves DETECTION: a stale-version `save()` is rejected, never
silently overwritten. This file proves RECOVERY - the thing Task 9
actually asks for: "two writers cannot silently overwrite each other's
accepted turn" means the second writer's change still has to land, not
merely that the conflict was logged and dropped.

`ConflictInjectingStore`/`AlwaysStaleStore` wrap a real
`InMemorySessionStore` and force a GENUINE `SessionConflictError` out of
it - by actually committing a concurrent write between the caller's load
and save, not by mocking the exception - so every test here exercises
the real store's real optimistic-concurrency check, exactly the way two
truly-concurrent callers would trigger it. The final section additionally
proves the same guarantee under real Python threads, not just a
simulated race.
"""
from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from aico.api.identity import TrustedIdentity
from aico.memory.errors import SessionConflictError, SessionNotFoundError
from aico.memory.models import SessionState, SessionTurn, TurnRole
from aico.memory.service import DEFAULT_MAX_SAVE_ATTEMPTS, MemorySessionService
from aico.memory.store import InMemorySessionStore, SessionStore

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
IDENTITY = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1")


def _turn(turn_id: str, content: str = "x") -> SessionTurn:
    return SessionTurn(turn_id=turn_id, role=TurnRole.USER, timestamp=NOW, content=content)


class ConflictInjectingStore(SessionStore):
    """Wraps a real `InMemorySessionStore`. On the Nth call to `save()`
    for any session, it first commits an extra "phantom" concurrent write
    directly against the wrapped store - simulating another writer
    winning the race right between this caller's load and save - so the
    save this class then forwards is genuinely stale, and the wrapped
    store raises its own real `SessionConflictError`."""

    def __init__(self, inner: SessionStore, *, conflict_on_attempt: int, phantom_turn: SessionTurn):
        self._inner = inner
        self._conflict_on_attempt = conflict_on_attempt
        self._phantom_turn = phantom_turn
        self.save_calls = 0

    def create(self, **kwargs):  # type: ignore[no-untyped-def]
        return self._inner.create(**kwargs)

    def get(self, **kwargs):  # type: ignore[no-untyped-def]
        return self._inner.get(**kwargs)

    def clear(self, **kwargs):  # type: ignore[no-untyped-def]
        return self._inner.clear(**kwargs)

    def delete(self, **kwargs):  # type: ignore[no-untyped-def]
        return self._inner.delete(**kwargs)

    def expire(self, **kwargs):  # type: ignore[no-untyped-def]
        return self._inner.expire(**kwargs)

    def save(self, session: SessionState) -> SessionState:
        self.save_calls += 1
        if self.save_calls == self._conflict_on_attempt:
            current = self._inner.get(tenant_id=session.tenant_id, user_id=session.user_id, session_id=session.session_id)
            phantom = current.model_copy(update={"recent_turns": [*current.recent_turns, self._phantom_turn]})
            self._inner.save(phantom)  # a genuine concurrent writer commits first
        return self._inner.save(session)


class AlwaysStaleStore(SessionStore):
    """Every `save()` attempt is made stale by its own phantom concurrent
    write first - simulates sustained contention, to prove the retry loop
    is bounded (Task 9: "no unbounded retry loop")."""

    def __init__(self, inner: SessionStore):
        self._inner = inner
        self.save_calls = 0

    def create(self, **kwargs):  # type: ignore[no-untyped-def]
        return self._inner.create(**kwargs)

    def get(self, **kwargs):  # type: ignore[no-untyped-def]
        return self._inner.get(**kwargs)

    def clear(self, **kwargs):  # type: ignore[no-untyped-def]
        return self._inner.clear(**kwargs)

    def delete(self, **kwargs):  # type: ignore[no-untyped-def]
        return self._inner.delete(**kwargs)

    def expire(self, **kwargs):  # type: ignore[no-untyped-def]
        return self._inner.expire(**kwargs)

    def save(self, session: SessionState) -> SessionState:
        self.save_calls += 1
        current = self._inner.get(tenant_id=session.tenant_id, user_id=session.user_id, session_id=session.session_id)
        phantom = current.model_copy(update={"recent_turns": [*current.recent_turns, _turn(f"PHANTOM-{self.save_calls}")]})
        self._inner.save(phantom)  # always wins the race, every attempt
        return self._inner.save(session)


class TestLostUpdateProtection:
    def test_two_writers_do_not_silently_overwrite_each_others_turn(self) -> None:
        inner = InMemorySessionStore()
        session = inner.create(tenant_id=IDENTITY.tenant_id, user_id=IDENTITY.user_id)
        turn_a, turn_b = _turn("A", "writer A's turn"), _turn("B", "writer B's turn")

        store = ConflictInjectingStore(inner, conflict_on_attempt=1, phantom_turn=turn_a)
        service = MemorySessionService(store)

        result = service.update_session(
            IDENTITY, session.session_id, lambda current: current.model_copy(update={"recent_turns": [*current.recent_turns, turn_b]})
        )

        turn_ids = {t.turn_id for t in result.recent_turns}
        assert turn_ids == {"A", "B"}  # neither writer's turn was silently dropped
        assert store.save_calls == 2  # rejected once, recovered on retry

    def test_mutate_sees_a_freshly_reloaded_state_on_each_retry_not_a_stale_closure(self) -> None:
        inner = InMemorySessionStore()
        session = inner.create(tenant_id=IDENTITY.tenant_id, user_id=IDENTITY.user_id)
        store = ConflictInjectingStore(inner, conflict_on_attempt=1, phantom_turn=_turn("PHANTOM"))
        service = MemorySessionService(store)

        seen_turn_counts: list[int] = []

        def _mutate(current: SessionState) -> SessionState:
            seen_turn_counts.append(len(current.recent_turns))
            return current.model_copy(update={"recent_turns": [*current.recent_turns, _turn("MINE")]})

        service.update_session(IDENTITY, session.session_id, _mutate)

        # First attempt saw 0 turns (before the phantom write); the retry
        # saw 1 (the phantom write is now visible) - proof `current` is
        # reloaded, not reused.
        assert seen_turn_counts == [0, 1]

    def test_successful_save_needs_no_retry_when_there_is_no_conflict(self) -> None:
        inner = InMemorySessionStore()
        session = inner.create(tenant_id=IDENTITY.tenant_id, user_id=IDENTITY.user_id)
        service = MemorySessionService(inner)

        result = service.update_session(
            IDENTITY, session.session_id, lambda current: current.model_copy(update={"recent_turns": [*current.recent_turns, _turn("A")]})
        )

        assert [t.turn_id for t in result.recent_turns] == ["A"]
        assert result.version == 2  # exactly one save


class TestBoundedRetry:
    def test_bounded_retry_gives_up_rather_than_looping_forever(self) -> None:
        inner = InMemorySessionStore()
        session = inner.create(tenant_id=IDENTITY.tenant_id, user_id=IDENTITY.user_id)
        store = AlwaysStaleStore(inner)
        service = MemorySessionService(store)

        with pytest.raises(SessionConflictError):
            service.update_session(
                IDENTITY,
                session.session_id,
                lambda current: current.model_copy(update={"recent_turns": [*current.recent_turns, _turn("MINE")]}),
                max_attempts=3,
            )

        assert store.save_calls == 3  # exactly bounded - not 1, not unbounded

    def test_default_max_attempts_is_small_and_positive(self) -> None:
        assert 1 <= DEFAULT_MAX_SAVE_ATTEMPTS <= 10

    def test_max_attempts_is_configurable(self) -> None:
        inner = InMemorySessionStore()
        session = inner.create(tenant_id=IDENTITY.tenant_id, user_id=IDENTITY.user_id)
        store = AlwaysStaleStore(inner)
        service = MemorySessionService(store)

        with pytest.raises(SessionConflictError):
            service.update_session(
                IDENTITY,
                session.session_id,
                lambda current: current.model_copy(update={"recent_turns": [*current.recent_turns, _turn("MINE")]}),
                max_attempts=7,
            )

        assert store.save_calls == 7

    def test_rejects_a_non_positive_max_attempts(self) -> None:
        inner = InMemorySessionStore()
        session = inner.create(tenant_id=IDENTITY.tenant_id, user_id=IDENTITY.user_id)
        service = MemorySessionService(inner)

        with pytest.raises(ValueError):
            service.update_session(IDENTITY, session.session_id, lambda current: current, max_attempts=0)

    def test_exhausted_retry_raises_the_same_error_type_a_single_stale_save_would(self) -> None:
        # No new, weaker failure mode invented for the bounded-retry path -
        # a caller that already handles SessionConflictError from
        # save_session handles this identically.
        inner = InMemorySessionStore()
        session = inner.create(tenant_id=IDENTITY.tenant_id, user_id=IDENTITY.user_id)
        store = AlwaysStaleStore(inner)
        service = MemorySessionService(store)

        with pytest.raises(SessionConflictError) as excinfo:
            service.update_session(
                IDENTITY, session.session_id, lambda current: current.model_copy(update={"recent_turns": [*current.recent_turns, _turn("MINE")]}), max_attempts=2
            )
        assert excinfo.value.expected_version is not None
        assert excinfo.value.actual_version is not None


class TestUpdateSessionOwnershipAndAvailability:
    def test_does_not_retry_against_a_session_that_does_not_exist(self) -> None:
        inner = InMemorySessionStore()
        service = MemorySessionService(inner)

        with pytest.raises(SessionNotFoundError):
            service.update_session(IDENTITY, "SES-never-created", lambda current: current)

    def test_does_not_retry_for_a_different_owner(self) -> None:
        inner = InMemorySessionStore()
        session = inner.create(tenant_id=IDENTITY.tenant_id, user_id=IDENTITY.user_id)
        stranger = TrustedIdentity(tenant_id=IDENTITY.tenant_id, user_id="USER-2")
        service = MemorySessionService(inner)

        with pytest.raises(SessionNotFoundError):
            service.update_session(stranger, session.session_id, lambda current: current)


class TestRealConcurrentThreads:
    def test_real_concurrent_writers_via_threads_lose_no_turn(self) -> None:
        # No wrapper, no simulation - genuine Python threads racing
        # against the same InMemorySessionStore, still in-process (no
        # distributed infrastructure needed, per the assignment brief).
        store = InMemorySessionStore()
        session = store.create(tenant_id=IDENTITY.tenant_id, user_id=IDENTITY.user_id)
        service = MemorySessionService(store)
        writer_count = 8
        expected_ids = {f"T{i}" for i in range(writer_count)}

        def _write(turn_id: str) -> None:
            service.update_session(
                IDENTITY,
                session.session_id,
                lambda current, tid=turn_id: current.model_copy(update={"recent_turns": [*current.recent_turns, _turn(tid)]}),
                max_attempts=20,  # generous bound for real thread contention - still bounded, not infinite
            )

        threads = [threading.Thread(target=_write, args=(turn_id,)) for turn_id in expected_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = store.get(tenant_id=IDENTITY.tenant_id, user_id=IDENTITY.user_id, session_id=session.session_id)
        assert {t.turn_id for t in final.recent_turns} == expected_ids
        assert len(final.recent_turns) == writer_count  # every writer's turn survived


class TestClockUnused:
    def test_bounded_retry_never_sleeps(self) -> None:
        # Deterministic (Task 9 / repo convention): no time.sleep-based
        # backoff anywhere in the retry loop - proven indirectly by this
        # whole file running near-instantly, not by inspecting timing.
        started = datetime.now(UTC)
        inner = InMemorySessionStore()
        session = inner.create(tenant_id=IDENTITY.tenant_id, user_id=IDENTITY.user_id)
        store = AlwaysStaleStore(inner)
        service = MemorySessionService(store)

        with pytest.raises(SessionConflictError):
            service.update_session(IDENTITY, session.session_id, lambda current: current, max_attempts=5)

        assert datetime.now(UTC) - started < timedelta(seconds=1)
