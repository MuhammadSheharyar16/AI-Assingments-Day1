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
"""
from __future__ import annotations

import time

from fastapi import Depends, FastAPI, Request

from aico.api import health
from aico.api.contracts import AskRequest, AskResponse, ask_response_from_result
from aico.api.correlation import CorrelationMiddleware, RequestContext, get_request_context
from aico.api.dependencies import get_answer_service
from aico.api.errors import register_error_handlers
from aico.api.identity import TrustedIdentity, get_trusted_identity
from aico.api.request_cancellation import run_cancellable
from aico.api.request_protection import RequestProtectionMiddleware
from aico.observability.logging import configure_logging, log_event
from aico.observability.metrics import record_request_outcome
from aico.rag.answer_service import GroundedAnswerService

configure_logging()

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
) -> AskResponse:
    # `identity` is required for this call to reach here at all (an
    # untrusted caller was already rejected by the dependency above) but
    # is not yet threaded into the pipeline/response - Day 5's
    # GroundedAnswerService has no multi-tenant retrieval to scope by
    # tenant_id, and the response contract intentionally never echoes
    # authorization context back to the caller (Day 6 rule). Structured
    # logging (Task 7) and tracing (Task 9) are where `identity` becomes
    # observable - as sanitized tenant/user identifiers, never raw claims.
    del identity

    start = time.monotonic()
    result = await run_cancellable(http_request, lambda token: service.answer(request.question, token))
    response = ask_response_from_result(result, request_id=context.request_id, correlation_id=context.correlation_id)

    # response.status/.category are already the public, pre-sanitized
    # AskResponse fields (contracts.py) - never the question, the answer
    # text, or retrieved evidence.
    latency_ms = (time.monotonic() - start) * 1000
    log_event(
        request_id=context.request_id,
        correlation_id=context.correlation_id,
        stage="ask_pipeline",
        outcome=response.status.value,
        error_category=response.category,
        latency_ms=latency_ms,
    )
    record_request_outcome(response.status.value, response.category)

    return response
