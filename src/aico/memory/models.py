"""
Day 8 — typed session-memory contract (Task 1).

`src/aico/memory/` is the Day 8 memory boundary (`structure_rules`,
Day 8 pack). This module is its single source of truth for what a
session-memory record looks like — the store (Task 2), context builder
(Task 5), summarizer (Task 6) and API integration (Task 4) all read and
write these types rather than a hand-rolled dict, so the shape session
memory takes cannot silently drift between them. Built from
`data/day08_pack/memory_contract_guidance.md`, which recommends this exact
field set without prescribing class names or a database schema.

Three contracts:
- `SessionTurn` — one stored conversational turn.
- `MemorySummary` — a bounded compaction of older turns (Task 6),
  carrying provenance back to the turns it replaces.
- `SessionState` — the full session record: ownership, lifecycle
  timestamps, concurrency version, recent turns and an optional summary.

Every model here sets `extra="forbid"`, matching the Day 4 contract
convention (`contracts/models.py`): an unknown field is a contract
violation, not something silently dropped.

What this module deliberately does NOT do, mirroring Day 4's own
separation of concerns: it does not enforce cross-field lifecycle rules
(e.g. "expires_at must be after created_at", "version increments by
exactly one", "turn_ids are unique within a session", "timestamps are
timezone-aware"). Those are store/service-level behaviors — Task 2
(create/get/save/expire), Task 8 (expiry/reset) and Task 9 (concurrency)
own them, against the real store and its deterministic in-memory fake.
Collapsing them into this module would blur the same shape-valid-vs-
business-valid distinction Day 4's `models.py` draws between contract and
semantic validation. This module only defines what a syntactically valid
session-memory record looks like.

Trust/safety rules encoded structurally rather than by validation:
- There is no field anywhere in this module for an authorization header,
  bearer token, API key or other credential. Session ownership is
  `tenant_id`/`user_id`, always supplied by the caller from a
  `TrustedIdentity` (Day 6, `api/identity.py`) — never accepted from a
  request-body memory field (Day 8 working rule). Nothing here embeds or
  wraps a `TrustedIdentity`; a session record only ever stores the two
  plain id strings it was scoped under.
- `SessionTurn.content` is documented as safe, already-sanitized text
  (or a reference to one) — never a place to persist raw provider
  request/response payloads, secrets or credentials (Task 1 rule).
- Nothing in `SessionTurn`/`MemorySummary` is ever treated as system
  instruction or as retrieval evidence by construction — that boundary
  is enforced downstream, by the context builder (Task 5/7) keeping
  memory in its own labelled section and the citation validator (Day 5)
  never accepting a memory turn/summary id as a citation source (Task 7).
  This module only makes the fields available; it does not — and cannot,
  as a pure data contract — perform that enforcement itself.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SESSION_STATE_SCHEMA_VERSION = "1.0"


class TurnRole(str, Enum):
    """Who produced a stored turn. There is deliberately no `"system"`
    role: memory never stores something claiming to be a system
    instruction (Task 10 — "blocked/injected text cannot become system
    instruction")."""

    USER = "user"
    ASSISTANT = "assistant"


class SessionTurn(BaseModel):
    """One stored conversational turn (`memory_contract_guidance.md`:
    turn_id / role / timestamp / content representation).

    Session memory is untrusted conversational context (Day 8 working
    rules, Task 10) — nothing about being persisted here makes `content`
    trusted. Downstream consumers (context builder, prompt construction)
    are responsible for labelling it as such; this type only carries it.
    """

    model_config = ConfigDict(extra="forbid")

    turn_id: str = Field(min_length=1, description="Non-empty, unique-within-its-session turn identifier.")
    role: TurnRole
    timestamp: datetime
    content: str = Field(
        min_length=1,
        description=(
            "Safe textual content for this turn: the user's message text, or the "
            "assistant's answer text / a reference to its typed result (e.g. a "
            "ResponseEnvelope request_id) — never secrets, authorization headers or "
            "raw credentials (Task 1 rule)."
        ),
    )
    blocked: bool = Field(
        default=False,
        description=(
            "True when this turn was rejected by the Day 5 input policy before an "
            "answer was produced. Recorded so a blocked turn can still be reloaded as "
            "conversational history, but a store/context-builder consumer must never "
            "replay a blocked turn as a trusted instruction (Task 10)."
        ),
    )


class MemorySummary(BaseModel):
    """A bounded compaction of older turns (Task 6), with provenance back
    to the turns it replaces (`memory_contract_guidance.md`: summary_version
    / source_turn_ids / created_at / model alias when model-generated).

    A summary is still untrusted conversational context, not authoritative
    evidence (Day 8 working rules, Task 7) — it is never accepted as a
    citation source, and summarizing must never invent a new fact,
    permission or instruction that was not present in its source turns
    (Task 6 summary rules)."""

    model_config = ConfigDict(extra="forbid")

    summary_version: int = Field(
        ge=1,
        description=(
            "Compaction generation number for this session's summary — 1 for the "
            "first compaction, incremented each time older turns are re-compacted. "
            "Distinct from `SESSION_STATE_SCHEMA_VERSION`: this counts compactions, "
            "it does not version this module's contract shape."
        ),
    )
    text: str = Field(min_length=1, description="Bounded summary text representing conversational context only.")
    source_turn_ids: list[str] = Field(
        min_length=1,
        description="Non-empty list of the turn_ids this summary was generated from — required provenance, never dropped.",
    )
    created_at: datetime
    model_alias: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Safe Model Gateway alias/version metadata that produced this summary, "
            "when model-generated (Task 6). None for the deterministic fake "
            "summarizer used in tests, which is not model-generated."
        ),
    )


class SessionState(BaseModel):
    """Typed session-memory contract (Task 1, `memory_contract_guidance.md`).

    Ownership is `tenant_id` + `user_id` + `session_id` (Day 8 working
    rules, Task 3): a session belongs to exactly that triple, and any
    store/service built on this type must resolve sessions only against a
    trusted identity — never from an untrusted request-body field.
    `version` supports optimistic-concurrency updates (Task 9)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = Field(description="Session-state contract version.")
    session_id: str = Field(min_length=1, description="Non-empty session identifier.")
    tenant_id: str = Field(min_length=1, description="Trusted tenant id this session is scoped to.")
    user_id: str = Field(min_length=1, description="Trusted user id this session is scoped to.")
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    version: int = Field(ge=1, description="Optimistic-concurrency version, incremented on every accepted write (Task 9).")
    recent_turns: list[SessionTurn] = Field(default_factory=list, description="Bounded recent-turn window (Task 5).")
    summary: MemorySummary | None = Field(default=None, description="Compacted older turns, when compaction has occurred (Task 6).")
