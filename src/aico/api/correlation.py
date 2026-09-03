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

1. reads/generates both IDs and stores them on `scope["state"]`
   (`get_request_context` - a FastAPI dependency handlers use - reads
   this back via `request.state`) so the value a handler sees and the
   value already decided are always the same value, never independently
   recomputed;
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

This is a pure ASGI middleware, not a `starlette.middleware.base.
BaseHTTPMiddleware`, deliberately: `BaseHTTPMiddleware` wraps the request
it forwards downstream in its own `receive_or_disconnect`/`_CachedRequest.
wrapped_receive` indirection, which - combined with `Request.
is_disconnected()` deliberately running inside an already-cancelled
`anyio.CancelScope` so it never blocks - means a real client disconnect
can never actually be observed by code running *inside* the wrapped app
(a known Starlette `BaseHTTPMiddleware` limitation, not specific to this
codebase). Task 5's cancellation propagation (request_cancellation.py)
depends on `Request.is_disconnected()` working correctly from inside the
route handler, so this middleware must not be a `BaseHTTPMiddleware` -
see `tests/test_day06_cancellation.py`'s HTTP-level test, which is what
caught this.
"""
from __future__ import annotations

import contextvars
import uuid
from dataclasses import dataclass
from typing import Optional

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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


class CorrelationMiddleware:
    """Decides request_id/correlation_id once per request (generating
    whichever the caller did not supply) and makes that decision
    available to the rest of the request - `request.state`, contextvars,
    and the response headers - consistently."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        # Header-only access - never touches the body, so `receive` is
        # untouched and passed straight through to the inner app below.
        request = Request(scope, receive=receive)
        request_id = _clean_id(request.headers.get(REQUEST_ID_HEADER)) or new_id()
        correlation_id = _clean_id(request.headers.get(CORRELATION_ID_HEADER)) or new_id()

        # `scope["state"]` is the same dict every `Request(scope, ...)`
        # built later (routing, dependencies, RequestProtectionMiddleware)
        # reads via `.state` - see starlette.requests.Request.state - so a
        # plain dict write here is visible everywhere downstream.
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id
        scope["state"]["correlation_id"] = correlation_id

        request_token = _request_id_var.set(request_id)
        correlation_token = _correlation_id_var.set(correlation_id)

        async def send_with_correlation_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                # `__setitem__` (replace-if-present), not `.append()` - a
                # handler/error-response layer downstream (errors.py's
                # `error_response`) may already have set these from the
                # same `request.state` values; appending would duplicate
                # the header instead of agreeing with it.
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                headers[CORRELATION_ID_HEADER] = correlation_id
            await send(message)

        try:
            await self._app(scope, receive, send_with_correlation_headers)
        finally:
            _request_id_var.reset(request_token)
            _correlation_id_var.reset(correlation_token)


def get_request_context(request: Request) -> RequestContext:
    """FastAPI dependency: reads the IDs `CorrelationMiddleware` already
    decided for this request from `request.state` - never generates its
    own, so a route handler and the middleware can never disagree about
    what this request's IDs are."""

    return RequestContext(
        request_id=request.state.request_id,
        correlation_id=request.state.correlation_id,
    )
