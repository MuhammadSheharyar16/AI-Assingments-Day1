"""
Day 6 Task 1 — the typed FastAPI service.

Exposes `POST /ask` over the Day 5 grounded RAG pipeline
(`aico.rag.answer_service.GroundedAnswerService`). This module never
reimplements retrieval/generation itself (working rule) - it only:

    1. accepts a public, typed `AskRequest` (contracts.py)
    2. calls `GroundedAnswerService.answer()` (Day 5, unchanged) via an
       injected dependency (dependencies.py, Task 10)
    3. maps the typed `AnswerResult` onto the public `AskResponse`
       (contracts.py's `ask_response_from_result`)

Task 2 IS wired: `get_trusted_identity` is a required dependency on
`POST /ask`, so an untrusted caller never reaches `GroundedAnswerService`
(see identity.py). `IdentityError` is an `ApiError` (errors.py), so
`register_error_handlers` covers it - no bespoke handler here.

Task 3 IS wired: `CorrelationMiddleware` (correlation.py) decides
request_id/correlation_id for every request - accepting a caller-supplied
`X-Request-ID`/`X-Correlation-ID`, generating whichever is absent - and
echoes both back as response headers on every response (success or
error), in addition to `AskResponse.request_id`/`.correlation_id`
already carrying them in the body.

Task 4 IS wired:
- `RequestProtectionMiddleware` (request_protection.py) rejects an
  unsupported Content-Type or an oversize body before routing/dependency
  resolution even runs - `GroundedAnswerService` never sees either case.
- `register_error_handlers` (errors.py) makes every 4xx/5xx `/ask`
  response - identity rejection, content-type/size rejection, a body that
  fails `AskRequest` validation, or an unexpected failure - use the one
  shared `ErrorResponse` envelope.

Middleware order matters here and is deliberately NOT the order these two
`add_middleware` calls appear in: Starlette's `add_middleware` makes the
most-recently-added middleware the OUTERMOST one (it runs first on the
way in). `RequestProtectionMiddleware` is added first so
`CorrelationMiddleware`, added second, ends up outermost - meaning
request_id/correlation_id are already decided by the time
`RequestProtectionMiddleware` runs, so even a Content-Type/size rejection
carries them (request_protection.py reads `request.state`, which
`CorrelationMiddleware` must have already populated).

Task 5 IS wired: `/ask` runs `GroundedAnswerService.answer()` through
`run_cancellable` (request_cancellation.py), which watches the HTTP
request for a client disconnect while the (synchronous) pipeline call
runs in the thread pool, and sets a `CancellationToken` the moment one is
observed - threaded all the way down to the Model Gateway call
(answer_service.py / prompt_builder.py), not just stopped at this
handler.

Swagger `/docs` "Authorize" button: `POST /ask` also declares
`identity.bearer_scheme` (an `HTTPBearer` security scheme, `auto_error=False`)
purely so the OpenAPI spec advertises a bearer-token field and `/docs` can
attach it for "Try it out" - it is documentation-only and never itself
verifies anything. `get_trusted_identity` (Task 2, above) remains the sole
enforcement point; this second dependency's value is discarded unread.

Task 6 IS wired: `health.router` adds `GET /health/live`, `GET /health/ready`
and `GET /health/dependencies` - three distinct endpoints, not one that
conflates liveness/readiness/dependency health (see health.py for the
documented degraded-mode policy). None of the three require trusted
identity or go through `RequestProtectionMiddleware`'s Content-Type/size
checks (they are unauthenticated GET probes, same as any orchestrator's
liveness/readiness probe).

Task 7 IS wired: `configure_logging()` (observability/logging.py) sets up
structured JSON logging once at import time. `CorrelationMiddleware`
already emits the request-level `stage="http_request"` start/end log
lines for every route; `/ask` additionally emits one `stage="ask_pipeline"`
log line once the typed result is known - `outcome` is the same
already-safe `AskStatus` value the caller receives in the body, and
`error_category` (when present) is `AskResponse.category` - never the
question, the answer, or any raw pipeline content.

Task 8 IS wired: `dependencies.get_answer_service` wraps the real
gateway/retriever in `MetricsGateway`/`MetricsRetriever`
(instrumentation.py) so gateway latency/tokens/retries and retrieval
latency are recorded without touching Day 5. `/ask` itself records the
end-to-end request outcome metric alongside its structured log line,
from the same already-safe `AskResponse.status`/`.category` fields.

Task 9 IS wired: `configure_tracing()` (observability/telemetry.py)
installs the OTel provider once at import time. `/ask` opens the root
`api.ask` span around the whole pipeline call - `answer_service.py`'s own
"policy"/"retrieval"/"model_gateway"/"validation"/"response_composition"
spans nest under it automatically (OTel's ambient-current-span context
propagates through `run_cancellable`'s thread-pool hop, the same way
`aico.api.correlation`'s contextvars already do), so every span in one
`/ask` call shares one trace_id - the "same correlation context [linking]
the operation" the assignment requires - without `answer_service.py`
needing to know anything about `request_id`/`correlation_id`, which are
instead set as attributes on this root span (the one place both this
module's HTTP-level IDs and the OTel trace they head are both available).

Day 8 Task 4 IS wired - session participation, around the still-unchanged
Day 5 pipeline call (working rule: "Do not reimplement RAG inside the
memory layer"):

    1. Session resolution happens BEFORE the pipeline runs, using
       `identity` (Day 6) and `get_memory_service` (Task 3's
       `MemorySessionService`): a supplied `request.session_id` is loaded
       - and, per Task 3, rejected with `SessionAccessError` (404) exactly
       the same way whether it never existed or belongs to someone else -
       or, when omitted, a new session is created for this identity. This
       is "Session Resolution" / "Load Bounded Session State" in the
       build_outcome flow diagram.
    2. `service.answer()` still runs unmodified as Day 5's actual answer
       path (working rule: "Do not reimplement RAG inside the memory
       layer") - this module has no retrieval/generation logic of its
       own. It is now handed the resolved session's bounded memory
       context as one additional, optional argument (Task 7, below) -
       "Build Memory Context" in the diagram.
    3. After the pipeline returns, the user's question (and the answer,
       only when `status="answered"`) become `SessionTurn`s appended to
       the session and saved - "Store Safe Turn Result" / "Update /
       Compact Session State". A question that was rejected by the input
       policy is still recorded, marked `blocked=True` (Task 10 groundwork
       - a blocked turn can be reloaded as history but a future context
       builder must never replay it as instruction); a question that was
       merely inconclusive (insufficient evidence / needs clarification)
       or that failed a pipeline stage produces no assistant turn - there
       is no cited, evidence-backed content to record for one.
       `AskResponse.session_id` always reflects the resolved session,
       even when this last save step is skipped.
    4. A concurrent write losing the race on this exact session does not
       fail the request - the caller's answer was already correctly,
       independently produced by Day 5 before this step ever runs, and
       Day 8's own rule is that memory is supplementary context, not the
       source of truth for an answer. `_record_turn` calls
       `memory_service.update_session` (Task 9), which retries the save
       with a fresh reload a bounded number of times before giving up -
       only exhausting that bound (rare, sustained contention) reaches
       the log-and-continue path below; the ordinary single-conflict case
       already recovered silently.

Day 8 Task 7 IS wired - session memory now genuinely participates in
answering, without ever becoming evidence:

    1. `build_memory_context(session)` (Task 5) turns the resolved
       session into a budget-bounded `MemoryContext` and is handed to
       `service.answer(..., memory_context=...)` as one extra, optional
       argument - Day 5's pipeline (policy/retrieval/citation/support
       validation) is otherwise byte-for-byte what it was on Day 5; only
       `prompt_builder.build_prompt` reads `memory_context`, to add its
       own separately-labelled SESSION MEMORY message (`prompt_builder.py`'s
       module docstring has the full boundary rationale). A brand-new
       session's context is always empty, so its very first request
       produces the identical three-message prompt Day 5 always built -
       this never changes behavior when there is no history to add.
    2. After a turn is stored, `compact_session` (Task 6) is run on the
       updated session before it is saved - "Update / Compact Session
       State" in the diagram. It is a safe no-op (returns the session
       unchanged) whenever nothing exceeds the budget yet, so this runs
       unconditionally on every turn rather than needing its own
       threshold check here. The summarizer used is `get_summarizer`'s
       default, `FakeSummarizer` - see `dependencies.py`'s module
       docstring for why compaction deliberately does not default to a
       real model call the way the main answer path does.

Day 8 Task 11 IS wired - sanitized session/memory telemetry, never raw
turn/summary content:

    1. Session resolution, memory-context building, and turn storage all
       moved INSIDE the `api.ask` span (they used to straddle it) - every
       memory operation for this request now shares its one trace_id,
       fulfilling "preserve Day 6 correlation context across memory
       operations" at the trace level, not only via the request_id/
       correlation_id strings every `log_event` call already carried.
    2. `session_flow.resolve_session` emits one `stage="session_lifecycle"`
       log line per outcome (created/loaded/denied) - a denial's
       `error_category` is `SessionNotFoundError.reason`
       ("not_found"/"expired"), Task 3's already-safe "isolation denial
       category", never which tenant/session was denied.
       `session_flow.record_turn` emits one more on save, carrying session
       version, recent-turn count, whether compaction actually occurred,
       and the resulting summary's source-turn count - counts and
       booleans only.
    3. `dependencies.get_memory_service` wraps the store in
       `MetricsSessionStore` (instrumentation.py), the same wrap-at-
       assembly pattern Task 8 already uses for gateway/retriever -
       session create/load/clear/delete/expire and isolation-denial
       counts are recorded there, at the one place that covers every
       caller of the store, not only `/ask`. Memory-context token/recent-
       turn counts and the compaction-occurred count are recorded here in
       `app.py` instead, where those values are already computed
       (Task 5/6) - `MetricsSessionStore` has no way to know them.

Day 9 Task 9 IS wired: `POST /ask/governed` (`control_plane.py`, included
below) runs the same trusted-identity -> session-resolution ->
`session_flow` turn-storage flow as `/ask` above, reused unmodified via
`session_flow.py` (extracted out of this module for exactly this reuse),
but routes the resolved question through `ControlPlaneAnswerService`
(Gate-A -> lane selector -> selected-lane behavior) instead of directly
through `GroundedAnswerService`. `/ask` itself is deliberately left
untouched - it keeps answering over the full Day 7 golden-eval corpus,
which Day 9's intentionally narrow synthetic ontology does not yet cover
(see `control_plane.py`'s module docstring for why routing `/ask` itself
through Gate-A today would silently fail Day 7's permanent regression
gate)."""
from __future__ import annotations

import time

from fastapi import Depends, FastAPI, Request, Security
from fastapi.security import HTTPAuthorizationCredentials
from opentelemetry import trace

from aico.api import control_plane, health
from aico.api.contracts import AskRequest, AskResponse, AskStatus, ask_response_from_result
from aico.api.correlation import CorrelationMiddleware, RequestContext, get_request_context
from aico.api.dependencies import get_answer_service, get_memory_service, get_summarizer
from aico.api.errors import register_error_handlers
from aico.api.identity import TrustedIdentity, bearer_scheme, get_trusted_identity
from aico.api.request_cancellation import run_cancellable
from aico.api.request_protection import RequestProtectionMiddleware
from aico.api.session_flow import record_turn, resolve_session
from aico.memory.context_builder import build_memory_context
from aico.memory.service import MemorySessionService
from aico.memory.summarizer import Summarizer
from aico.observability.logging import configure_logging, log_event
from aico.observability.metrics import record_memory_context, record_request_outcome
from aico.observability.telemetry import configure_tracing
from aico.rag.answer_service import GroundedAnswerService

configure_logging()
configure_tracing()

_tracer = trace.get_tracer(__name__)

app = FastAPI(
    title="AICO Grounded RAG API",
    version="1.0",
    description=(
        "Typed HTTP boundary over the Day 5 grounded retrieval-augmented "
        "answer pipeline. See api_contract_guidance.md for the contract "
        "boundary this service maintains."
    ),
)

# See the module docstring's "Middleware order matters" note - this order
# is required, not incidental.
app.add_middleware(RequestProtectionMiddleware)
app.add_middleware(CorrelationMiddleware)

register_error_handlers(app)
app.include_router(health.router)
# Day 9 Task 9 - `POST /ask/governed` (control_plane.py): Gate-A/lane
# selection in front of this same Day 5 pipeline. Kept as its own router,
# not folded into `/ask` itself - see control_plane.py's module docstring
# for why `/ask` stays on the direct Day 5 path.
app.include_router(control_plane.router)


@app.post(
    "/ask",
    response_model=AskResponse,
    summary="Ask a grounded question",
    tags=["ask"],
)
async def ask(
    request: AskRequest,
    http_request: Request,
    context: RequestContext = Depends(get_request_context),
    identity: TrustedIdentity = Depends(get_trusted_identity),
    service: GroundedAnswerService = Depends(get_answer_service),
    memory_service: MemorySessionService = Depends(get_memory_service),
    summarizer: Summarizer = Depends(get_summarizer),
    _bearer: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> AskResponse:
    # `_bearer` (identity.bearer_scheme) exists solely to make `/docs`
    # render an "Authorize" button - see module docstring. It is never
    # read; `get_trusted_identity` above already did the real
    # verification independently of this value.
    del _bearer

    start = time.monotonic()
    with _tracer.start_as_current_span("api.ask") as span:
        # The one place request_id/correlation_id (Task 3, HTTP-level IDs)
        # and the OTel trace they head both exist together - see module
        # docstring. Never the question/answer/evidence.
        span.set_attribute("request_id", context.request_id)
        span.set_attribute("correlation_id", context.correlation_id)

        # Session Resolution / Load Bounded Session State (Day 8 Task 4) -
        # inside this span, and every memory operation below stays inside
        # it too, so all of them share the request's one trace_id (Day 8
        # Task 11: "preserve Day 6 correlation context across memory
        # operations") - using only the trusted identity and the
        # caller-supplied (untrusted) session_id, never anything else
        # from the request body.
        session = resolve_session(
            identity, request.session_id, memory_service, request_id=context.request_id, correlation_id=context.correlation_id
        )
        span.set_attribute("session_id", session.session_id)
        span.set_attribute("session.version", session.version)

        # Build Memory Context (Day 8 Task 5/7) - bounded, budgeted, and
        # kept structurally separate from evidence: this is data, handed
        # to the pipeline as one extra argument, never something that
        # alters policy, retrieval or citation validation (see module
        # docstring / Task 7). Task 11's "memory token count"/"recent-turn
        # count" telemetry - counts only, never the memory text itself.
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

        # Day 5's pipeline - unmodified except for the one additional,
        # optional memory_context argument (Task 7; see module docstring).
        result = await run_cancellable(
            http_request, lambda token: service.answer(request.question, token, memory_context=memory_context)
        )
        response = ask_response_from_result(
            result,
            request_id=context.request_id,
            correlation_id=context.correlation_id,
            session_id=session.session_id,
        )
        span.set_attribute("response.status", response.status.value)
        if response.category:
            span.set_attribute("response.category", response.category)

        # response.status/.category are already the public, pre-sanitized
        # AskResponse fields (contracts.py) - never the question, the
        # answer text, or retrieved evidence.
        latency_ms = (time.monotonic() - start) * 1000
        log_event(
            request_id=context.request_id,
            correlation_id=context.correlation_id,
            stage="ask_pipeline",
            outcome=response.status.value,
            error_category=response.category,
            latency_ms=latency_ms,
            session_id=session.session_id,
        )
        record_request_outcome(response.status.value, response.category)

        # Store Safe Turn Result / Update Session State (Day 8 Task 4) -
        # after the response is fully built, never blocking the caller's
        # answer on a lost concurrent write (see record_turn's
        # docstring). Still inside this span - Task 11.
        record_turn(
            identity,
            session,
            question=request.question,
            blocked=response.status is AskStatus.BLOCKED,
            answer_text=response.answer if response.status is AskStatus.ANSWERED else None,
            memory_service=memory_service,
            summarizer=summarizer,
            request_id=context.request_id,
            correlation_id=context.correlation_id,
        )

    return response
