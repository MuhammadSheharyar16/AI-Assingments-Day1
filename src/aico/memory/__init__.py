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
section stays Task 7's job, not this module's. Task 6 adds `Summarizer`
and `compact_session`, which replaces the turns a session has outgrown
its budget for with a bounded, provenance-carrying summary - not yet
called from the session-saving path (Task 4's `app.py`); wiring exactly
when compaction runs during a request is left for the task that finishes
threading memory into the live pipeline.
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
from aico.memory.summarizer import (
    DEFAULT_MAX_SUMMARY_TOKENS,
    FakeSummarizer,
    ModelGatewaySummarizer,
    Summarizer,
    SummarizerResult,
    compact_session,
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
    "Summarizer",
    "SummarizerResult",
    "FakeSummarizer",
    "ModelGatewaySummarizer",
    "compact_session",
    "DEFAULT_MAX_SUMMARY_TOKENS",
]
