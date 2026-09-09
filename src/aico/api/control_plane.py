"""
Day 9 Task 9 — `POST /ask/governed`: the live HTTP boundary over the
Day 9 control plane (Gate-A -> lane selector -> selected-lane behavior,
`aico.rag.control_plane_answer_service.ControlPlaneAnswerService`).

Required order (Day 9 assignment), all now reachable through a real
request, not only through direct instantiation in tests/scripts:

    trusted identity -> session resolution -> Day 5 input policy
    -> Gate-A -> lane selector -> selected-lane behavior

This module is `app.py`'s peer for `/ask`, not a replacement for it -
same shape as `health.router`: its own `APIRouter`, included into `app`
once (`app.py`: `app.include_router(control_plane.router)`), so it
inherits the same global middleware (`RequestProtectionMiddleware`,
`CorrelationMiddleware`) and error handlers without any of its own.

Session resolution and turn storage are NOT reimplemented here -
`aico.api.session_flow.resolve_session`/`record_turn` (extracted out of
`app.py` for exactly this reuse) are called unmodified, so `/ask/governed`
gets the identical isolation, retry, and telemetry guarantees `/ask`
itself already has tests for (Day 8 Tasks 3/9/11) - a second, drifting
copy of that logic is exactly what Day 8's own regression tests exist to
catch, and this module adds none.

WHY THIS IS A SEPARATE ROUTE, NOT `/ask` ITSELF: Day 9's committed
ontology (`ontology/registry.v1.json`) is a small, deliberately SYNTHETIC
Mode-A registry (`ontology_requirements.md`: "does not contain supplier
facts") covering exactly three intents. `/ask`'s own corpus
(`data/documents/`) and Day 7's permanent golden-eval questions
(`evals/golden_v1.json`) are far broader real supplier-governance
questions this narrow registry was never built to recognize - routing
`/ask` itself through Gate-A first would classify most of them
`unsupported` and block them from ever reaching retrieval, silently
breaking Day 7's permanent regression gate (a working rule this build
must never violate). `/ask` therefore keeps answering exactly as it does
today, unmodified; `/ask/governed` is this Task's complete, independently
reachable integration, ready to become the canonical `/ask` path once a
future day's ontology actually covers the corpus it gates.

Task 11 observability: `ControlPlaneAnswerService.answer()` already opens
its own `"gate_a"`/`"lane_selection"` spans (see its module docstring) -
this handler's `"api.ask_governed"` root span (mirroring `/ask`'s
`"api.ask"`) is what makes those two share this request's one trace_id,
the same ambient-current-span mechanism `/ask` already relies on.
`request_id`/`correlation_id` are set as attributes on this root span
only - never passed into `aico.control`/`aico.rag`, matching those
modules' own layering rule (see `control_plane_answer_service.py`).
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials
from opentelemetry import trace

from aico.api.contracts import AskRequest
from aico.api.control_plane_contracts import GovernedAskResponse, governed_ask_response_from_result
from aico.api.correlation import RequestContext, get_request_context
from aico.api.dependencies import get_control_plane_answer_service, get_memory_service, get_summarizer
from aico.api.identity import TrustedIdentity, bearer_scheme, get_trusted_identity
from aico.api.request_cancellation import run_cancellable
from aico.api.session_flow import record_turn, resolve_session
from aico.memory.context_builder import build_memory_context
from aico.memory.service import MemorySessionService
from aico.memory.summarizer import Summarizer
from aico.observability.logging import log_event
from aico.observability.metrics import record_memory_context, record_request_outcome
from aico.rag.control_plane_answer_service import ControlPlaneAnswerService

_tracer = trace.get_tracer(__name__)

router = APIRouter()


@router.post(
    "/ask/governed",
    response_model=GovernedAskResponse,
    summary="Ask a question through the Day 9 governed control plane (Gate-A / lane selection)",
    tags=["ask"],
)
async def ask_governed(
    request: AskRequest,
    http_request: Request,
    context: RequestContext = Depends(get_request_context),
    identity: TrustedIdentity = Depends(get_trusted_identity),
    service: ControlPlaneAnswerService = Depends(get_control_plane_answer_service),
    memory_service: MemorySessionService = Depends(get_memory_service),
    summarizer: Summarizer = Depends(get_summarizer),
    _bearer: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> GovernedAskResponse:
    # `_bearer` exists solely so `/docs` renders an "Authorize" button -
    # see `app.py`'s module docstring. `get_trusted_identity` above is the
    # real enforcement point; this value is discarded unread.
    del _bearer

    start = time.monotonic()
    with _tracer.start_as_current_span("api.ask_governed") as span:
        span.set_attribute("request_id", context.request_id)
        span.set_attribute("correlation_id", context.correlation_id)

        # Session Resolution / Load Bounded Session State - identical,
        # reused logic to `/ask` (session_flow.py), inside this span so
        # every memory operation below shares this request's trace_id.
        session = resolve_session(
            identity, request.session_id, memory_service, request_id=context.request_id, correlation_id=context.correlation_id
        )
        span.set_attribute("session_id", session.session_id)
        span.set_attribute("session.version", session.version)

        # Build Memory Context - same Day 8 Task 5/7 helper `/ask` uses.
        # `service.answer()` forwards this to the wrapped RAG pipeline
        # only for the `rag` lane (see `control_plane_answer_service.py`) -
        # never to Gate-A/the lane selector, which see only the question
        # text.
        memory_context = build_memory_context(session)
        record_memory_context(token_count=memory_context.token_count, recent_turn_count=len(memory_context.included_turns))
        span.set_attribute("memory.token_count", memory_context.token_count)
        span.set_attribute("memory.recent_turn_count", len(memory_context.included_turns))
        span.set_attribute("memory.summary_present", memory_context.summary is not None)
        log_event(
            request_id=context.request_id,
            correlation_id=context.correlation_id,
            stage="memory_context",
            outcome="built",
            session_id=session.session_id,
            memory_token_count=memory_context.token_count,
            memory_recent_turn_count=len(memory_context.included_turns),
            memory_summary_present=memory_context.summary is not None,
        )

        # Day 9's pipeline: Day 5 input policy -> Gate-A -> lane selector
        # -> selected-lane behavior, all inside `ControlPlaneAnswerService.
        # answer()` (Task 9). `reference_context` (Task 8, session-memory-
        # assisted follow-up) is intentionally not threaded from stored
        # session turns here yet - AMB-003's own working example
        # (`tests/test_day09_memory_interaction.py`) already proves the
        # resolver's guarantees against `GateA`/`LaneSelector` directly;
        # deriving a `SessionReferenceContext` from arbitrary prior turns
        # automatically is future integration work, not required for this
        # route to be a real, reachable governed request path today.
        result = await run_cancellable(
            http_request, lambda token: service.answer(request.question, token, memory_context=memory_context)
        )
        response = governed_ask_response_from_result(
            result,
            request_id=context.request_id,
            correlation_id=context.correlation_id,
            session_id=session.session_id,
            ontology_version=service.registry.ontology_version,
        )
        span.set_attribute("response.status", response.status.value)
        if response.lane:
            span.set_attribute("response.lane", response.lane)
        if response.category:
            span.set_attribute("response.category", response.category)

        # response.status/.category/.lane are already the public,
        # pre-sanitized GovernedAskResponse fields - never the question,
        # the answer text, retrieved evidence, or the clarification
        # question's own content (that stays inside GateA's own "gate_a"
        # span attributes, already sanitized - see gate_a.py/
        # control_plane_answer_service.py).
        latency_ms = (time.monotonic() - start) * 1000
        log_event(
            request_id=context.request_id,
            correlation_id=context.correlation_id,
            stage="ask_governed_pipeline",
            outcome=response.status.value,
            lane=response.lane,
            error_category=response.category,
            latency_ms=latency_ms,
            session_id=session.session_id,
        )
        record_request_outcome(response.status.value, response.category)

        # Store Safe Turn Result / Update Session State - identical,
        # reused logic to `/ask` (session_flow.py). `blocked` covers both
        # Day 5's own early block and Gate-A's `block` lane (unsupported
        # or Gate-A-level blocked) - either way this turn must never be
        # replayed as instruction by a future context builder, exactly
        # like `/ask`'s own blocked turns.
        record_turn(
            identity,
            session,
            question=request.question,
            blocked=response.status.value == "blocked",
            answer_text=response.answer if response.status.value in ("answered", "safe_fast_path") else None,
            memory_service=memory_service,
            summarizer=summarizer,
            request_id=context.request_id,
            correlation_id=context.correlation_id,
        )

    return response


__all__ = ["ask_governed", "router"]
