"""
Day 8 memory boundary. `src/aico/memory/` is the only place session
conversational state is typed and persisted (`structure_rules`, Day 8
pack).

Task 1 exports the versioned session-memory contracts. Task 2 adds the
session-store abstraction (`SessionStore`) and its two implementations,
plus the typed store failures. Task 3 adds `MemorySessionService`, the
identity-bound seam application/API code actually resolves sessions
through - see its module docstring for why isolation depends on this
being the only entry point. Task 5 adds `build_memory_context`, the
budget-bounded selection over a loaded session's summary/recent turns -
see its module docstring for why rendering that into an actual prompt
section stays Task 7's job, not this module's. Task 6 adds the
summarizer in a sibling module.
"""
from aico.memory.context_builder import (
    DEFAULT_MAX_MEMORY_TOKENS,
    DEFAULT_MAX_RECENT_TURNS,
    DEFAULT_MEMORY_BUDGET,
    MemoryBudget,
    MemoryContext,
    build_memory_context,
    estimate_tokens,
)
from aico.memory.errors import SessionConflictError, SessionError, SessionNotFoundError
from aico.memory.models import (
    SESSION_STATE_SCHEMA_VERSION,
    MemorySummary,
    SessionState,
    SessionTurn,
    TurnRole,
)
from aico.memory.service import MemorySessionService
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
    "MemorySessionService",
    "MemoryBudget",
    "MemoryContext",
    "build_memory_context",
    "estimate_tokens",
    "DEFAULT_MAX_MEMORY_TOKENS",
    "DEFAULT_MAX_RECENT_TURNS",
    "DEFAULT_MEMORY_BUDGET",
]
