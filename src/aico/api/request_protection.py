"""
Day 6 Task 4 — request protection (Content-Type validation, request-size
ceiling).

`RequestProtectionMiddleware` is a pure ASGI middleware, not a
`BaseHTTPMiddleware` - deliberately, so it never has to buffer/read the
request body itself just to police it (which would just move the "read
everything into memory first" problem rather than remove it). It rejects
on `Content-Type`/`Content-Length` headers ALONE, before Starlette routing,
before FastAPI dependency resolution, before the body is parsed - the
common cause of failure this avoids by construction is "request-size check
occurring after the expensive model call" (`api_contract_guidance.md` /
working rules): a request that fails either check here never reaches
`GroundedAnswerService`, or even `AskRequest` parsing.

`MAX_REQUEST_BODY_BYTES` is the one named, documented, configurable
ceiling (Task 4: "Define a named/configurable request-size ceiling").

Known lab-scope limitation: this checks the *declared* `Content-Length`
header, not bytes actually streamed off the wire. A client that lies about
`Content-Length` (or omits it and streams via chunked transfer-encoding)
is not caught here - in production that residual gap is closed by the
ASGI server / reverse proxy's own body-size limit (e.g. a load balancer or
Uvicorn `--limit-max-requests`-style setting), which is standard practice
layered defense, not something an application-level check alone is
expected to fully own. `identity_claim_cases.json`'s sibling pack,
`api_cases.json`, only exercises the declared-size case (API-003), which
this covers.
"""
from __future__ import annotations

from typing import Iterable

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from aico.api.errors import error_response, request_ids

ALLOWED_CONTENT_TYPE = "application/json"
MAX_REQUEST_BODY_BYTES = 32 * 1024  # 32 KiB - a grounded question is text, not a file upload.

_DEFAULT_PROTECTED_METHODS = ("POST", "PUT", "PATCH")


def _content_type_allowed(content_type: str, allowed: str) -> bool:
    # Accept an optional "; charset=..." (or other) parameter suffix -
    # `application/json; charset=utf-8` is still JSON. Case-insensitive
    # per RFC 9110.
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type == allowed.lower()


class RequestProtectionMiddleware:
    """Enforces Content-Type and a request-size ceiling on protected
    methods, ahead of routing. Must be registered so `CorrelationMiddleware`
    (Task 3) runs first (see `app.py`'s `add_middleware` ordering comment)
    - that is what lets a rejection here still carry request_id/
    correlation_id, exactly like a success response does."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int = MAX_REQUEST_BODY_BYTES,
        allowed_content_type: str = ALLOWED_CONTENT_TYPE,
        protected_methods: Iterable[str] = _DEFAULT_PROTECTED_METHODS,
    ) -> None:
        self._app = app
        self._max_body_bytes = max_body_bytes
        self._allowed_content_type = allowed_content_type
        self._protected_methods = set(protected_methods)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in self._protected_methods:
            await self._app(scope, receive, send)
            return

        # Header-only access - never touches the body, so the inner app
        # still receives an unconsumed `receive` channel either way.
        request = Request(scope, receive=receive)
        request_id, correlation_id = request_ids(request)

        content_type = request.headers.get("content-type", "")
        if not _content_type_allowed(content_type, self._allowed_content_type):
            response = error_response(
                status_code=415,
                error_code="unsupported_content_type",
                message=f"Content-Type must be {self._allowed_content_type!r}, got {content_type!r}",
                request_id=request_id,
                correlation_id=correlation_id,
            )
            await response(scope, receive, send)
            return

        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = None
            if declared_size is not None and declared_size > self._max_body_bytes:
                response = error_response(
                    status_code=413,
                    error_code="payload_too_large",
                    message=f"request body of {declared_size} bytes exceeds the {self._max_body_bytes}-byte limit",
                    request_id=request_id,
                    correlation_id=correlation_id,
                )
                await response(scope, receive, send)
                return

        await self._app(scope, receive, send)
