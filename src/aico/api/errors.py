"""
Day 6 Task 4 — the shared error envelope every 4xx/5xx `/ask` response
uses (`api_contract_guidance.md` "Error behavior").

One public shape (`ErrorResponse`), one place that builds it
(`error_response`), and one registration point (`register_error_handlers`)
that wires every source of a failure - a typed `ApiError` this service
raises on purpose, a FastAPI/pydantic request-validation failure, a plain
Starlette HTTPException, and the last-resort catch-all for anything
unnormalized - onto that same shape. A caller can always read:

- `error_code` - a stable, machine-readable category (never changes
  meaning between releases the way a free-text message might).
- `message` - safe to display; never a stack trace, a secret, a raw
  prompt/model completion, or a provider's raw exception text (working
  rule / common cause of failure: "returning provider stack traces to
  callers").
- `request_id` / `correlation_id` - so a failure is traceable back to a
  specific request even though it carries no other identifying detail.

`ApiError` is the base every typed API failure this codebase raises
inherits from - `identity.py`'s `IdentityError` (Task 2) and
`request_protection.py`'s `UnsupportedContentTypeError`/
`PayloadTooLargeError` (Task 4) among them - so `register_error_handlers`
only needs one handler to cover all of them consistently, and a future
typed failure only needs to subclass `ApiError`, never invent its own
response shape.
"""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from aico.api.correlation import CORRELATION_ID_HEADER, REQUEST_ID_HEADER


class ErrorResponse(BaseModel):
    """The one public error envelope. `extra="forbid"` so this shape can
    never silently grow an undocumented field."""

    model_config = ConfigDict(extra="forbid")

    error_code: str = Field(description="Stable, machine-readable error category.")
    message: str = Field(description="Safe, human-readable explanation.")
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None


class ApiError(Exception):
    """Base class for every typed API failure this service raises on
    purpose. Subclasses set `status_code`/`error_code` as class
    attributes; `message` is always instance-specific and must already be
    safe to return - this class never sees, and therefore can never leak,
    a raw exception, secret, or prompt."""

    status_code: int = 500
    error_code: str = "internal_error"

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def request_ids(request: Request) -> tuple[Optional[str], Optional[str]]:
    """Read the request_id/correlation_id `CorrelationMiddleware` (Task 3)
    already decided for this request. `getattr` with a default because a
    failure can in principle occur before that middleware runs (e.g. it is
    misconfigured) - an error response must never itself raise."""

    return getattr(request.state, "request_id", None), getattr(request.state, "correlation_id", None)


def error_response(
    *,
    status_code: int,
    error_code: str,
    message: str,
    request_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> JSONResponse:
    """The one place an `ErrorResponse` becomes an actual HTTP response.
    Also echoes request_id/correlation_id as headers (Task 3 convention -
    every response, success or error, carries them both ways)."""

    body = ErrorResponse(
        error_code=error_code,
        message=message,
        request_id=request_id,
        correlation_id=correlation_id,
    )
    headers = {}
    if request_id:
        headers[REQUEST_ID_HEADER] = request_id
    if correlation_id:
        headers[CORRELATION_ID_HEADER] = correlation_id
    return JSONResponse(status_code=status_code, content=body.model_dump(), headers=headers)


def register_error_handlers(app: FastAPI) -> None:
    """Wire every failure source this service can produce onto the one
    shared envelope. Call once, from `app.py`, after the app is created."""

    @app.exception_handler(ApiError)
    def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        request_id, correlation_id = request_ids(request)
        return error_response(
            status_code=exc.status_code,
            error_code=exc.error_code,
            message=exc.message,
            request_id=request_id,
            correlation_id=correlation_id,
        )

    @app.exception_handler(RequestValidationError)
    def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # exc.errors() can carry the caller's actual submitted value in an
        # "input" field - never included below. Only the stable field path
        # + pydantic's own canned, generic message (e.g. "Extra inputs are
        # not permitted") are safe to return - never the value itself.
        field_paths = sorted(
            {".".join(str(part) for part in err["loc"] if part != "body") or "body" for err in exc.errors()}
        )
        message = f"request body failed validation: {', '.join(field_paths)}"
        request_id, correlation_id = request_ids(request)
        return error_response(
            status_code=422,
            error_code="invalid_request",
            message=message,
            request_id=request_id,
            correlation_id=correlation_id,
        )

    @app.exception_handler(StarletteHTTPException)
    def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        request_id, correlation_id = request_ids(request)
        return error_response(
            status_code=exc.status_code,
            error_code="http_error",
            message=str(exc.detail),
            request_id=request_id,
            correlation_id=correlation_id,
        )

    @app.exception_handler(Exception)
    def _handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
        # Last-resort safety net: an unnormalized exception never reaches
        # the caller as-is (no stack trace, no exception message, which
        # could easily contain a provider error, a file path, or worse -
        # working rule / common cause of failure list). Real diagnosis
        # happens server-side via structured logs (Task 7), not via what
        # this response contains.
        request_id, correlation_id = request_ids(request)
        return error_response(
            status_code=500,
            error_code="internal_error",
            message="an unexpected error occurred",
            request_id=request_id,
            correlation_id=correlation_id,
        )
