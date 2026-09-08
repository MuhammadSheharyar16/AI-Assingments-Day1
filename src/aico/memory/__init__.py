"""
Day 8 memory boundary. `src/aico/memory/` is the only place session
conversational state is typed and persisted (`structure_rules`, Day 8
pack).

Task 1 exports the versioned session-memory contracts. Task 2 adds the
session-store abstraction (`SessionStore`) and its two implementations,
plus the typed store failures. Later tasks add the context builder
(Task 5) and summarizer (Task 6) in sibling modules.
"""
from aico.memory.errors import SessionConflictError, SessionError, SessionNotFoundError
from aico.memory.models import (
    SESSION_STATE_SCHEMA_VERSION,
    MemorySummary,
    SessionState,
    SessionTurn,
    TurnRole,
)
from aico.memory.store import (
    DEFAULT_SESSION_DB_PATH,
    DEFAULT_SESSION_TTL_SECONDS,
    InMemorySessionStore,
    SessionStore,
    SqliteSessionStore,
    new_session_id,
)

__all__ = [
    "SESSION_STATE_SCHEMA_VERSION",
    "MemorySummary",
    "SessionState",
    "SessionTurn",
    "TurnRole",
    "SessionError",
    "SessionNotFoundError",
    "SessionConflictError",
    "SessionStore",
    "InMemorySessionStore",
    "SqliteSessionStore",
    "new_session_id",
    "DEFAULT_SESSION_TTL_SECONDS",
    "DEFAULT_SESSION_DB_PATH",
]
