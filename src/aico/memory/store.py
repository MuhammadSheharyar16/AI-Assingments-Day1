"""
Day 8 Task 2 — the session-store abstraction: the seam between session
memory (`models.py`, Task 1) and however it is actually persisted.

`SessionStore` is the interface every later Day 8 piece (the memory
service, the context builder, the `/ask` API integration) depends on -
never a concrete store class directly (`structure_rules`: "Do not let
application/API code access session database internals directly").

Two implementations, against the exact same interface:
- `SqliteSessionStore` - the real, local implementation for the lab
  (`structure_rules`: "A local SQLite implementation is acceptable").
  One row per session, keyed by `session_id`, storing the full
  `SessionState` as JSON in one column. There is no separate SQL table per
  field (`recent_turns`, `summary`, ...) - `models.py` (Task 1) stays the
  single source of truth for that shape, so the store never has its own
  competing schema to drift out of sync with it.
- `InMemorySessionStore` - deterministic, offline, no filesystem. Every
  Day 8 test uses this (`structure_rules`: "An in-memory fake/store is
  required for deterministic tests"). Mirrors `retrieval/embedding_provider.py`'s
  real-vs-fake split against one shared interface.

Both share one critical property, proven identically for each by
`tests/test_day08_session_lifecycle.py`: every lookup is scoped by
`tenant_id` + `user_id` + `session_id` *together*, in a single step -
never "find by session_id, then check ownership after." A session that
belongs to a different tenant/user, and a `session_id` that was never
created at all, take the exact same code path and raise the exact same
`SessionNotFoundError(reason="not_found")` (see `errors.py`). Neither
store can be used to enumerate or distinguish another tenant's sessions
(Day 8 working rule / Task 3: "fail closed without revealing whether
another tenant's session exists").

An injectable `Clock` (`Callable[[], datetime]`, always expected to return
a timezone-aware `datetime`) is accepted by both constructors so expiry
(Task 8) is deterministic in tests - never `time.sleep`, always a fake
clock advanced explicitly by the caller.

Neither implementation is safe for use across multiple OS processes ( no
distributed locking - `structure_rules`: "External Redis or another
managed cache is not required"); a single in-process `threading.Lock`
around every read-then-write is enough to make `save()`'s
optimistic-concurrency check (Task 9) reliable for this lab.
"""
from __future__ import annotations

import secrets
import sqlite3
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aico.memory.errors import SessionConflictError, SessionNotFoundError
from aico.memory.models import SESSION_STATE_SCHEMA_VERSION, SessionState

Clock = Callable[[], datetime]

# Lab default: 30 minutes of inactivity (Task 8's "named/configurable
# session TTL" - callers may pass a different `ttl_seconds` to `create()`).
DEFAULT_SESSION_TTL_SECONDS: float = 30 * 60.0

DEFAULT_SESSION_DB_PATH = Path("data/sessions/sessions.db")

# secrets.token_urlsafe(24) ~= 192 bits of entropy - stable and
# effectively non-guessable for the lab (Task 2 required behavior: "session
# IDs are stable and non-guess-based enough for the lab"). A session id
# doubles as the only thing a caller needs to resume a conversation, so it
# must be as hard to guess as a bearer token, not merely unique like a
# correlation/trace id (contrast `api/correlation.py`'s plain `uuid4()`).
_SESSION_ID_ENTROPY_BYTES = 24


def _utcnow() -> datetime:
    return datetime.now(UTC)


def new_session_id() -> str:
    """Generate a new session id. Never accept a caller-supplied id as
    authorization - see `SessionStore.create`'s docstring for the one
    reproducibility exception."""

    return f"SES-{secrets.token_urlsafe(_SESSION_ID_ENTROPY_BYTES)}"


class SessionStore(ABC):
    """Interface every session-memory caller depends on. Method names are
    a Day 8 design choice (`structure_rules`: "Exact method names are your
    choice") - `create`/`get`/`save`/`clear`/`delete`/`expire` map onto the
    assignment's required operation list (create, get, save/update,
    clear/delete, expire)."""

    @abstractmethod
    def create(
        self,
        *,
        tenant_id: str,
        user_id: str,
        ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
        session_id: str | None = None,
    ) -> SessionState:
        """Create and persist a new, empty session owned by
        `tenant_id`/`user_id`. `session_id` is normally left unset (the
        store generates one via `new_session_id()`); accepting an explicit
        id exists only to reproduce a fixture/test case deterministically
        - it is never how a real caller's session gets its id, and it is
        rejected with `ValueError` if that id is already in use by any
        session, regardless of owner (never silently reassign an existing
        id to a different tenant/user)."""

    @abstractmethod
    def get(self, *, tenant_id: str, user_id: str, session_id: str) -> SessionState:
        """Return the session owned by exactly this tenant/user/session-id
        triple. Raises `SessionNotFoundError` - never `None`, never a
        partial/default session - if it does not exist, belongs to a
        different owner, or has expired (Task 2/3/8)."""

    @abstractmethod
    def save(self, session: SessionState) -> SessionState:
        """Optimistic-concurrency update (Task 9). `session.version` must
        equal the version currently stored - the version the caller's copy
        was loaded/created with. On success, returns a new `SessionState`
        with `version` incremented by one and `updated_at` refreshed from
        the store's clock; the passed-in `session` object is left
        unmodified. Raises `SessionConflictError` on a stale version, and
        `SessionNotFoundError` if the session no longer exists under its
        own tenant/user/session-id."""

    @abstractmethod
    def clear(self, *, tenant_id: str, user_id: str, session_id: str) -> SessionState:
        """Reset a session's conversational state: `recent_turns` and
        `summary` are removed, but the session record itself (ownership,
        `created_at`, `expires_at`) is kept - this is the Task 8
        "clear/reset" operation, distinct from `delete`. Also increments
        `reset_count` (unlike an ordinary turn-append save, which never
        touches it) - the signal `api/session_flow.py::record_turn` uses
        to detect and refuse an in-flight request's stale, pre-clear turn
        rather than silently re-populating a just-cleared session with
        it. Returns the cleared session. Raises `SessionNotFoundError`
        under the same rules as `get`."""

    @abstractmethod
    def delete(self, *, tenant_id: str, user_id: str, session_id: str) -> None:
        """Permanently remove the session record. A subsequent `get()` for
        the same id raises `SessionNotFoundError` exactly as if it had
        never existed."""

    @abstractmethod
    def expire(self, *, tenant_id: str, user_id: str, session_id: str) -> None:
        """Administratively force this session to be already-expired
        (sets `expires_at` to the store's current clock time), without
        needing to wait for or fake-advance a clock. A subsequent `get()`
        raises `SessionNotFoundError(reason="expired")`."""


class InMemorySessionStore(SessionStore):
    """Deterministic, offline fake (`structure_rules`: required for
    tests). Same ownership-scoped-lookup contract as `SqliteSessionStore`
    - see module docstring - proven identically by the shared lifecycle
    test suite."""

    def __init__(self, clock: Clock = _utcnow):
        self._clock = clock
        self._sessions: dict[str, SessionState] = {}
        self._lock = threading.Lock()

    def _scoped(self, *, tenant_id: str, user_id: str, session_id: str) -> SessionState | None:
        """Internal lookup only - returns the LIVE object actually held in
        `self._sessions`, deliberately not a copy, so `save()`/`clear()`/
        `delete()`/`expire()` can compare against it (e.g. `stored.version`)
        without extra copying on every internal call. Every PUBLIC method
        that hands a `SessionState` back to a caller must return a copy of
        what this returns, never this reference itself - see `get()`,
        `create()`, `save()`, `clear()` below."""
        session = self._sessions.get(session_id)
        if session is None or session.tenant_id != tenant_id or session.user_id != user_id:
            return None
        return session

    def create(
        self,
        *,
        tenant_id: str,
        user_id: str,
        ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
        session_id: str | None = None,
    ) -> SessionState:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")

        now = self._clock()
        new_id = session_id or new_session_id()
        with self._lock:
            if new_id in self._sessions:
                raise ValueError(f"session_id already exists: {new_id!r}")
            session = SessionState(
                schema_version=SESSION_STATE_SCHEMA_VERSION,
                session_id=new_id,
                tenant_id=tenant_id,
                user_id=user_id,
                created_at=now,
                updated_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
                version=1,
                recent_turns=[],
                summary=None,
            )
            self._sessions[new_id] = session
        # A defensive copy, not the object just stored - see `_scoped()`'s
        # docstring. Without this, a caller that mutates a mutable field
        # in place (e.g. `session.recent_turns.append(...)`) would change
        # this store's internal state directly, bypassing `save()` and
        # its version-based optimistic-concurrency check entirely - the
        # store's own single-source-of-truth guarantee would be only a
        # convention, not something this class actually enforces.
        return session.model_copy(deep=True)

    def get(self, *, tenant_id: str, user_id: str, session_id: str) -> SessionState:
        with self._lock:
            session = self._scoped(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
            if session is None:
                raise SessionNotFoundError(reason="not_found")
            if self._clock() >= session.expires_at:
                raise SessionNotFoundError(reason="expired")
            return session.model_copy(deep=True)

    def save(self, session: SessionState) -> SessionState:
        with self._lock:
            stored = self._scoped(tenant_id=session.tenant_id, user_id=session.user_id, session_id=session.session_id)
            if stored is None:
                raise SessionNotFoundError(reason="not_found")
            # A stale-but-version-matching write must not be able to
            # resurrect an already-expired session (a caller can hold a
            # pre-expiry `SessionState` snapshot whose own `version` still
            # equals what's stored, since `expire()` is itself just
            # another version-bumping mutation below - but the version
            # check alone is not the source of truth for "is this session
            # still alive"; `expires_at` is, and it must be re-checked
            # against the STORED record, not trusted from the caller's
            # possibly-stale `session` argument, on every save.
            if self._clock() >= stored.expires_at:
                raise SessionNotFoundError(reason="expired")
            if stored.version != session.version:
                raise SessionConflictError(expected_version=session.version, actual_version=stored.version)
            # `deep=True` so the object this store now holds shares no
            # mutable field (`recent_turns`, in particular) with the
            # caller's own `session` argument - otherwise a caller who
            # later mutated their in-hand `session` object in place could
            # still reach into this store's internal state, the exact
            # aliasing hole `get()`/`create()` close on the read side.
            updated = session.model_copy(deep=True, update={"version": stored.version + 1, "updated_at": self._clock()})
            self._sessions[updated.session_id] = updated
            return updated.model_copy(deep=True)

    def clear(self, *, tenant_id: str, user_id: str, session_id: str) -> SessionState:
        with self._lock:
            stored = self._scoped(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
            if stored is None:
                raise SessionNotFoundError(reason="not_found")
            cleared = stored.model_copy(
                update={
                    "recent_turns": [],
                    "summary": None,
                    "version": stored.version + 1,
                    "updated_at": self._clock(),
                    "reset_count": stored.reset_count + 1,
                }
            )
            self._sessions[session_id] = cleared
            return cleared.model_copy(deep=True)

    def delete(self, *, tenant_id: str, user_id: str, session_id: str) -> None:
        with self._lock:
            stored = self._scoped(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
            if stored is None:
                raise SessionNotFoundError(reason="not_found")
            del self._sessions[session_id]

    def expire(self, *, tenant_id: str, user_id: str, session_id: str) -> None:
        with self._lock:
            stored = self._scoped(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
            if stored is None:
                raise SessionNotFoundError(reason="not_found")
            # A real mutation, exactly like `clear()` - bumping `version`
            # here is defense in depth alongside `save()`'s own expiry
            # check above: a caller holding a pre-expiry snapshot now also
            # fails the ordinary stale-version check, not only the
            # expiry check, should either be changed independently later.
            self._sessions[session_id] = stored.model_copy(
                update={"expires_at": self._clock(), "version": stored.version + 1, "updated_at": self._clock()}
            )


class SqliteSessionStore(SessionStore):
    """Real, local implementation (`structure_rules`: "A local SQLite
    implementation is acceptable"). One row per session; the full
    `SessionState` is stored as JSON in `state_json`. `tenant_id`,
    `user_id` and `session_id` are also plain indexed columns so ownership
    scoping happens in the SQL `WHERE` clause itself - not as a second,
    separate check performed after fetching a row by id alone. That is
    what makes "wrong owner" and "id never existed" structurally
    indistinguishable outcomes (see module docstring / Task 3).

    `check_same_thread=False` plus one process-wide lock: this lab does
    not need a connection pool or distributed locking - a single lock
    around every read-then-write serializes concurrent writers exactly
    enough to make `save()`'s optimistic-concurrency check reliable
    (Task 9), without any distributed infrastructure.
    """

    def __init__(self, db_path: str | Path = DEFAULT_SESSION_DB_PATH, clock: Clock = _utcnow):
        self._clock = clock
        self._lock = threading.Lock()
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    state_json TEXT NOT NULL
                )
                """
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions (tenant_id, user_id, session_id)")
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _fetch_scoped_locked(self, *, tenant_id: str, user_id: str, session_id: str) -> SessionState | None:
        """Caller must already hold `self._lock`."""
        cursor = self._conn.execute(
            "SELECT state_json FROM sessions WHERE session_id = ? AND tenant_id = ? AND user_id = ?",
            (session_id, tenant_id, user_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return SessionState.model_validate_json(row[0])

    def _write_locked(self, session: SessionState) -> None:
        """Caller must already hold `self._lock`."""
        self._conn.execute(
            "UPDATE sessions SET expires_at = ?, version = ?, state_json = ? WHERE session_id = ? AND tenant_id = ? AND user_id = ?",
            (
                session.expires_at.isoformat(),
                session.version,
                session.model_dump_json(),
                session.session_id,
                session.tenant_id,
                session.user_id,
            ),
        )
        self._conn.commit()

    def create(
        self,
        *,
        tenant_id: str,
        user_id: str,
        ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
        session_id: str | None = None,
    ) -> SessionState:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")

        now = self._clock()
        new_id = session_id or new_session_id()
        with self._lock:
            existing = self._conn.execute("SELECT 1 FROM sessions WHERE session_id = ?", (new_id,)).fetchone()
            if existing is not None:
                raise ValueError(f"session_id already exists: {new_id!r}")
            session = SessionState(
                schema_version=SESSION_STATE_SCHEMA_VERSION,
                session_id=new_id,
                tenant_id=tenant_id,
                user_id=user_id,
                created_at=now,
                updated_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
                version=1,
                recent_turns=[],
                summary=None,
            )
            self._conn.execute(
                "INSERT INTO sessions (session_id, tenant_id, user_id, expires_at, version, state_json) VALUES (?, ?, ?, ?, ?, ?)",
                (session.session_id, session.tenant_id, session.user_id, session.expires_at.isoformat(), session.version, session.model_dump_json()),
            )
            self._conn.commit()
        return session

    def get(self, *, tenant_id: str, user_id: str, session_id: str) -> SessionState:
        with self._lock:
            session = self._fetch_scoped_locked(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
        if session is None:
            raise SessionNotFoundError(reason="not_found")
        if self._clock() >= session.expires_at:
            raise SessionNotFoundError(reason="expired")
        return session

    def save(self, session: SessionState) -> SessionState:
        with self._lock:
            stored = self._fetch_scoped_locked(tenant_id=session.tenant_id, user_id=session.user_id, session_id=session.session_id)
            if stored is None:
                raise SessionNotFoundError(reason="not_found")
            # See InMemorySessionStore.save()'s identical check: a stale-
            # but-version-matching write must not be able to resurrect an
            # already-expired session. `expires_at` on the STORED record
            # is the source of truth, re-checked on every save - never
            # trusted from the caller's possibly-stale `session` argument.
            if self._clock() >= stored.expires_at:
                raise SessionNotFoundError(reason="expired")
            if stored.version != session.version:
                raise SessionConflictError(expected_version=session.version, actual_version=stored.version)
            updated = session.model_copy(update={"version": stored.version + 1, "updated_at": self._clock()})
            self._write_locked(updated)
            return updated

    def clear(self, *, tenant_id: str, user_id: str, session_id: str) -> SessionState:
        with self._lock:
            stored = self._fetch_scoped_locked(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
            if stored is None:
                raise SessionNotFoundError(reason="not_found")
            cleared = stored.model_copy(
                update={
                    "recent_turns": [],
                    "summary": None,
                    "version": stored.version + 1,
                    "updated_at": self._clock(),
                    "reset_count": stored.reset_count + 1,
                }
            )
            self._write_locked(cleared)
            return cleared

    def delete(self, *, tenant_id: str, user_id: str, session_id: str) -> None:
        with self._lock:
            stored = self._fetch_scoped_locked(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
            if stored is None:
                raise SessionNotFoundError(reason="not_found")
            self._conn.execute("DELETE FROM sessions WHERE session_id = ? AND tenant_id = ? AND user_id = ?", (session_id, tenant_id, user_id))
            self._conn.commit()

    def expire(self, *, tenant_id: str, user_id: str, session_id: str) -> None:
        with self._lock:
            stored = self._fetch_scoped_locked(tenant_id=tenant_id, user_id=user_id, session_id=session_id)
            if stored is None:
                raise SessionNotFoundError(reason="not_found")
            # See InMemorySessionStore.expire()'s identical reasoning -
            # a real mutation, bumping `version` as defense in depth
            # alongside `save()`'s own expiry check above.
            expired = stored.model_copy(
                update={"expires_at": self._clock(), "version": stored.version + 1, "updated_at": self._clock()}
            )
            self._write_locked(expired)
