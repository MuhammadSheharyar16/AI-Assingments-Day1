"""
Day 8 memory boundary. `src/aico/memory/` is the only place session
conversational state is typed and persisted (`structure_rules`, Day 8
pack).

Task 1 exports the versioned session-memory contracts. Task 2 adds the
session-store abstraction (`SessionStore`) and its two implementations,
plus the typed store failures. Task 3 adds `MemorySessionService`, the
identity-bound seam application/API code actually resolves sessions
through. Task 5 adds `build_memory_context`, the budget-bounded selection
over a loaded session's summary/recent turns. Task 6 adds `Summarizer`
and `compact_session`, which replaces the turns a session has outgrown
its budget for with a bounded, provenance-carrying summary. Task 7 wires
both of those into `api/app.py`'s `/ask` handler and `rag/prompt_builder.py`'s
SESSION MEMORY prompt section - memory now genuinely participates in
answering, structurally unable to be treated as evidence (see
`context_builder.py`/`summarizer.py`/`aico.rag.prompt_builder`'s own
module docstrings for the full boundary rationale). Task 9 adds
`MemorySessionService.update_session`, bounded lost-update protection on
top of `save_session`'s bare optimistic-concurrency primitive - see its
own docstring for why detection (the store, Task 2) and recovery (this
method) are deliberately separate responsibilities.
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
from aico.memory.service import DEFAULT_MAX_SAVE_ATTEMPTS, MemorySessionService
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
    "DEFAULT_MAX_SAVE_ATTEMPTS",
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
