"""
Day 6/8 session lifecycle helpers, shared by every route that resolves a
trusted identity's session and records a turn against it - `POST /ask`
(`app.py`) and Day 9's `POST /ask/governed` (`control_plane.py`).

Extracted unchanged out of `app.py` (Day 8 Task 4/11) so Day 9's new route
reuses the exact same, already-tested session-resolution and turn-storage
behavior rather than a second copy of it - two call sites drifting out of
sync on isolation/logging/retry semantics is exactly the kind of defect
Day 8's own tests exist to catch. `app.py`'s `/ask` handler is otherwise
untouched: same log lines, same span/telemetry fields, same retry and
isolation behavior as before this file existed.

`record_turn` takes `blocked`/`answer_text` explicitly rather than a whole
route-specific response object, so it stays agnostic to which HTTP
contract (`AskResponse` today, `GovernedAskResponse` for Day 9) a caller
is building a turn from - the one thing it needs to know is "was this
question rejected by policy" and "is there answer text to also store",
both of which are safe, already-public facts by the time either caller
reaches this function.
"""
from __future__ import annotations

from datetime import UTC, datetime

from aico.api.errors import ApiError
from aico.api.identity import TrustedIdentity
from aico.memory.errors import SessionConflictError, SessionNotFoundError
from aico.memory.models import SessionState, SessionTurn, TurnRole
from aico.memory.service import MemorySessionService
from aico.memory.summarizer import Summarizer, compact_session
from aico.observability.logging import log_event
from aico.observability.metrics import record_compaction


class SessionAccessError(ApiError):
    """Day 8 Task 4 - raised when a caller-supplied `session_id` does not
    resolve for the trusted identity making this request. Deliberately
    the same outward shape (404, one generic message) whether the id
    never existed, belongs to a different user, or belongs to a different
    tenant - it adds no disclosure beyond what `SessionNotFoundError`
    (Task 3) already refused to reveal; this class only maps that refusal
    onto the shared `ErrorResponse` envelope via `register_error_handlers`."""

    status_code = 404
    error_code = "session_not_found"

    def __init__(self) -> None:
        super().__init__("session not found")


def resolve_session(
    identity: TrustedIdentity,
    requested_session_id: str | None,
    memory_service: MemorySessionService,
    *,
    request_id: str,
    correlation_id: str,
) -> SessionState:
    """Session Resolution / Load Bounded Session State (build_outcome flow
    diagram). A supplied id must belong to `identity`; omitting it starts
    a fresh session for `identity` - never for anyone else, since ownership
    here can only ever come from `identity`, not from the request body.

    Day 8 Task 11 - each outcome (created/loaded/denied) emits one
    `stage="session_lifecycle"` log line, carrying only sanitized counts/
    categories (session_id, version, recent-turn count; on denial,
    `SessionNotFoundError.reason` alone - Task 3's already-safe "isolation
    denial category", never which tenant/session was denied) - never turn
    or summary content."""

    if requested_session_id is None:
        session = memory_service.create_session(identity)
        log_event(
            request_id=request_id,
            correlation_id=correlation_id,
            stage="session_lifecycle",
            outcome="created",
            session_id=session.session_id,
            session_version=session.version,
        )
        return session

    try:
        session = memory_service.load_session(identity, requested_session_id)
    except SessionNotFoundError as exc:
        log_event(
            request_id=request_id,
            correlation_id=correlation_id,
            stage="session_lifecycle",
            outcome="denied",
            error_category=exc.reason,
        )
        raise SessionAccessError() from exc

    log_event(
        request_id=request_id,
        correlation_id=correlation_id,
        stage="session_lifecycle",
        outcome="loaded",
        session_id=session.session_id,
        session_version=session.version,
        recent_turn_count=len(session.recent_turns),
    )
    return session


def record_turn(
    identity: TrustedIdentity,
    session: SessionState,
    *,
    question: str,
    blocked: bool,
    answer_text: str | None,
    memory_service: MemorySessionService,
    summarizer: Summarizer,
    request_id: str,
    correlation_id: str,
) -> None:
    """Store Safe Turn Result / Update Session State. Never raises past
    this point - even after Task 9's bounded retries are exhausted, a
    lost race on this session must not turn an already-correctly-produced
    answer into a failed request; it is logged instead of silently
    disappearing. Session memory is supplementary conversational context,
    not the source of truth this request's answer depended on (Day 8's
    core rule).

    `blocked`/`answer_text` are the two route-agnostic facts this function
    needs - whichever HTTP contract a caller maps its pipeline result onto
    (`AskResponse` for `/ask`, `GovernedAskResponse` for `/ask/governed`),
    it derives these two values from its own result variant before calling
    here; this function itself never inspects a route-specific status
    enum."""

    now = datetime.now(UTC)
    turns = [
        SessionTurn(
            turn_id=f"{request_id}-user",
            role=TurnRole.USER,
            timestamp=now,
            content=question,
            blocked=blocked,
        )
    ]
    if answer_text is not None:
        turns.append(SessionTurn(turn_id=f"{request_id}-assistant", role=TurnRole.ASSISTANT, timestamp=now, content=answer_text))

    # Task 11 - captured from inside _mutate (identity check against
    # compact_session's own no-op contract - `is not` is exact, not an
    # inference from before/after summary_version) so it reflects
    # whichever attempt actually got saved, retries included.
    compaction = {"occurred": False}

    def _mutate(current: SessionState) -> SessionState:
        # `current` is reloaded fresh on every retry attempt (Task 9) -
        # appending this call's own turns onto whatever is actually
        # stored right now, not onto a possibly-stale copy, is what makes
        # a concurrent writer's turn additive rather than overwritten.
        appended = current.model_copy(update={"recent_turns": [*current.recent_turns, *turns]})
        # Task 6 - a safe no-op whenever nothing exceeds the budget yet,
        # so this can run on every turn rather than needing its own
        # threshold check here (compact_session's own docstring).
        compacted = compact_session(appended, summarizer, now=now)
        compaction["occurred"] = compacted is not appended
        return compacted

    try:
        saved = memory_service.update_session(identity, session.session_id, _mutate)
    except SessionConflictError:
        # Task 9's bounded retry (update_session) was exhausted - a rare,
        # sustained-contention case, not the ordinary "detected once,
        # retried once" path, which already recovered silently above.
        log_event(
            request_id=request_id,
            correlation_id=correlation_id,
            stage="session_lifecycle",
            outcome="conflict_exhausted_retries",
            session_id=session.session_id,
        )
        return
    except SessionNotFoundError:
        # The session expired/was cleared between resolution and this
        # save - the answer above was still valid and correctly produced;
        # only the turn-history write is skipped.
        log_event(
            request_id=request_id,
            correlation_id=correlation_id,
            stage="session_lifecycle",
            outcome="session_unavailable",
            session_id=session.session_id,
        )
        return

    # Task 11 - "compaction occurred", "session version", "recent-turn
    # count", "summary source-turn count": counts/booleans only, never
    # turn or summary text.
    record_compaction(occurred=compaction["occurred"])
    log_event(
        request_id=request_id,
        correlation_id=correlation_id,
        stage="session_lifecycle",
        outcome="saved",
        session_id=saved.session_id,
        session_version=saved.version,
        recent_turn_count=len(saved.recent_turns),
        compaction_occurred=compaction["occurred"],
        summary_source_turn_count=len(saved.summary.source_turn_ids) if saved.summary else 0,
    )
