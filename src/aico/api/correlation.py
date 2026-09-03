"""
Day 6 Task 3 — request and correlation IDs.

Every request carries two IDs:

- `request_id` - identifies this one HTTP call.
- `correlation_id` - identifies the logical operation across every stage
  it touches (API -> policy -> retrieval -> Model Gateway -> validation ->
  response composition). Several `request_id`s can in principle share one
  `correlation_id` (e.g. a retried client call); today this service
  treats them as a 1:1 pair per call, which is a stricter special case of
  that, not a violation of it.

Day 6 rule: "Correlation context may be generated. Authorization context
may not." (contrast with identity.py, which fails closed rather than ever
inventing a tenant/user.) Both IDs here are safe to invent - `_clean_id`
accepts a caller-supplied value (from `X-Request-ID`/`X-Correlation-ID`)
and `CorrelationMiddleware` generates a fresh UUID only when the header is
absent or blank, never rejecting the request either way.

`CorrelationMiddleware` is the single place these IDs are decided, for
every request (success or error path - including the 401 an identity
rejection produces and the 422 a contract violation produces, since it
wraps the whole ASGI call). It:

1. reads/generates both IDs and stores them on `request.state`
   (`get_request_context` - a FastAPI dependency handlers use) so the
   value a handler sees and the value already decided are always the same
   value, never independently recomputed;
2. also publishes them on module-level `contextvars`, so Tasks 7-9
   (structured logs, metrics, spans) can read the *current* request's
   correlation id from deep inside `answer_service`/`model_gateway`
   *without* every intermediate function accepting it as an explicit
   parameter - Day 5/Day 3 code stays unchanged, per the working rule
   "Keep Day 5 as the internal RAG application flow";
3. echoes both back as `X-Request-ID`/`X-Correlation-ID` response
   headers, in addition to `AskResponse.request_id`/`.correlation_id`
   (contracts.py) already carrying them in the body - both are
   "documented API contract/header behavior" per the assignment.
"""
from __future__ import annotations

import contextvars
import uuid
from dataclasses import dataclass
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"
CORRELATION_ID_HEADER = "X-Correlation-ID"

# Readable from anywhere in the current request's call stack (Tasks 7-9),
# without threading request_id/correlation_id through every function
# signature in aico.rag/aico.platform. Each request gets its own value -
# contextvars are per-async-task, so concurrent requests never see each
# other's IDs.
_request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("aico_request_id", default=None)
_correlation_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "aico_correlation_id", default=None
)


def new_id() -> str:
    return str(uuid.uuid4())


def current_request_id() -> Optional[str]:
    """The current request's request_id, or None outside a request
    (e.g. a unit test that never went through `CorrelationMiddleware`)."""
    return _request_id_var.get()


def current_correlation_id() -> Optional[str]:
    """The current request's correlation_id, or None outside a request."""
    return _correlation_id_var.get()


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    correlation_id: str


def _clean_id(value: Optional[str]) -> Optional[str]:
    """A caller-supplied header value is accepted as-is once non-blank -
    unlike identity.py's trusted claims, an ID is safe operational
    context, never authorization, so there is nothing here to verify."""
    if value is None:
        return None
    value = value.strip()
    return value or None


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Decides request_id/correlation_id once per request (generating
    whichever the caller did not supply) and makes that decision
    available to the rest of the request - `request.state`, contextvars,
    and the response headers - consistently."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = _clean_id(request.headers.get(REQUEST_ID_HEADER)) or new_id()
        correlation_id = _clean_id(request.headers.get(CORRELATION_ID_HEADER)) or new_id()

        request.state.request_id = request_id
        request.state.correlation_id = correlation_id

        request_token = _request_id_var.set(request_id)
        correlation_token = _correlation_id_var.set(correlation_id)
        try:
            response = await call_next(request)
        finally:
            _request_id_var.reset(request_token)
            _correlation_id_var.reset(correlation_token)

        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response


def get_request_context(request: Request) -> RequestContext:
    """FastAPI dependency: reads the IDs `CorrelationMiddleware` already
    decided for this request from `request.state` - never generates its
    own, so a route handler and the middleware can never disagree about
    what this request's IDs are."""

    return RequestContext(
        request_id=request.state.request_id,
        correlation_id=request.state.correlation_id,
    )
