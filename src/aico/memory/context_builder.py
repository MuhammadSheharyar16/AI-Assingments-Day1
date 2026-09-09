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

Day 9 Task 8 -- reference resolution (`SessionReferenceContext`,
`resolve_reference()`, bottom of this module). "Session Context" is its
own stage in the Day 9 pipeline, upstream of Gate-A
(`Trusted Request -> Session Context -> Mode-A Ontology Registry ->
Gate-A -> ...`) -- resolving a dangling reference like "What about its
invoice policy?" is memory's job, not Gate-A's (`aico.control.gate_a`
takes no session dependency at all, by construction). See
`resolve_reference()`'s own docstring for exactly how little it does, and
why that little is what makes "memory may resolve references but cannot
widen ontology/intent/lane policy" (Day 9 working rules) true.
"""
from __future__ import annotations

import re
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


# ── Task 8: reference resolution ────────────────────────────────────────

# The small, closed set of dangling-reference pronouns this resolves --
# exactly the shape the assignment's own example uses ("What about its
# invoice policy?"), not a general pronoun-resolution engine. Matched
# whole-word, case-insensitively, so "it"/"It"/"ITS" all resolve the same
# way and a word merely containing these letters ("This", "bit") never does.
_REFERENCE_PRONOUN_RE = re.compile(r"\b(?:it's|its|it)\b", re.IGNORECASE)


@dataclass(frozen=True)
class SessionReferenceContext:
    """The minimal, already-resolved signal `resolve_reference()` needs:
    the subject and governed intent of the most recent relevant prior turn
    -- exactly `ambiguity_cases.json` AMB-003's own `session_context` shape
    (`previous_subject`/`previous_intent`).

    Deriving this from a session's actual stored `recent_turns` (Task 1's
    `SessionTurn`) -- e.g. re-classifying the prior user turn through
    `GateA` and extracting the entity it was about -- is a later
    integration concern (Task 9), not Task 8's: this type is the boundary
    Task 8's required behaviors are proven against, matching exactly what
    the pack's own fixture supplies rather than a speculative NLP
    entity-extraction heuristic no fixture exercises.

    Deliberately NOT `SessionState`/`SessionTurn` themselves -- keeping
    this a small, explicit value, rather than handing `resolve_reference()`
    a whole session to go rummaging through, is what makes "memory cannot
    widen policy" checkable by inspection: there is no session data here
    for that function to reach for beyond these two plain strings, and (see
    `resolve_reference()`) it does not even read `previous_intent`."""

    previous_subject: str | None = None
    previous_intent: str | None = None


def resolve_reference(text: str, context: SessionReferenceContext) -> str:
    """Substitute a dangling `it`/`its` reference in `text` with
    `context.previous_subject`, when one is available -- "Session memory
    may resolve references but cannot widen ontology/intent/lane policy"
    (Day 9 working rules).

    Returns `text` unchanged when there is nothing to substitute
    (`previous_subject` is `None`/empty) or nothing to substitute it into
    (no matching pronoun in `text`).

    This is deliberately the ENTIRE extent of what memory does here: a
    plain string substitution, returning plain text. It never imports or
    touches `aico.control` (no `OntologyRegistry`, no `GateA`, no
    `LaneSelector`) -- so it cannot create an ontology concept, cannot
    pick an intent, and cannot choose a lane; there is no code path here
    that could. It never reads `context.previous_intent` at all -- the
    resolved text is only ever a *candidate* for the caller to run back
    through the real, unmodified `GateA.classify()`, which is what
    actually (and independently) decides whether the resolved text names
    a governed intent (`ambiguity_cases.json` AMB-003: "memory may resolve
    the reference but the final intent must exist in the registry"). If
    Gate-A would have classified the resolved text as `unsupported`
    without memory's help, substituting the subject back in does not
    change that unless the resolved text now genuinely, independently
    matches something governed -- memory supplies words, never a verdict.

    It also performs no trust upgrade on `previous_subject` itself: the
    resolved text is passed to `GateA.classify()` exactly like any other
    request text, still subject to Day 5's input policy (Gate-A's own
    Tier 0). If a prior turn's remembered subject happened to contain
    injected/malicious text, the resolved text containing it is still
    evaluated -- and still blocked -- like any other input; nothing here
    marks it as pre-trusted (Day 9 working rule: "cannot turn remembered
    injection text into policy")."""
    if not context.previous_subject:
        return text
    resolved, substitutions = _REFERENCE_PRONOUN_RE.subn(context.previous_subject, text)
    return resolved if substitutions else text
