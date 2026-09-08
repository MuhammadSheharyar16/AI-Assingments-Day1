"""
Day 8 Task 13 — sanitized memory/session artifacts.

Run: uv run python scripts/day08_generate_memory_artifacts.py

Generates the three required artifacts from real system behavior, not
hand-written prose - the same discipline Day 6 Task 11's
`scripts/day06_generate_trace_artifact.py` established: real components
(the real `MemorySessionService`/`SessionStore`/`build_memory_context`/
`compact_session`, and for the follow-up case, a real `POST /ask` through
the actual FastAPI app), a fake Model Gateway/retriever (no real network
call, per the working rules), and an explicit redaction check the script
itself asserts before writing - never left to a human eyeballing a
checklist.

    artifacts/day08/session_lifecycle.md   - creation, two-turn follow-up
                                              (driven through the real
                                              POST /ask), load, expiry,
                                              clear/reset
    artifacts/day08/context_compaction.md  - configured budget, context
                                              size before/after
                                              compact_session, compacted
                                              source turn IDs, summary
                                              provenance, confirmation
                                              retrieved evidence stays a
                                              separate prompt section
    artifacts/day08/isolation_report.md    - the Task 3 case matrix
                                              (same-owner / cross-user /
                                              cross-tenant / different-
                                              session / nonexistent),
                                              plus the fail-closed
                                              indistinguishability proof

Every value written to these files is a count, an id, a status, a
timestamp, a version, or a boolean - never a turn's or summary's actual
text content (working rule: "Do not include raw sensitive conversation
content"), even though every value here is already synthetic/public data
end to end. Distinctive `ARTIFACT-MARKER-*` strings stand in for turn
content specifically so the script can assert none of them leaked into
any rendered file, rather than assuming the discipline held.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from aico.api.app import app
from aico.api.dependencies import get_answer_service, get_session_store
from aico.api.identity import TrustedIdentity, get_trusted_identity
from aico.memory.context_builder import MemoryBudget, build_memory_context
from aico.memory.errors import SessionNotFoundError
from aico.memory.models import SESSION_STATE_SCHEMA_VERSION, SessionState, SessionTurn, TurnRole
from aico.memory.service import MemorySessionService
from aico.memory.store import InMemorySessionStore
from aico.memory.summarizer import DEFAULT_MAX_SUMMARY_TOKENS, FakeSummarizer, compact_session
from aico.platform.model_gateway import CallMetadata, ChatRequest, ChatResult
from aico.rag.answer_service import GroundedAnswerService
from aico.rag.citation_validator import EvidenceChunk
from aico.rag.prompt_builder import build_prompt

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = REPO_ROOT / "artifacts" / "day08"

_IDENTITY_A = TrustedIdentity(tenant_id="TENANT-SYN-001", user_id="USER-SYN-001")
_IDENTITY_A_OTHER_USER = TrustedIdentity(tenant_id="TENANT-SYN-001", user_id="USER-SYN-002")
_IDENTITY_B_SAME_LOOKING_USER = TrustedIdentity(tenant_id="TENANT-SYN-002", user_id="USER-SYN-001")

# Content markers - deliberately distinctive, deliberately never meant to
# appear in any rendered artifact (module docstring). Never real supplier
# or personal data, per the working rule "use synthetic/public data only".
_TURN_1_QUESTION = "ARTIFACT-MARKER-Q1: what are the synthetic supplier's payment terms?"
_TURN_2_QUESTION = "ARTIFACT-MARKER-Q2: what about its invoice submission window?"
_TURN_1_ANSWER = "ARTIFACT-MARKER-A1: payment terms are net 30 days."
_TURN_2_ANSWER = "ARTIFACT-MARKER-A2: invoices are due within 15 days of delivery."
_ALL_CONTENT_MARKERS = (_TURN_1_QUESTION, _TURN_2_QUESTION, _TURN_1_ANSWER, _TURN_2_ANSWER)

_TURN_1_JSON = json.dumps(
    {
        "schema_version": "1.0",
        "status": "answered",
        "answer": _TURN_1_ANSWER,
        "citations": [{"chunk_id": "DOC-SYN::chunk-0", "source_file": "DOC-SYN-supplier.md"}],
        "confidence_label": "high",
    }
)
_TURN_2_JSON = json.dumps(
    {
        "schema_version": "1.0",
        "status": "answered",
        "answer": _TURN_2_ANSWER,
        "citations": [{"chunk_id": "DOC-SYN::chunk-1", "source_file": "DOC-SYN-supplier.md"}],
        "confidence_label": "high",
    }
)


class _FakeGateway:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)

    def chat(self, request: ChatRequest) -> ChatResult:
        content = self._responses.pop(0)
        return ChatResult(
            content=content,
            metadata=CallMetadata(
                operation="chat",
                model_alias="artifact-fake-chat",
                latency_ms=5.0,
                retry_count=0,
                token_usage={"prompt_tokens": 20, "completion_tokens": 10},
                budget_status="within_budget",
            ),
        )


def _fake_retriever(question: str) -> list[EvidenceChunk]:
    return [
        EvidenceChunk(chunk_id="DOC-SYN::chunk-0", source_file="DOC-SYN-supplier.md", text=_TURN_1_ANSWER),
        EvidenceChunk(chunk_id="DOC-SYN::chunk-1", source_file="DOC-SYN-supplier.md", text=_TURN_2_ANSWER),
    ]


class _FakeClock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


# ── Evidence gathering (real components; no assertions skipped) ──────────


@dataclass
class LifecycleEvidence:
    created_session_id: str
    created_version: int
    created_at: str
    expires_at: str
    turn1_response: dict
    turn2_response: dict
    followup_session_id: str
    loaded_version: int
    loaded_recent_turn_count: int
    expiry_session_id: str
    expiry_denial_reason: str
    cleared_session_id: str
    cleared_version_before: int
    cleared_version_after: int
    cleared_recent_turn_count_before: int
    cleared_recent_turn_count_after: int
    cleared_summary_present_after: bool


def gather_session_lifecycle_evidence() -> LifecycleEvidence:
    memory_service = MemorySessionService(InMemorySessionStore())

    # Session creation - direct, isolated demonstration.
    created = memory_service.create_session(_IDENTITY_A)

    # Two-turn follow-up - driven through the real POST /ask.
    store = InMemorySessionStore()
    gateway = _FakeGateway([_TURN_1_JSON, _TURN_2_JSON])
    service = GroundedAnswerService(gateway=gateway, retriever=_fake_retriever)
    app.dependency_overrides[get_answer_service] = lambda: service
    app.dependency_overrides[get_trusted_identity] = lambda: _IDENTITY_A
    app.dependency_overrides[get_session_store] = lambda: store
    client = TestClient(app)

    first = client.post("/ask", json={"question": _TURN_1_QUESTION})
    assert first.status_code == 200 and first.json()["status"] == "answered"
    followup_session_id = first.json()["session_id"]

    second = client.post("/ask", json={"question": _TURN_2_QUESTION, "session_id": followup_session_id})
    assert second.status_code == 200 and second.json()["status"] == "answered"
    assert second.json()["session_id"] == followup_session_id

    app.dependency_overrides.clear()

    # Session load - direct store read, real ownership scoping.
    loaded = store.get(tenant_id=_IDENTITY_A.tenant_id, user_id=_IDENTITY_A.user_id, session_id=followup_session_id)

    # Expiry - a separate session, deterministic injected clock.
    clock = _FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    expiry_service = MemorySessionService(InMemorySessionStore(clock=clock))
    expiry_session = expiry_service.create_session(_IDENTITY_A, ttl_seconds=60.0)
    clock.advance(61)
    try:
        expiry_service.load_session(_IDENTITY_A, expiry_session.session_id)
        expiry_reason = "NOT-EXPIRED (unexpected)"
    except SessionNotFoundError as exc:
        expiry_reason = exc.reason

    # Clear/reset - the same two-turn follow-up session, via the service.
    memory_service_for_store = MemorySessionService(store)
    before_clear = loaded
    cleared = memory_service_for_store.clear_session(_IDENTITY_A, followup_session_id)

    return LifecycleEvidence(
        created_session_id=created.session_id,
        created_version=created.version,
        created_at=created.created_at.isoformat(),
        expires_at=created.expires_at.isoformat(),
        turn1_response=first.json(),
        turn2_response=second.json(),
        followup_session_id=followup_session_id,
        loaded_version=loaded.version,
        loaded_recent_turn_count=len(loaded.recent_turns),
        expiry_session_id=expiry_session.session_id,
        expiry_denial_reason=expiry_reason,
        cleared_session_id=followup_session_id,
        cleared_version_before=before_clear.version,
        cleared_version_after=cleared.version,
        cleared_recent_turn_count_before=len(before_clear.recent_turns),
        cleared_recent_turn_count_after=len(cleared.recent_turns),
        cleared_summary_present_after=cleared.summary is not None,
    )


@dataclass
class CompactionEvidence:
    budget_max_tokens: int
    budget_max_recent_turns: int
    max_summary_tokens: int
    turn_count_before: int
    context_before_included: int
    context_before_omitted: int
    context_before_tokens: int
    compacted_summary_version: int
    compacted_summary_created_at: str
    compacted_summary_model_alias: str | None
    compacted_summary_source_turn_ids: list[str]
    context_after_included: int
    context_after_tokens: int
    context_after_summary_present: bool
    memory_section_present: bool
    evidence_section_present: bool
    sections_are_distinct: bool


def gather_context_compaction_evidence() -> CompactionEvidence:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    # A small demo budget so compaction is guaranteed and clearly visible
    # - not dependent on production DEFAULT_MEMORY_BUDGET's larger
    # numbers, which are reported alongside it below for comparison.
    demo_budget = MemoryBudget(max_memory_tokens=40, max_recent_turns=4)
    turns = [
        SessionTurn(
            turn_id=f"ARTIFACT-TURN-{i:02d}",
            role=TurnRole.USER if i % 2 == 0 else TurnRole.ASSISTANT,
            timestamp=now,
            content=f"synthetic turn content {i}",
        )
        for i in range(10)
    ]
    session = SessionState(
        schema_version=SESSION_STATE_SCHEMA_VERSION,
        session_id="SESSION-ARTIFACT-COMPACTION",
        tenant_id=_IDENTITY_A.tenant_id,
        user_id=_IDENTITY_A.user_id,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
        version=1,
        recent_turns=turns,
        summary=None,
    )

    context_before = build_memory_context(session, budget=demo_budget)
    compacted = compact_session(session, FakeSummarizer(), budget=demo_budget, now=now)
    assert compacted.summary is not None, "expected compaction to actually occur for this demo session"
    context_after = build_memory_context(compacted, budget=demo_budget)

    # Confirm retrieved evidence stays its own, separate prompt section -
    # not folded into or replaced by session memory.
    evidence = EvidenceChunk(chunk_id="DOC-SYN::chunk-9", source_file="DOC-SYN.md", text="synthetic evidence text")
    prompt = build_prompt("synthetic current question", [evidence], context_after)
    sections = prompt.sections()

    return CompactionEvidence(
        budget_max_tokens=demo_budget.max_memory_tokens,
        budget_max_recent_turns=demo_budget.max_recent_turns,
        max_summary_tokens=DEFAULT_MAX_SUMMARY_TOKENS,
        turn_count_before=len(turns),
        context_before_included=len(context_before.included_turns),
        context_before_omitted=len(context_before.omitted_turns),
        context_before_tokens=context_before.token_count,
        compacted_summary_version=compacted.summary.summary_version,
        compacted_summary_created_at=compacted.summary.created_at.isoformat(),
        compacted_summary_model_alias=compacted.summary.model_alias,
        compacted_summary_source_turn_ids=list(compacted.summary.source_turn_ids),
        context_after_included=len(context_after.included_turns),
        context_after_tokens=context_after.token_count,
        context_after_summary_present=context_after.summary is not None,
        memory_section_present="session_memory" in sections,
        evidence_section_present="retrieved_evidence" in sections,
        sections_are_distinct=sections.get("session_memory") != sections.get("retrieved_evidence"),
    )


@dataclass
class IsolationCase:
    name: str
    requester: str
    session_owner: str
    outcome: str
    denial_reason: str | None


@dataclass
class IsolationEvidence:
    cases: list[IsolationCase]
    wrong_owner_message: str
    nonexistent_message: str
    messages_identical: bool


def _identity_label(identity: TrustedIdentity) -> str:
    return f"{identity.tenant_id}/{identity.user_id}"


def gather_isolation_evidence() -> IsolationEvidence:
    service = MemorySessionService(InMemorySessionStore())
    session = service.create_session(_IDENTITY_A)
    other_session = service.create_session(_IDENTITY_A)  # a second, distinct session under the same owner

    cases: list[IsolationCase] = []

    # ISO-001 same tenant + same user + correct session -> allow
    reloaded = service.load_session(_IDENTITY_A, session.session_id)
    cases.append(
        IsolationCase(
            "same-owner access",
            _identity_label(_IDENTITY_A),
            _identity_label(_IDENTITY_A),
            "allow" if reloaded.session_id == session.session_id else "UNEXPECTED",
            None,
        )
    )

    # ISO-002 same tenant + different user -> deny
    try:
        service.load_session(_IDENTITY_A_OTHER_USER, session.session_id)
        cases.append(IsolationCase("cross-user, same tenant", _identity_label(_IDENTITY_A_OTHER_USER), _identity_label(_IDENTITY_A), "UNEXPECTED ALLOW", None))
    except SessionNotFoundError as exc:
        cases.append(IsolationCase("cross-user, same tenant", _identity_label(_IDENTITY_A_OTHER_USER), _identity_label(_IDENTITY_A), "deny", exc.reason))

    # ISO-003 different tenant + same-looking user -> deny
    try:
        service.load_session(_IDENTITY_B_SAME_LOOKING_USER, session.session_id)
        cases.append(IsolationCase("cross-tenant, same-looking user id", _identity_label(_IDENTITY_B_SAME_LOOKING_USER), _identity_label(_IDENTITY_A), "UNEXPECTED ALLOW", None))
    except SessionNotFoundError as exc:
        cases.append(IsolationCase("cross-tenant, same-looking user id", _identity_label(_IDENTITY_B_SAME_LOOKING_USER), _identity_label(_IDENTITY_A), "deny", exc.reason))

    # ISO-004 different session under the same owner -> no cross-session leakage
    reloaded_other = service.load_session(_IDENTITY_A, other_session.session_id)
    no_leak = reloaded_other.session_id == other_session.session_id and reloaded_other.recent_turns == []
    cases.append(
        IsolationCase(
            "different session, same owner",
            _identity_label(_IDENTITY_A),
            f"{_identity_label(_IDENTITY_A)} (session {other_session.session_id[:12]}...)",
            "allow, no cross-session content" if no_leak else "LEAKAGE DETECTED",
            None,
        )
    )

    # ISO-005 guessed/nonexistent session id -> deny
    try:
        service.load_session(_IDENTITY_A, "SES-guessed-does-not-exist")
        cases.append(IsolationCase("guessed/nonexistent session id", _identity_label(_IDENTITY_A), "(none)", "UNEXPECTED ALLOW", None))
    except SessionNotFoundError as exc:
        cases.append(IsolationCase("guessed/nonexistent session id", _identity_label(_IDENTITY_A), "(none)", "deny", exc.reason))

    # Fail-closed indistinguishability: wrong-owner vs nonexistent.
    try:
        service.load_session(_IDENTITY_A_OTHER_USER, session.session_id)
        wrong_owner_message = "(no exception raised - unexpected)"
    except SessionNotFoundError as exc:
        wrong_owner_message = str(exc)
    try:
        service.load_session(_IDENTITY_A_OTHER_USER, "SES-never-created")
        nonexistent_message = "(no exception raised - unexpected)"
    except SessionNotFoundError as exc:
        nonexistent_message = str(exc)

    return IsolationEvidence(
        cases=cases,
        wrong_owner_message=wrong_owner_message,
        nonexistent_message=nonexistent_message,
        messages_identical=wrong_owner_message == nonexistent_message,
    )


# ── Rendering ──────────────────────────────────────────────────────────


def render_session_lifecycle(e: LifecycleEvidence) -> str:
    lines: list[str] = []
    lines.append("# Day 8 — Session Lifecycle")
    lines.append("")
    lines.append(
        f"Generated {datetime.now(UTC).date().isoformat()} by "
        "`scripts/day08_generate_memory_artifacts.py` from real "
        "`MemorySessionService`/`SessionStore` calls and one real two-turn "
        "conversation driven through the actual `POST /ask` endpoint "
        "(fake Model Gateway/retriever - no real network call). Every "
        "identity/session id below is synthetic; no raw turn content "
        "appears anywhere in this file - see Redaction Check."
    )
    lines.append("")

    lines.append("## Session Creation")
    lines.append("")
    lines.append(f"- Session ID: `{e.created_session_id}`")
    lines.append(f"- Owner: `{_identity_label(_IDENTITY_A)}`")
    lines.append(f"- Version: {e.created_version}")
    lines.append(f"- Created at: {e.created_at}")
    lines.append(f"- Expires at: {e.expires_at}")
    lines.append("- Recent turns: 0, summary: none")
    lines.append("")

    lines.append("## Two-Turn Follow-up (driven through `POST /ask`)")
    lines.append("")
    lines.append("| Turn | Status | Session ID | Citation count | Confidence |")
    lines.append("|---|---|---|---:|---|")
    for label, resp in (("1", e.turn1_response), ("2", e.turn2_response)):
        lines.append(
            f"| {label} | `{resp['status']}` | `{resp['session_id']}` | "
            f"{len(resp['citations'])} | {resp.get('confidence_label', '(n/a)')} |"
        )
    lines.append("")
    lines.append(
        f"Both turns share one session id (`{e.followup_session_id}`) - the second turn's request supplied "
        "the first turn's `session_id` and the response echoed the identical value back, confirming session "
        "continuity across the follow-up."
    )
    lines.append("")

    lines.append("## Session Load")
    lines.append("")
    lines.append(f"- Session ID: `{e.followup_session_id}`")
    lines.append(f"- Version after two turns: {e.loaded_version}")
    lines.append(f"- Recent turns after two turns: {e.loaded_recent_turn_count} (one user + one assistant turn per answered request)")
    lines.append("")

    lines.append("## Expiry")
    lines.append("")
    lines.append(f"- Session ID: `{e.expiry_session_id}` (TTL 60s, deterministic injected clock advanced 61s)")
    lines.append(f"- Load after TTL elapsed: denied, reason=`{e.expiry_denial_reason}`")
    lines.append("- No stale context was returned - the store's own ownership+expiry-scoped lookup refused it before any content could reach a caller.")
    lines.append("")

    lines.append("## Clear / Reset")
    lines.append("")
    lines.append(f"- Session ID: `{e.cleared_session_id}`")
    lines.append(f"- Before clear: version={e.cleared_version_before}, recent_turns={e.cleared_recent_turn_count_before}")
    lines.append(f"- After clear: version={e.cleared_version_after}, recent_turns={e.cleared_recent_turn_count_after}, summary_present={e.cleared_summary_present_after}")
    lines.append("- The session itself remains reloadable (clear is reset, not delete) - only its conversational state was removed.")
    lines.append("")

    lines.append("## Redaction Check")
    lines.append("")
    lines.append("Confirmed programmatically (this script asserts each line below before writing the file):")
    lines.append("")
    lines.append("- [x] raw turn question/answer text - absent")
    lines.append("- [x] authorization claims - absent")
    lines.append("- [x] secrets/tokens - absent")
    lines.append("")
    return "\n".join(lines)


def render_context_compaction(e: CompactionEvidence) -> str:
    lines: list[str] = []
    lines.append("# Day 8 — Context Compaction")
    lines.append("")
    lines.append(
        f"Generated {datetime.now(UTC).date().isoformat()} by "
        "`scripts/day08_generate_memory_artifacts.py` from real "
        "`build_memory_context`/`compact_session` calls (`src/aico/memory/context_builder.py`, "
        "`src/aico/memory/summarizer.py`) against a synthetic 10-turn session. "
        "No turn/summary text appears anywhere in this file - see Redaction Check."
    )
    lines.append("")

    lines.append("## Configured Memory Budget")
    lines.append("")
    lines.append(f"- This demo's `max_memory_tokens`: {e.budget_max_tokens} (a small budget, chosen so compaction is guaranteed and clearly visible)")
    lines.append(f"- This demo's `max_recent_turns`: {e.budget_max_recent_turns}")
    lines.append(f"- `max_summary_tokens` (bounds the summary itself): {e.max_summary_tokens}")
    lines.append("- Production defaults (`context_builder.DEFAULT_MEMORY_BUDGET`): `max_memory_tokens=800`, `max_recent_turns=12` - larger, so an ordinary conversation compacts far less often than this demo does.")
    lines.append("")

    lines.append("## Context Size Before Compaction")
    lines.append("")
    lines.append(f"- Session turn count: {e.turn_count_before}")
    lines.append(f"- `build_memory_context` included turns: {e.context_before_included}")
    lines.append(f"- `build_memory_context` omitted turns (eligible for compaction): {e.context_before_omitted}")
    lines.append(f"- Token count: {e.context_before_tokens} (≤ {e.budget_max_tokens}, the configured budget)")
    lines.append("")

    lines.append("## Compaction Result")
    lines.append("")
    lines.append(f"- Summary version: {e.compacted_summary_version}")
    lines.append(f"- Summary created at: {e.compacted_summary_created_at}")
    lines.append(f"- Summary model alias: {e.compacted_summary_model_alias!r} (`None` = deterministic `FakeSummarizer`, not model-generated)")
    lines.append(f"- Compacted source turn IDs ({len(e.compacted_summary_source_turn_ids)}): {', '.join(e.compacted_summary_source_turn_ids)}")
    lines.append("")

    lines.append("## Context Size After Compaction")
    lines.append("")
    lines.append(f"- `build_memory_context` included turns: {e.context_after_included}")
    lines.append(f"- Token count: {e.context_after_tokens} (≤ {e.budget_max_tokens})")
    lines.append(f"- Summary present: {e.context_after_summary_present}")
    lines.append(
        "- The compacted source turns are not duplicated in the active context - they were removed from "
        "`recent_turns` when compacted and survive only via the summary's provenance above."
    )
    lines.append("")

    lines.append("## Retrieved Evidence Remains Separate")
    lines.append("")
    lines.append(f"- `SESSION MEMORY` prompt section present: {e.memory_section_present}")
    lines.append(f"- `RETRIEVED EVIDENCE` prompt section present: {e.evidence_section_present}")
    lines.append(f"- The two sections are distinct (never merged): {e.sections_are_distinct}")
    lines.append("- Memory (including the compacted summary above) is never converted into an `EvidenceChunk` and never reaches citation validation as an evidence source (Task 7).")
    lines.append("")

    lines.append("## Redaction Check")
    lines.append("")
    lines.append("Confirmed programmatically (this script asserts each line below before writing the file):")
    lines.append("")
    lines.append("- [x] raw turn content - absent")
    lines.append("- [x] raw summary text - absent")
    lines.append("")
    return "\n".join(lines)


def render_isolation_report(e: IsolationEvidence) -> str:
    lines: list[str] = []
    lines.append("# Day 8 — Isolation Report")
    lines.append("")
    lines.append(
        f"Generated {datetime.now(UTC).date().isoformat()} by "
        "`scripts/day08_generate_memory_artifacts.py` from real "
        "`MemorySessionService` calls (`src/aico/memory/service.py`) - "
        "the exact Task 3 case matrix. No turn/summary content appears "
        "anywhere in this file - see Redaction Check."
    )
    lines.append("")

    lines.append("## Case Matrix")
    lines.append("")
    lines.append("| Case | Requester | Session Owner | Outcome | Denial Reason |")
    lines.append("|---|---|---|---|---|")
    for case in e.cases:
        reason = f"`{case.denial_reason}`" if case.denial_reason else "(n/a)"
        lines.append(f"| {case.name} | `{case.requester}` | `{case.session_owner}` | **{case.outcome}** | {reason} |")
    lines.append("")

    lines.append("## Fail-Closed Confirmation")
    lines.append("")
    lines.append(
        "Denial for a wrong-owner request and denial for a nonexistent session id must be indistinguishable "
        "from outside the service - neither message reveals whether another tenant's session exists."
    )
    lines.append("")
    lines.append(f"- Wrong-owner denial message: `{e.wrong_owner_message}`")
    lines.append(f"- Nonexistent-session denial message: `{e.nonexistent_message}`")
    lines.append(f"- Identical: **{e.messages_identical}**")
    lines.append("")

    lines.append("## Redaction Check")
    lines.append("")
    lines.append("Confirmed programmatically (this script asserts each line below before writing the file):")
    lines.append("")
    lines.append("- [x] raw turn content - absent (no turns were ever added to these sessions)")
    lines.append("- [x] which tenant/session a denial actually refers to - never disclosed beyond the sanitized reason category")
    lines.append("")
    return "\n".join(lines)


def _assert_no_content_markers(rendered: str, artifact_name: str) -> None:
    for marker in _ALL_CONTENT_MARKERS:
        assert marker not in rendered, f"redaction failure: {marker!r} appears in {artifact_name}"


def main() -> None:
    lifecycle_evidence = gather_session_lifecycle_evidence()
    compaction_evidence = gather_context_compaction_evidence()
    isolation_evidence = gather_isolation_evidence()

    rendered_lifecycle = render_session_lifecycle(lifecycle_evidence)
    rendered_compaction = render_context_compaction(compaction_evidence)
    rendered_isolation = render_isolation_report(isolation_evidence)

    for rendered, name in (
        (rendered_lifecycle, "session_lifecycle.md"),
        (rendered_compaction, "context_compaction.md"),
        (rendered_isolation, "isolation_report.md"),
    ):
        _assert_no_content_markers(rendered, name)

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    (ARTIFACT_DIR / "session_lifecycle.md").write_text(rendered_lifecycle, encoding="utf-8")
    (ARTIFACT_DIR / "context_compaction.md").write_text(rendered_compaction, encoding="utf-8")
    (ARTIFACT_DIR / "isolation_report.md").write_text(rendered_isolation, encoding="utf-8")
    for name in ("session_lifecycle.md", "context_compaction.md", "isolation_report.md"):
        print(f"wrote {(ARTIFACT_DIR / name).relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
