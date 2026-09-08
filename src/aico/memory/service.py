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
"""
from __future__ import annotations

from aico.api.identity import TrustedIdentity
from aico.memory.errors import SessionNotFoundError
from aico.memory.models import SessionState
from aico.memory.store import DEFAULT_SESSION_TTL_SECONDS, SessionStore


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
