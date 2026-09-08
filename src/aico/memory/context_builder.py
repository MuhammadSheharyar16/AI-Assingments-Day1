"""
Day 8 Task 5 — the bounded memory context window
(`context_budget_guidance.md`, `memory_contract_guidance.md`).

`build_memory_context()` turns a loaded `SessionState` (Task 2/3) into a
`MemoryContext`: a small, budget-bounded selection of that session's
compacted summary (Task 6, when present) and most-recent turns - never
the session's full, unbounded `recent_turns` list. This is deliberately a
pure, synchronous function over data already in hand - no store access,
no clock, no I/O - so it is trivial to unit-test and to call from
anywhere that already holds a `SessionState` (the API integration, Task
4; a future prompt builder, Task 7).

Scope, deliberately narrow: this module only SELECTS what fits.
Rendering that selection into the actual labelled "SESSION MEMORY" prompt
section, kept structurally separate from retrieved evidence, is Task 7's
job (`memory_contract_guidance.md`'s trust rule: memory is conversational
context, never an evidence source) - `MemoryContext` has no field for
retrieved evidence at all, so it is not merely a convention that this
module can't blur that boundary, it is impossible for this type to. What
Task 5 owns is the budget: named/configurable limits
(`max_memory_tokens`, `max_recent_turns`), a documented token-count
method, and the selection policy below.

Token-count method (the "documented token-count method" Task 5 requires):
whitespace-delimited word count, via the exact same `\\S+` regex
`retrieval/chunker.py`'s `WORD_RE` already uses to size a chunk's
`token_count` - one consistent, deterministic definition of "token"
project-wide (no tokenizer library dependency here, and this module does
not invent a second, incompatible definition). It undercounts a real
subword-tokenizer's count for the same text, which is exactly why the
default budget below is chosen conservatively small, not sized to exactly
match a specific model's real token limit.

Selection policy (`context_budget_guidance.md`'s "current request ->
compacted summary -> newest recent turns that fit"):
    1. The current request itself is not this module's concern at all -
       it is never part of "memory" and is never bounded by
       `max_memory_tokens`; a caller keeps it in its own section.
    2. The session's summary (Task 6), if present, is included whenever
       it fits inside `max_memory_tokens` on its own. If it does not -
       only reachable with a badly undersized budget - it is omitted
       entirely rather than truncated: the budget is a hard ceiling
       (Task 5's "context never exceeds the configured memory budget"),
       and silently truncating a summary's own words could change its
       meaning in a way a caller cannot detect.
    3. Recent turns are then added newest-first, until either
       `max_recent_turns` is reached or the next turn would exceed the
       remaining token budget - whichever happens first. The first turn
       (walking backward from newest) that does not fit stops the window:
       the result is always a contiguous "most recent" suffix, never a
       scattered subset with gaps.
    4. `blocked` turns (Task 10 - a question the input policy rejected)
       are not filtered out here. They remain real conversational history
       ("a blocked turn can still be reloaded as conversational history",
       Task 1) and count against the budget like any other turn; it is
       downstream prompt construction (Task 7) that must render them as
       untrusted context that can never become an instruction, not this
       module's job to hide that they happened.

Turns this selection leaves out (`MemoryContext.omitted_turns`) are
exactly the ones "eligible for compaction" (Task 5's required behavior) -
Task 6's summarizer consumes that list directly rather than
recomputing which turns are "old" on its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from aico.memory.models import MemorySummary, SessionState, SessionTurn
from aico.retrieval.chunker import WORD_RE

# Chosen conservatively small, not to exactly match any one model's real
# context window: this module's word-count estimate undercounts a real
# subword tokenizer, and the memory section is only ever one part of the
# assembled prompt (system instructions + memory + current question +
# retrieved evidence, Task 7's layout) - it must leave the rest of that
# budget to the parts of the prompt that actually determine correctness.
DEFAULT_MAX_MEMORY_TOKENS = 800

# 6 user/assistant exchange pairs - enough recent context to resolve a
# follow-up like "what about its X", bounded well short of "unlimited
# history is unsafe and expensive" (working rules).
DEFAULT_MAX_RECENT_TURNS = 12


def estimate_tokens(text: str) -> int:
    """The module's one documented token-count method: whitespace-
    delimited word count (`retrieval/chunker.py`'s `WORD_RE`, `\\S+`) -
    see module docstring for why this definition, not a new one."""

    return len(WORD_RE.findall(text))


@dataclass(frozen=True)
class MemoryBudget:
    """Named/configurable context-budget limits (Task 5's required
    `max_memory_tokens` / `max_recent_turns`). A plain in-process value,
    never persisted - contrast `SessionState` (Task 1), which is."""

    max_memory_tokens: int = DEFAULT_MAX_MEMORY_TOKENS
    max_recent_turns: int = DEFAULT_MAX_RECENT_TURNS

    def __post_init__(self) -> None:
        if self.max_memory_tokens <= 0:
            raise ValueError("max_memory_tokens must be positive")
        if self.max_recent_turns <= 0:
            raise ValueError("max_recent_turns must be positive")


DEFAULT_MEMORY_BUDGET = MemoryBudget()


@dataclass(frozen=True)
class MemoryContext:
    """The bounded result of `build_memory_context()`. Deliberately has
    no field that could ever carry retrieved evidence - Day 8's rule that
    memory cannot replace evidence is structurally true of this type, not
    only true by convention (module docstring)."""

    summary: MemorySummary | None
    included_turns: tuple[SessionTurn, ...] = field(default_factory=tuple)  # chronological (oldest -> newest)
    omitted_turns: tuple[SessionTurn, ...] = field(default_factory=tuple)  # chronological; eligible for compaction (Task 6)
    token_count: int = 0  # actual tokens consumed by summary + included_turns, per estimate_tokens
    budget: MemoryBudget = DEFAULT_MEMORY_BUDGET

    @property
    def is_empty(self) -> bool:
        return self.summary is None and not self.included_turns


def build_memory_context(session: SessionState, *, budget: MemoryBudget = DEFAULT_MEMORY_BUDGET) -> MemoryContext:
    """Select a budget-bounded memory context from `session`. Never
    raises on a large session - excess turns are simply omitted, never
    passed through unbounded (Task 5's "do not pass unbounded raw history
    to the model")."""

    remaining = budget.max_memory_tokens

    included_summary: MemorySummary | None = None
    if session.summary is not None:
        summary_tokens = estimate_tokens(session.summary.text)
        if summary_tokens <= remaining:
            included_summary = session.summary
            remaining -= summary_tokens
        # else: omitted entirely - see module docstring's selection policy #2.

    selected: list[SessionTurn] = []
    omitted: list[SessionTurn] = []
    stopped = False
    for turn in reversed(session.recent_turns):  # newest first
        if stopped:
            omitted.append(turn)
            continue
        turn_tokens = estimate_tokens(turn.content)
        if len(selected) >= budget.max_recent_turns or turn_tokens > remaining:
            stopped = True
            omitted.append(turn)
            continue
        selected.append(turn)
        remaining -= turn_tokens

    selected.reverse()  # back to chronological order
    omitted.reverse()

    token_count = budget.max_memory_tokens - remaining

    return MemoryContext(
        summary=included_summary,
        included_turns=tuple(selected),
        omitted_turns=tuple(omitted),
        token_count=token_count,
        budget=budget,
    )
