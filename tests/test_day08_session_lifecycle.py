"""
Day 8 Task 2 — the session-store abstraction (`src/aico/memory/store.py`).

Every test in this file runs against BOTH `InMemorySessionStore` and
`SqliteSessionStore` via the `store` fixture below - the direct proof of
`structure_rules`' "test fake follows the same contract as the real
store." A behavior asserted here is therefore never true of only one
backend.

Covers Task 2's required behaviors: session ids are stable/non-guessable,
lookup is scoped by trusted tenant/user ownership, an expired session
returns the documented not-found result, clearing a session removes its
conversational state, and (since `SessionState.version` already exists
from Task 1) `save()`'s optimistic-concurrency contract that Task 9 will
test more deeply. Cross-tenant/cross-user/cross-session isolation gets
its own dedicated matrix in Task 3's `test_day08_isolation.py`; this file
only proves the store scopes lookups by ownership at all, using the
lifecycle scenarios in `data/day08_pack/fixtures/session_lifecycle_cases.json`
(SES-001 create/reload, SES-003 expiry, SES-004 clear, SES-005 stale
version) as its cases, plus this store's own `delete`/administrative
`expire`, which the fixture pack does not separately name.

A `FakeClock` (never `time.sleep`) makes expiry deterministic (Task 8:
"Use an injectable clock/time source for deterministic tests where
practical").

Task 8 ("Expiry and reset") required most of this file's own behavior
already - `TestExpire`/`TestClear` above were written ahead of time
because `SessionState.expires_at`/`SessionStore.expire`/`.clear` (Task 2)
already had to exist for Task 1's contract to be honest. What Task 8 adds
here, in `TestExpiryAndClearIntegrateWithMemory` below: proof that
expiry/clear are honored by the *rest* of the memory stack too, not just
the store's own return value - a cleared session's memory context is
genuinely empty (Task 5), compacting a cleared session is a safe no-op
rather than resurrecting old content (Task 6), and TTL is independently
configurable per session, not one hardcoded global cutoff.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aico.memory.context_builder import build_memory_context
from aico.memory.errors import SessionConflictError, SessionNotFoundError
from aico.memory.models import MemorySummary, SessionTurn, TurnRole
from aico.memory.store import (
    DEFAULT_SESSION_TTL_SECONDS,
    Clock,
    InMemorySessionStore,
    SessionStore,
    SqliteSessionStore,
    new_session_id,
)
from aico.memory.summarizer import FakeSummarizer, compact_session

TENANT_A = "TENANT-A"
USER_1 = "USER-1"
USER_2 = "USER-2"
TENANT_B = "TENANT-B"


class FakeClock:
    """Deterministic, explicitly-advanced clock - the `Clock` both store
    implementations accept in place of `datetime.now`."""

    def __init__(self, start: datetime):
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


@pytest.fixture(params=["memory", "sqlite"])
def store_and_clock(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[tuple[SessionStore, FakeClock]]:
    clock = FakeClock(datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC))
    store: SessionStore
    if request.param == "memory":
        store = InMemorySessionStore(clock=clock)
    else:
        store = SqliteSessionStore(db_path=tmp_path / "sessions.db", clock=clock)
    yield store, clock
    close = getattr(store, "close", None)
    if close is not None:
        close()


@pytest.fixture
def store(store_and_clock: tuple[SessionStore, FakeClock]) -> SessionStore:
    return store_and_clock[0]


@pytest.fixture
def clock(store_and_clock: tuple[SessionStore, FakeClock]) -> FakeClock:
    return store_and_clock[1]


class TestCreate:
    def test_create_returns_expected_defaults(self, store: SessionStore, clock: FakeClock) -> None:
        session = store.create(tenant_id=TENANT_A, user_id=USER_1)

        assert session.tenant_id == TENANT_A
        assert session.user_id == USER_1
        assert session.version == 1
        assert session.recent_turns == []
        assert session.summary is None
        assert session.created_at == clock()
        assert session.updated_at == clock()
        assert session.expires_at == clock() + timedelta(seconds=DEFAULT_SESSION_TTL_SECONDS)

    def test_create_generates_stable_non_guessable_ids(self, store: SessionStore) -> None:
        first = store.create(tenant_id=TENANT_A, user_id=USER_1)
        second = store.create(tenant_id=TENANT_A, user_id=USER_1)

        assert first.session_id != second.session_id
        # "non-guess-based enough for the lab": not a short/sequential id
        # an attacker could enumerate - `new_session_id()` uses
        # `secrets.token_urlsafe`, not an incrementing counter.
        assert len(first.session_id) >= 20
        assert len(second.session_id) >= 20

    def test_new_session_id_helper_is_used_by_default(self, store: SessionStore) -> None:
        session = store.create(tenant_id=TENANT_A, user_id=USER_1)
        assert session.session_id.startswith("SES-")

    def test_create_honors_explicit_ttl(self, store: SessionStore, clock: FakeClock) -> None:
        session = store.create(tenant_id=TENANT_A, user_id=USER_1, ttl_seconds=60.0)
        assert session.expires_at == clock() + timedelta(seconds=60.0)

    def test_create_rejects_non_positive_ttl(self, store: SessionStore) -> None:
        with pytest.raises(ValueError):
            store.create(tenant_id=TENANT_A, user_id=USER_1, ttl_seconds=0)

    def test_create_rejects_duplicate_explicit_session_id(self, store: SessionStore) -> None:
        explicit_id = new_session_id()
        store.create(tenant_id=TENANT_A, user_id=USER_1, session_id=explicit_id)

        with pytest.raises(ValueError):
            # Even a different owner must not silently take over an
            # existing session id.
            store.create(tenant_id=TENANT_B, user_id=USER_2, session_id=explicit_id)


class TestGet:
    def test_create_then_reload_returns_same_session_state(self, store: SessionStore) -> None:
        # SES-001 create_and_reload -> same_session_state
        created = store.create(tenant_id=TENANT_A, user_id=USER_1)

        reloaded = store.get(tenant_id=TENANT_A, user_id=USER_1, session_id=created.session_id)

        assert reloaded == created

    def test_get_scoped_by_owner_allows_the_true_owner(self, store: SessionStore) -> None:
        session = store.create(tenant_id=TENANT_A, user_id=USER_1)
        assert store.get(tenant_id=TENANT_A, user_id=USER_1, session_id=session.session_id).session_id == session.session_id

    def test_get_denies_a_different_user_same_tenant(self, store: SessionStore) -> None:
        session = store.create(tenant_id=TENANT_A, user_id=USER_1)
        with pytest.raises(SessionNotFoundError) as excinfo:
            store.get(tenant_id=TENANT_A, user_id=USER_2, session_id=session.session_id)
        assert excinfo.value.reason == "not_found"

    def test_get_denies_a_different_tenant_same_looking_user(self, store: SessionStore) -> None:
        session = store.create(tenant_id=TENANT_A, user_id=USER_1)
        with pytest.raises(SessionNotFoundError):
            store.get(tenant_id=TENANT_B, user_id=USER_1, session_id=session.session_id)

    def test_get_nonexistent_session_raises_not_found(self, store: SessionStore) -> None:
        with pytest.raises(SessionNotFoundError) as excinfo:
            store.get(tenant_id=TENANT_A, user_id=USER_1, session_id="SES-guessed-does-not-exist")
        assert excinfo.value.reason == "not_found"

    def test_wrong_owner_and_nonexistent_are_indistinguishable(self, store: SessionStore) -> None:
        # Fail closed without revealing whether another tenant's session
        # exists (Day 8 working rules / Task 3): same exception type, same
        # outward message, same reason category either way.
        session = store.create(tenant_id=TENANT_A, user_id=USER_1)

        with pytest.raises(SessionNotFoundError) as wrong_owner:
            store.get(tenant_id=TENANT_B, user_id=USER_2, session_id=session.session_id)
        with pytest.raises(SessionNotFoundError) as nonexistent:
            store.get(tenant_id=TENANT_B, user_id=USER_2, session_id="SES-never-created")

        assert str(wrong_owner.value) == str(nonexistent.value)
        assert wrong_owner.value.reason == nonexistent.value.reason == "not_found"


class TestSave:
    def test_save_persists_updated_content_and_increments_version(self, store: SessionStore, clock: FakeClock) -> None:
        session = store.create(tenant_id=TENANT_A, user_id=USER_1)

        clock.advance(5)
        turn = SessionTurn(turn_id="TURN-001", role=TurnRole.USER, timestamp=clock(), content="What are Supplier Alpha's payment terms?")
        updated_input = session.model_copy(update={"recent_turns": [turn]})  # caller's in-hand copy, still version 1
        saved = store.save(updated_input)

        assert saved.version == 2
        assert saved.updated_at == clock()
        assert [t.turn_id for t in saved.recent_turns] == ["TURN-001"]

        reloaded = store.get(tenant_id=TENANT_A, user_id=USER_1, session_id=session.session_id)
        assert reloaded.version == 2
        assert [t.turn_id for t in reloaded.recent_turns] == ["TURN-001"]

    def test_stale_version_write_is_rejected_not_silently_overwritten(self, store: SessionStore) -> None:
        # SES-005 stale_version -> conflict_not_silent_overwrite
        session = store.create(tenant_id=TENANT_A, user_id=USER_1)

        first_writer_copy = session.model_copy()
        second_writer_copy = session.model_copy()

        store.save(first_writer_copy)  # accepted: bumps store to version 2

        with pytest.raises(SessionConflictError) as excinfo:
            store.save(second_writer_copy)  # still claims version 1: stale

        assert excinfo.value.expected_version == 1
        assert excinfo.value.actual_version == 2
        # The first writer's accepted turn must still be there - not
        # overwritten by the rejected stale write.
        assert store.get(tenant_id=TENANT_A, user_id=USER_1, session_id=session.session_id).version == 2

    def test_save_for_a_deleted_session_raises_not_found(self, store: SessionStore) -> None:
        session = store.create(tenant_id=TENANT_A, user_id=USER_1)
        store.delete(tenant_id=TENANT_A, user_id=USER_1, session_id=session.session_id)

        with pytest.raises(SessionNotFoundError):
            store.save(session)


class TestExpire:
    def test_get_before_ttl_elapses_succeeds(self, store: SessionStore, clock: FakeClock) -> None:
        session = store.create(tenant_id=TENANT_A, user_id=USER_1, ttl_seconds=60.0)
        clock.advance(59)
        assert store.get(tenant_id=TENANT_A, user_id=USER_1, session_id=session.session_id) is not None

    def test_get_after_ttl_elapses_returns_no_stale_context(self, store: SessionStore, clock: FakeClock) -> None:
        # SES-003 expiry (advance_clock_past_ttl) -> no_stale_memory
        session = store.create(tenant_id=TENANT_A, user_id=USER_1, ttl_seconds=60.0)
        clock.advance(61)

        with pytest.raises(SessionNotFoundError) as excinfo:
            store.get(tenant_id=TENANT_A, user_id=USER_1, session_id=session.session_id)
        assert excinfo.value.reason == "expired"

    def test_administrative_expire_forces_immediate_expiry(self, store: SessionStore) -> None:
        session = store.create(tenant_id=TENANT_A, user_id=USER_1, ttl_seconds=3600.0)

        store.expire(tenant_id=TENANT_A, user_id=USER_1, session_id=session.session_id)

        with pytest.raises(SessionNotFoundError) as excinfo:
            store.get(tenant_id=TENANT_A, user_id=USER_1, session_id=session.session_id)
        assert excinfo.value.reason == "expired"

    def test_expire_denies_a_different_owner(self, store: SessionStore) -> None:
        session = store.create(tenant_id=TENANT_A, user_id=USER_1)
        with pytest.raises(SessionNotFoundError):
            store.expire(tenant_id=TENANT_A, user_id=USER_2, session_id=session.session_id)


class TestClear:
    def test_clear_removes_recent_turns_and_summary(self, store: SessionStore) -> None:
        # SES-004 clear -> recent_turns_and_summary_removed
        session = store.create(tenant_id=TENANT_A, user_id=USER_1)
        with_history = session.model_copy(
            update={
                "recent_turns": [
                    SessionTurn(turn_id="TURN-001", role=TurnRole.USER, timestamp=session.created_at, content="hello")
                ],
                "summary": MemorySummary(
                    summary_version=1, text="prior context", source_turn_ids=["TURN-000"], created_at=session.created_at
                ),
            }
        )
        store.save(with_history)

        cleared = store.clear(tenant_id=TENANT_A, user_id=USER_1, session_id=session.session_id)

        assert cleared.recent_turns == []
        assert cleared.summary is None

    def test_clear_keeps_the_session_itself_reloadable(self, store: SessionStore) -> None:
        # clear/reset removes conversational state but is not delete - the
        # session shell (ownership, timestamps) survives.
        session = store.create(tenant_id=TENANT_A, user_id=USER_1)

        store.clear(tenant_id=TENANT_A, user_id=USER_1, session_id=session.session_id)

        reloaded = store.get(tenant_id=TENANT_A, user_id=USER_1, session_id=session.session_id)
        assert reloaded.session_id == session.session_id
        assert reloaded.tenant_id == TENANT_A

    def test_clear_bumps_version(self, store: SessionStore) -> None:
        session = store.create(tenant_id=TENANT_A, user_id=USER_1)
        cleared = store.clear(tenant_id=TENANT_A, user_id=USER_1, session_id=session.session_id)
        assert cleared.version == session.version + 1

    def test_clear_denies_a_different_owner(self, store: SessionStore) -> None:
        session = store.create(tenant_id=TENANT_A, user_id=USER_1)
        with pytest.raises(SessionNotFoundError):
            store.clear(tenant_id=TENANT_B, user_id=USER_1, session_id=session.session_id)


class TestDelete:
    def test_delete_makes_the_session_unavailable(self, store: SessionStore) -> None:
        session = store.create(tenant_id=TENANT_A, user_id=USER_1)

        store.delete(tenant_id=TENANT_A, user_id=USER_1, session_id=session.session_id)

        with pytest.raises(SessionNotFoundError) as excinfo:
            store.get(tenant_id=TENANT_A, user_id=USER_1, session_id=session.session_id)
        assert excinfo.value.reason == "not_found"

    def test_delete_denies_a_different_owner_and_leaves_session_intact(self, store: SessionStore) -> None:
        session = store.create(tenant_id=TENANT_A, user_id=USER_1)

        with pytest.raises(SessionNotFoundError):
            store.delete(tenant_id=TENANT_A, user_id=USER_2, session_id=session.session_id)

        # The wrong-owner delete attempt must not have removed it.
        assert store.get(tenant_id=TENANT_A, user_id=USER_1, session_id=session.session_id) is not None

    def test_delete_nonexistent_session_raises_not_found(self, store: SessionStore) -> None:
        with pytest.raises(SessionNotFoundError):
            store.delete(tenant_id=TENANT_A, user_id=USER_1, session_id="SES-never-created")


class TestExpiryAndClearIntegrateWithMemory:
    """Task 8's "a cleared session cannot reconstruct old context from
    stale cache" and "expired session does not return stale context",
    proven against the rest of the memory stack (Task 5/6), not just the
    store's own return value."""

    def _session_with_history(self, store: SessionStore, **create_kwargs: object) -> str:
        session = store.create(tenant_id=TENANT_A, user_id=USER_1, **create_kwargs)
        with_history = session.model_copy(
            update={
                "recent_turns": [
                    SessionTurn(turn_id="TURN-001", role=TurnRole.USER, timestamp=session.created_at, content="What are Supplier Alpha's payment terms?")
                ],
                "summary": MemorySummary(
                    summary_version=1, text="earlier discussion of Supplier Alpha", source_turn_ids=["TURN-000"], created_at=session.created_at
                ),
            }
        )
        store.save(with_history)
        return session.session_id

    def test_cleared_session_produces_an_empty_memory_context(self, store: SessionStore) -> None:
        session_id = self._session_with_history(store)

        store.clear(tenant_id=TENANT_A, user_id=USER_1, session_id=session_id)
        reloaded = store.get(tenant_id=TENANT_A, user_id=USER_1, session_id=session_id)
        context = build_memory_context(reloaded)

        assert context.is_empty is True
        assert context.summary is None
        assert context.included_turns == ()

    def test_compacting_a_cleared_session_does_not_resurrect_old_content(self, store: SessionStore) -> None:
        # A cleared session must stay empty even when compaction runs on
        # it afterward - clear() is not merely "eligible for compaction",
        # it removed the content outright; there is nothing left to fold
        # into a summary, "reconstructed from stale cache" or otherwise.
        session_id = self._session_with_history(store)
        store.clear(tenant_id=TENANT_A, user_id=USER_1, session_id=session_id)
        cleared = store.get(tenant_id=TENANT_A, user_id=USER_1, session_id=session_id)

        result = compact_session(cleared, FakeSummarizer())

        assert result is cleared  # no-op: nothing omitted, nothing to compact
        assert result.summary is None
        assert result.recent_turns == []

    def test_expired_session_can_never_be_loaded_to_build_a_memory_context(self, store: SessionStore, clock: FakeClock) -> None:
        # The only way any caller (including the context builder) obtains
        # a SessionState is through get() - which already fails closed on
        # expiry before returning one. There is no second path a stale
        # SessionState could reach build_memory_context through.
        session_id = self._session_with_history(store, ttl_seconds=60.0)
        clock.advance(61)

        with pytest.raises(SessionNotFoundError) as excinfo:
            store.get(tenant_id=TENANT_A, user_id=USER_1, session_id=session_id)
        assert excinfo.value.reason == "expired"

    def test_ttl_is_independently_configurable_per_session_not_one_global_cutoff(self, store: SessionStore, clock: FakeClock) -> None:
        short_lived = store.create(tenant_id=TENANT_A, user_id=USER_1, ttl_seconds=30.0)
        long_lived = store.create(tenant_id=TENANT_A, user_id=USER_1, ttl_seconds=3600.0)

        clock.advance(31)

        with pytest.raises(SessionNotFoundError) as excinfo:
            store.get(tenant_id=TENANT_A, user_id=USER_1, session_id=short_lived.session_id)
        assert excinfo.value.reason == "expired"
        # The longer-TTL session, created in the same store at the same
        # time, is unaffected.
        assert store.get(tenant_id=TENANT_A, user_id=USER_1, session_id=long_lived.session_id) is not None


class TestClockContract:
    def test_clock_type_is_a_zero_arg_callable(self) -> None:
        example: Clock = lambda: datetime.now(UTC)  # noqa: E731
        assert isinstance(example(), datetime)
