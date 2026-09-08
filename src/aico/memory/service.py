"""
Day 8 Task 3 — session isolation: the seam between the Day 6 trusted
identity boundary (`api/identity.py`) and the session store (Task 2).

`MemorySessionService` is the ONLY supported way application/API code
resolves, creates, saves, clears, deletes or expires a session. Every
public method here takes a `TrustedIdentity` - the exact type Day 6's
`get_trusted_identity` dependency produces from a verified bearer token -
never raw `tenant_id`/`user_id` strings. That is not a style preference:
it makes "never accept tenant/user ownership from untrusted session
payload fields" (Day 8 working rules) structurally true rather than a
convention someone has to remember, because there is no parameter here a
request-body field could even be assigned to. `session_id` is the one
thing a caller may legitimately supply from the request - a session id is
an opaque handle, not authorization by itself; ownership is proven only
by whether the store's ownership-scoped lookup for THIS identity
succeeds, on every call, regardless of what the request claims.

This module adds no isolation logic of its own beyond that: the actual
ownership-scoped-lookup guarantee (a wrong-owner request and a
nonexistent session id are indistinguishable, per `store.py`'s module
docstring) already lives in `SessionStore`, from Task 2. What Task 3
adds is the guarantee that nothing sitting between the trusted identity
and the store can widen that boundary - see `tests/test_day08_isolation.py`,
which proves the full case matrix from the assignment brief
(`same tenant/same user` allowed; `same tenant/different user`,
`different tenant/same-looking user`, a different session under the same
owner, and a guessed/nonexistent id all denied identically) through this
service, not only against the raw store.

Day 8 Task 9 — `update_session()` adds bounded lost-update protection on
top of `save_session()`'s bare optimistic-concurrency primitive. The
store (Task 2) only ever DETECTS a stale write and rejects it
(`SessionConflictError`) - it never retries, and never should, since it
has no way to know what a caller's intended change even was. This method
is the one place that RECOVERS from that rejection: it reloads the
session's current state and re-applies the caller's own mutation on top
of it, up to a small, fixed number of attempts - "two writers cannot
silently overwrite each other's accepted turn" (Task 9) means the second
writer's change must still land, not merely that the conflict was logged
and dropped. `tests/test_day08_concurrency.py` proves this against a real
conflict (not a mocked one) and proves the retry is bounded, not
unbounded.
"""
from __future__ import annotations

from collections.abc import Callable

from aico.api.identity import TrustedIdentity
from aico.memory.errors import SessionConflictError, SessionNotFoundError
from aico.memory.models import SessionState
from aico.memory.store import DEFAULT_SESSION_TTL_SECONDS, SessionStore

# Bounded, not unbounded (Task 9's own required behavior). 3 is enough to
# absorb an ordinary handful of overlapping writers on one session without
# looping indefinitely under sustained contention - see
# `test_day08_concurrency.py::test_bounded_retry_gives_up_rather_than_looping_forever`
# for what happens once this is exhausted: the caller sees the same
# `SessionConflictError` `save_session` would have raised on a single
# attempt, never a silent, indefinite retry.
DEFAULT_MAX_SAVE_ATTEMPTS = 3


class MemorySessionService:
    """Thin, identity-bound wrapper around a `SessionStore`. Holds no
    state of its own beyond the store and the default TTL - safe to
    construct once per process (or once per request, both are fine) since
    all real state lives in the injected store."""

    def __init__(self, store: SessionStore, *, default_ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS):
        self._store = store
        self._default_ttl_seconds = default_ttl_seconds

    def create_session(self, identity: TrustedIdentity, *, ttl_seconds: float | None = None) -> SessionState:
        """Create a new, empty session owned by `identity`. There is no
        way to create a session for anyone other than the caller's own
        verified identity - ownership is taken from `identity`, never
        from a parameter a request body could set."""

        return self._store.create(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            ttl_seconds=ttl_seconds if ttl_seconds is not None else self._default_ttl_seconds,
        )

    def load_session(self, identity: TrustedIdentity, session_id: str) -> SessionState:
        """Return `session_id` if and only if it is owned by `identity`
        and has not expired. Raises `SessionNotFoundError` - identically,
        by construction (see `store.py`) - whether `session_id` never
        existed, belongs to a different user, belongs to a different
        tenant, or names a different session entirely under this same
        identity. Never returns `None` and never returns another
        identity's session."""

        return self._store.get(tenant_id=identity.tenant_id, user_id=identity.user_id, session_id=session_id)

    def save_session(self, identity: TrustedIdentity, session: SessionState) -> SessionState:
        """Persist an updated session (Task 9's optimistic-concurrency
        contract - see `SessionStore.save`). Defense in depth beyond what
        the store already checks: if `session`'s own `tenant_id`/`user_id`
        do not match `identity`, this is rejected before ever reaching the
        store, the same way a mismatched lookup is - a caller cannot save
        a session object under a different identity's ownership than the
        one it is authenticated as, even if something upstream
        constructed that object incorrectly."""

        if session.tenant_id != identity.tenant_id or session.user_id != identity.user_id:
            raise SessionNotFoundError(reason="not_found")
        return self._store.save(session)

    def clear_session(self, identity: TrustedIdentity, session_id: str) -> SessionState:
        """Reset `session_id`'s conversational state (Task 8's
        clear/reset) if owned by `identity`; same fail-closed denial as
        `load_session` otherwise."""

        return self._store.clear(tenant_id=identity.tenant_id, user_id=identity.user_id, session_id=session_id)

    def delete_session(self, identity: TrustedIdentity, session_id: str) -> None:
        """Permanently remove `session_id` if owned by `identity`; same
        fail-closed denial as `load_session` otherwise."""

        self._store.delete(tenant_id=identity.tenant_id, user_id=identity.user_id, session_id=session_id)

    def expire_session(self, identity: TrustedIdentity, session_id: str) -> None:
        """Administratively force `session_id` to be already-expired
        (Task 8) if owned by `identity`; same fail-closed denial as
        `load_session` otherwise."""

        self._store.expire(tenant_id=identity.tenant_id, user_id=identity.user_id, session_id=session_id)

    def update_session(
        self,
        identity: TrustedIdentity,
        session_id: str,
        mutate: Callable[[SessionState], SessionState],
        *,
        max_attempts: int = DEFAULT_MAX_SAVE_ATTEMPTS,
    ) -> SessionState:
        """Load-mutate-save with bounded retry on a lost race (Task 9).

        `mutate` receives the session's CURRENT state - freshly reloaded
        from the store on every attempt, never the same stale object
        twice - and returns the desired new state; it must be a pure
        function of that input (e.g. "append these turns to whatever
        `recent_turns` is right now"), never something that assumes a
        particular version survived, because it may be invoked more than
        once. This is what makes concurrent writers additive rather than
        last-write-wins: if another writer's change is already reflected
        in the state `mutate` sees on a retry, that writer's turn is
        still there when this call's own change is layered on top of it -
        "two writers cannot silently overwrite each other's accepted
        turn."

        Raises `SessionConflictError` if `max_attempts` is exhausted still
        losing the race (`max_attempts` must be a positive integer) - a
        bounded, documented policy (Task 9's own required behavior), never
        an unbounded loop. Raises `SessionNotFoundError` immediately,
        without retrying, if the session cannot be loaded at all (deleted/
        expired/wrong owner) - retrying against a session that does not
        exist could never succeed."""

        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")

        last_conflict: SessionConflictError | None = None
        for _ in range(max_attempts):
            current = self.load_session(identity, session_id)
            desired = mutate(current)
            try:
                return self.save_session(identity, desired)
            except SessionConflictError as exc:
                last_conflict = exc
        assert last_conflict is not None  # max_attempts > 0, so the loop ran at least once
        raise last_conflict
