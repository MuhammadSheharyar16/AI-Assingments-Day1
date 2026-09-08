"""
Day 8 — typed session-store failures (Task 2).

Shared by both `SessionStore` implementations (`store.py`) and, later,
the memory service/API integration (Task 4) - a caller distinguishes
"session unavailable" from "concurrent write lost" with one
`except SessionNotFoundError` / `except SessionConflictError`, the same
pattern Day 6's `IdentityError` uses for the trust boundary
(`api/identity.py`).
"""
from __future__ import annotations

from typing import Literal

# Safe, non-disclosing category for telemetry only (Task 11's "isolation
# denial category") - never part of the exception's outward message.
NotFoundReason = Literal["not_found", "expired"]


class SessionError(Exception):
    """Base class for every typed failure `aico.memory` raises. Never
    raised directly - see `SessionNotFoundError` / `SessionConflictError`."""


class SessionNotFoundError(SessionError):
    """Raised by `SessionStore.get()`/`save()`/`clear()`/`delete()`/
    `expire()` whenever a session cannot be returned to the caller under
    its trusted `tenant_id` + `user_id` + `session_id` scoping.

    Deliberately one exception, one outward message ("session not
    found"), for every cause: the session id genuinely never existed, it
    exists but belongs to a different tenant, it exists but belongs to a
    different user under the same tenant, or a *different* session under
    the same owner was asked for. Day 8's working rule is "fail closed
    without revealing whether another tenant's session exists" (Task 3) -
    collapsing all of those causes onto one message is what makes that
    true: both `SessionStore` implementations scope a lookup by
    `tenant_id` + `user_id` + `session_id` together, in one step (a SQL
    `WHERE` clause / a single dict-then-owner check), never "find by
    session_id, then check ownership after" - so a wrong-owner request
    and a nonexistent id take the exact same code path and raise the
    exact same error. There is nothing for a caller to learn from this
    exception about whether another tenant's session exists.

    `reason` exists only for internal telemetry (Task 11), never for the
    outward-facing message:
      - `"not_found"` - the ownership-scoped lookup found nothing at all.
        Covers "never existed" and "belongs to someone else" identically,
        by construction (see the module docstring in `store.py`).
      - `"expired"` - the lookup *did* find the caller's own session, but
        its `expires_at` has passed (Task 8). Only ever reachable for a
        session's rightful owner - a wrong-owner request never reaches
        this branch, so it can never learn whether someone else's session
        merely expired vs. never existed.
    """

    def __init__(self, reason: NotFoundReason = "not_found"):
        self.reason = reason
        super().__init__("session not found")


class SessionConflictError(SessionError):
    """Raised by `SessionStore.save()` when the caller's `session.version`
    no longer matches the version currently stored - a concurrent writer
    already saved an accepted turn first (Task 9, optimistic concurrency /
    lost-update protection). The write is rejected outright, never
    silently merged or retried in a loop by the store itself - Task 9's
    "no unbounded retry loop" rule means any retry is a bounded,
    caller-level policy, not something this exception hides.

    `expected_version`/`actual_version` are plain integers - safe to log
    and to return to a caller, unlike session content."""

    def __init__(self, *, expected_version: int, actual_version: int):
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(f"stale session version: expected {expected_version}, store has {actual_version}")
