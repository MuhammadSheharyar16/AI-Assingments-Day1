"""
Day 6 Task 1 — public HTTP contracts for the API.

These Pydantic models are the ONLY shape the HTTP boundary exposes. They
are deliberately separate from `aico.rag.answer_service`'s internal result
types (`GroundedAnswer` / `InsufficientEvidence` / `Clarify` / `Blocked` /
`TypedFailure`) and from `aico.contracts.models` (Day 4's model-facing
`CitedAnswer`/`Citation`) - per `api_contract_guidance.md`:

    HTTP API Contract
            v mapping
    Internal RAG / Domain Contract

A change to the internal dataclasses in `answer_service.py` does not
automatically redefine what `/ask` returns over HTTP - it only changes
what `ask_response_from_result` below has to map from. That mapping
function is the one place the two shapes are allowed to know about each
other.

Field names are this project's implementation choice (contract_guidance.md
"Exact field names may vary if clearly documented"):

- `request_id` / `correlation_id` - Task 3 metadata; present here (Task 1
  minimum) but populated with real header-aware logic once Task 3 lands.
- `status` mirrors which of the five Day 5 result paths produced the
  answer, using API-stable string values rather than exposing the
  dataclass type name.
- `category` carries the policy/failure category for every non-answered
  status (Blocked/Clarify/TypedFailure); `message` carries a safe,
  human-readable explanation. Neither ever receives a raw provider
  exception or stack trace (Task 4 owns enforcing that generally; this
  module already never has access to one - `answer_service.py` normalizes
  failures before they reach here).
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from aico.rag.answer_service import (
    AnswerResult,
    Blocked,
    Clarify,
    GroundedAnswer,
    InsufficientEvidence,
    TypedFailure,
)

# ── Request ──────────────────────────────────────────────────────────────


class AskRequest(BaseModel):
    """Public request body for `POST /ask`. `extra="forbid"` so an
    unknown field (e.g. a caller trying to smuggle `tenant_id`/`user_id`
    into the body - Task 2) is a 422, not silently ignored."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        min_length=1,
        max_length=4000,
        description="The caller's natural-language question.",
        examples=["What payment terms are stated in the supplier policy?"],
    )


# ── Response ─────────────────────────────────────────────────────────────


class AskStatus(str, Enum):
    """API-stable status vocabulary. Maps 1:1 to `answer_service.AnswerResult`'s
    five typed result paths, but as strings the OpenAPI contract owns -
    independent of the internal dataclass names."""

    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CLARIFY = "clarify"
    BLOCKED = "blocked"
    FAILED = "failed"


class CitationOut(BaseModel):
    """Public citation shape. Structurally similar to Day 4's `Citation`
    today, but declared independently - Day 4's contract is free to change
    without silently changing this HTTP response.

    `source_file` is optional: `GroundedAnswer.citation_ids` (answer_service.py)
    carries chunk IDs only, not their source document, so this field is
    left unset rather than fabricated from the chunk ID. A future
    enrichment (carrying source_file alongside citation_ids through Day 5)
    can populate it without changing this contract's shape."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    source_file: Optional[str] = None


class AskResponse(BaseModel):
    """Public response body for `POST /ask`. Every field is safe to return
    to any authenticated caller - no raw prompt, no retrieved evidence
    text, no provider exception detail (see module docstring)."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(description="Server-generated or caller-supplied request identifier.")
    correlation_id: str = Field(description="Correlation identifier shared with logs/spans for this operation.")
    status: AskStatus
    answer: Optional[str] = Field(default=None, description="The answer text, present only when status=answered.")
    citations: list[CitationOut] = Field(default_factory=list)
    confidence_label: Optional[str] = None
    category: Optional[str] = Field(
        default=None, description="Stable policy/failure category for a non-answered status."
    )
    message: Optional[str] = Field(default=None, description="Safe, human-readable explanation.")


def ask_response_from_result(
    result: AnswerResult,
    *,
    request_id: str,
    correlation_id: str,
) -> AskResponse:
    """The one place that maps a Day 5 `AnswerResult` onto the public
    `AskResponse` contract. Never passes an internal dataclass instance,
    a raw exception message, or provider content through untouched -
    every field written here is one this module already knows is safe to
    expose."""

    if isinstance(result, GroundedAnswer):
        return AskResponse(
            request_id=request_id,
            correlation_id=correlation_id,
            status=AskStatus.ANSWERED,
            answer=result.answer,
            citations=[CitationOut(chunk_id=cid) for cid in result.citation_ids],
            confidence_label=result.confidence_label,
        )

    if isinstance(result, InsufficientEvidence):
        return AskResponse(
            request_id=request_id,
            correlation_id=correlation_id,
            status=AskStatus.INSUFFICIENT_EVIDENCE,
            answer=result.explanation,
        )

    if isinstance(result, Clarify):
        return AskResponse(
            request_id=request_id,
            correlation_id=correlation_id,
            status=AskStatus.CLARIFY,
            category=result.category,
            message=result.reason,
        )

    if isinstance(result, Blocked):
        return AskResponse(
            request_id=request_id,
            correlation_id=correlation_id,
            status=AskStatus.BLOCKED,
            category=result.category,
            message=result.reason,
        )

    if isinstance(result, TypedFailure):
        return AskResponse(
            request_id=request_id,
            correlation_id=correlation_id,
            status=AskStatus.FAILED,
            category=result.category,
            message=f"{result.stage} stage failed: {result.category}",
        )

    raise TypeError(f"unhandled AnswerResult variant: {type(result).__name__}")  # pragma: no cover - exhaustive
