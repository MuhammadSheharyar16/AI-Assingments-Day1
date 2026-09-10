"""
Day 9 Task 9 — public HTTP contract for `POST /ask/governed`
(`control_plane.py`).

Same layering rule `contracts.py` already documents for `/ask`
(`api_contract_guidance.md`): this is a contract the HTTP boundary owns,
kept separate from `aico.rag.control_plane_answer_service`'s internal
result types (`GateBlocked` / `GateClarify` / `ModeBSelected` /
`SafeFastPathAnswer`, plus Day 5's own five `AnswerResult` variants for the
`rag` lane) and from `aico.control.models`' decision types (`GateADecision`
/ `LaneDecision`). `governed_ask_response_from_result` below is the one
place that maps *all nine* possible pipeline outcomes onto this one public
shape - never a raw dataclass, an internal reason string invented here, or
provider content passed straight through.

`GovernedAskResponse` extends `AskResponse`'s own field set (Task 9's
answer-flow order is a strict superset of `/ask`'s: Gate-A/lane selection
run in front of the same Day 5 pipeline) with the governed-routing fields
the Day 9 assignment names for a decision: `lane`, `reason_code`,
`ontology_version`, plus `intent_id`/`domain`/`clarification_question`/
`candidate_intents` where the lane that was selected makes one of them
meaningful. `lane`/`ontology_version` are `None` specifically for the one
case where they are honestly unknowable: a Day 5 `blocked`/`clarify`
outcome that short-circuited *before* Gate-A ever ran (`lane_policy.md`
case; matches `artifacts/day09/lane_selection_report.md`'s "Gate-A did not
run" row) - never backfilled with a lane/version that was never actually
decided.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from aico.rag.answer_service import (
    AnswerResult,
    Blocked,
    Clarify,
    GroundedAnswer,
    InsufficientEvidence,
    TypedFailure,
)
from aico.rag.control_plane_answer_service import (
    ControlPlaneAnswerResult,
    GateBAuthorizationClarify,
    GateBDenied,
    GateBlocked,
    GateClarify,
    ModeBSelected,
    SafeFastPathAnswer,
)


class GovernedAskStatus(str, Enum):
    """API-stable status vocabulary for `/ask/governed`. The first five
    values are the exact same strings `AskStatus` (`contracts.py`) uses
    for `/ask`'s own five Day 5 result paths - a caller comparing the two
    endpoints sees identical wording for identical outcomes. The two
    Day-9-only values (`mode_b_selected`, `safe_fast_path`) name the two
    lane outcomes `/ask` never produces, since `/ask` never runs Gate-A/
    lane selection at all. The two Day-10-only values (`gate_b_denied`,
    `gate_b_clarify`) name Gate-B's own two non-allow outcomes (Day 10
    Task 13) - reachable only from a `ControlPlaneAnswerService` built
    with `policy_registry` set; `/ask/governed`'s own DI wiring does not
    activate Gate-B yet (see that module's own docstring), so these two
    values are not produced by the live route today, but this mapper
    still handles them completely for whenever a caller does."""

    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CLARIFY = "clarify"
    BLOCKED = "blocked"
    FAILED = "failed"
    MODE_B_SELECTED = "mode_b_selected"
    SAFE_FAST_PATH = "safe_fast_path"
    GATE_B_DENIED = "gate_b_denied"
    GATE_B_CLARIFY = "gate_b_clarify"


class GovernedCitationOut(BaseModel):
    """Same shape as `contracts.CitationOut` - declared independently for
    the same layering reason (`contracts.py`'s own docstring)."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    source_file: str | None = None


class GovernedAskResponse(BaseModel):
    """Public response body for `POST /ask/governed`. Every field is safe
    to return to any authenticated caller - no raw prompt, no retrieved
    evidence text, no provider exception detail (same rule `AskResponse`
    documents)."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(description="Server-generated or caller-supplied request identifier.")
    correlation_id: str = Field(description="Correlation identifier shared with logs/spans for this operation.")
    session_id: str = Field(
        description="The active session this turn was recorded under - see AskResponse.session_id."
    )
    status: GovernedAskStatus
    answer: str | None = Field(default=None, description="The answer text, present only when status=answered.")
    citations: list[GovernedCitationOut] = Field(default_factory=list)
    confidence_label: str | None = None
    category: str | None = Field(
        default=None, description="Stable policy/failure category for a non-answered status."
    )
    message: str | None = Field(default=None, description="Safe, human-readable explanation.")

    # ── Day 9 governed-routing fields ───────────────────────────────────
    lane: str | None = Field(
        default=None,
        description=(
            "The governed lane this request was routed to (rag/mode_b/clarify/block/"
            "safe_fast_path). None only when Day 5's own input policy short-circuited "
            "before Gate-A ever ran - see class docstring."
        ),
    )
    reason_code: str | None = Field(
        default=None, description="Gate-A's or the lane selector's sanitized reason code for this decision."
    )
    ontology_version: str | None = Field(
        default=None, description="The governed ontology version this decision was made against, when Gate-A ran."
    )
    intent_id: str | None = Field(default=None, description="The governed intent_id this request resolved to, when one exists.")
    domain: str | None = Field(default=None, description="The governed domain_id this request resolved to, when one exists.")
    clarification_question: str | None = Field(
        default=None, description="Gate-A's deterministically generated clarification question, when status=clarify."
    )
    candidate_intents: list[str] = Field(
        default_factory=list,
        description="Governed intent_ids this request plausibly matched, when status=clarify and there were specific candidates.",
    )

    # ── Day 10 Task 13 governed-authorization fields ────────────────────
    policy_version: str | None = Field(
        default=None,
        description=(
            "The governed Gate-B policy version this decision was made against, when Gate-B ran "
            "(status=gate_b_denied or gate_b_clarify) - Task 9's own 'response metadata identifies "
            "the disclosure profile/rule version' sanitized provenance, never raw policy internals."
        ),
    )
    rule_id: str | None = Field(
        default=None, description="Matched PermissionRule.rule_id, when Gate-B matched one at all."
    )


def governed_ask_response_from_result(
    result: ControlPlaneAnswerResult,
    *,
    request_id: str,
    correlation_id: str,
    session_id: str,
    ontology_version: str,
) -> GovernedAskResponse:
    """The one place that maps a `ControlPlaneAnswerService.answer()`
    result onto the public `/ask/governed` contract - all nine possible
    variants (Day 5's five, reached only via the `rag` lane, plus the four
    lane-native outcomes Gate-A/the lane selector can produce). `lane` is
    derived here from which variant `result` actually is, not read back off
    a separately threaded `LaneDecision` - a deterministic, exhaustive
    mapping, the same "one place both shapes are allowed to know about
    each other" rule `contracts.ask_response_from_result` already
    documents for `/ask`.

    `ontology_version` is the caller's currently-loaded registry version
    (`OntologyRegistry.ontology_version`) - needed here only for the three
    `rag`-lane variants below, none of which carry it themselves (Day 5's
    dataclasses predate Gate-A and have no reason to). Reaching the `rag`
    lane at all *only* happens after Gate-A matched a governed intent
    against this exact version, so stamping it here is reporting a fact
    that already held, never inventing one - the four lane-native variants
    below all carry their own `GateADecision`/`LaneDecision`-sourced
    `ontology_version` instead and ignore this parameter."""

    common = {"request_id": request_id, "correlation_id": correlation_id, "session_id": session_id}

    # ── rag lane: Day 5's own five result types, unmodified ─────────────
    if isinstance(result, GroundedAnswer):
        return GovernedAskResponse(
            **common,
            status=GovernedAskStatus.ANSWERED,
            answer=result.answer,
            citations=[GovernedCitationOut(chunk_id=cid) for cid in result.citation_ids],
            confidence_label=result.confidence_label,
            lane="rag",
            ontology_version=ontology_version,
        )

    if isinstance(result, InsufficientEvidence):
        return GovernedAskResponse(
            **common,
            status=GovernedAskStatus.INSUFFICIENT_EVIDENCE,
            answer=result.explanation,
            lane="rag",
            ontology_version=ontology_version,
        )

    if isinstance(result, TypedFailure):
        return GovernedAskResponse(
            **common,
            status=GovernedAskStatus.FAILED,
            category=result.category,
            message=f"{result.stage} stage failed: {result.category}",
            lane="rag",
            ontology_version=ontology_version,
        )

    # ── Day 5's own early short-circuit: Gate-A never ran ────────────────
    if isinstance(result, Blocked):
        return GovernedAskResponse(
            **common,
            status=GovernedAskStatus.BLOCKED,
            category=result.category,
            message=result.reason,
        )

    if isinstance(result, Clarify):
        return GovernedAskResponse(
            **common,
            status=GovernedAskStatus.CLARIFY,
            category=result.category,
            message=result.reason,
        )

    # ── Lane-native outcomes: Gate-A ran, the lane selector routed ──────
    if isinstance(result, GateBlocked):
        return GovernedAskResponse(
            **common,
            status=GovernedAskStatus.BLOCKED,
            reason_code=result.reason_code,
            ontology_version=result.ontology_version,
            lane="block",
        )

    if isinstance(result, GateClarify):
        return GovernedAskResponse(
            **common,
            status=GovernedAskStatus.CLARIFY,
            message=result.clarification_question,
            clarification_question=result.clarification_question,
            candidate_intents=list(result.candidate_intents),
            reason_code=result.reason_code,
            ontology_version=result.ontology_version,
            lane="clarify",
        )

    if isinstance(result, ModeBSelected):
        return GovernedAskResponse(
            **common,
            status=GovernedAskStatus.MODE_B_SELECTED,
            message=result.message,
            intent_id=result.intent_id,
            domain=result.domain,
            reason_code=result.reason_code,
            ontology_version=result.ontology_version,
            lane="mode_b",
        )

    if isinstance(result, SafeFastPathAnswer):
        return GovernedAskResponse(
            **common,
            status=GovernedAskStatus.SAFE_FAST_PATH,
            answer=result.answer,
            intent_id=result.intent_id,
            reason_code=result.reason_code,
            ontology_version=result.ontology_version,
            lane="safe_fast_path",
        )

    # ── Day 10 Task 13: Gate-B's own two non-allow outcomes ─────────────
    if isinstance(result, GateBDenied):
        return GovernedAskResponse(
            **common,
            status=GovernedAskStatus.GATE_B_DENIED,
            reason_code=result.reason_code,
            policy_version=result.policy_version,
            rule_id=result.rule_id,
        )

    if isinstance(result, GateBAuthorizationClarify):
        return GovernedAskResponse(
            **common,
            status=GovernedAskStatus.GATE_B_CLARIFY,
            reason_code=result.reason_code,
            policy_version=result.policy_version,
            rule_id=result.rule_id,
        )

    raise TypeError(f"unhandled ControlPlaneAnswerResult variant: {type(result).__name__}")  # pragma: no cover - exhaustive


# Re-exported so a test/caller can type-hint against the full accepted
# input union without importing `aico.rag` directly.
__all__ = [
    "AnswerResult",
    "GovernedAskResponse",
    "GovernedAskStatus",
    "GovernedCitationOut",
    "governed_ask_response_from_result",
]
