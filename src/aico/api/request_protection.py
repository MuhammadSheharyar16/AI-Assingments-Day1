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

Two layers enforce it, not one:

1. The `Content-Length` header check below is a cheap fast-path: an
   honestly-declared oversize body is rejected before a single byte is
   read off the wire.
2. `_read_and_replay_within_limit` reads the body itself, off the real
   `receive` channel, counting bytes as they arrive and stopping the
   instant the running total exceeds `max_body_bytes` - never reading
   more than one message past the ceiling, so this still never buffers
   an actually-oversize body in full. This is what closes the gap layer 1
   cannot: a client that omits `Content-Length` entirely, lies about it
   (e.g. declares `1` while streaming far more), or uses chunked
   transfer-encoding (which carries no `Content-Length` at all) still
   cannot get an oversize body past this middleware.

   A body that stays within the limit is fully consumed by this point
   (it was at most `max_body_bytes`, the same ceiling the rest of the
   application already treats as small) - `_ReplayReceive` hands the
   buffered messages back to `self._app` exactly as `receive()` produced
   them, so FastAPI's own body reading downstream is unaffected and never
   knows this layer ran.

   An earlier version of this middleware instead wrapped `receive` and
   raised from inside it on overflow - reasonable in isolation, but
   FastAPI's own request-body-parsing wraps `receive()`/`request.json()`
   in a bare `except Exception` and converts *any* failure there into its
   own generic `HTTPException(400, "There was an error parsing the
   body")`, silently swallowing the specific `payload_too_large` 413
   this middleware means to produce. Enforcing the ceiling entirely
   within this middleware - before `self._app` is ever invoked - avoids
   depending on how a downstream framework happens to handle a mid-read
   exception.

Layer 2 does not remove the standard case for an ASGI server / reverse
proxy's own body-size limit as additional layered defense in production
(a proxy can refuse a connection before this application process ever
sees it at all), but this application no longer depends on that layer
alone to keep an oversize body away from `GroundedAnswerService`.
"""
from __future__ import annotations

from collections.abc import Iterable

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from aico.api.errors import error_response, request_ids

ALLOWED_CONTENT_TYPE = "application/json"
MAX_REQUEST_BODY_BYTES = 32 * 1024  # 32 KiB - a grounded question is text, not a file upload.

_DEFAULT_PROTECTED_METHODS = ("POST", "PUT", "PATCH")


class _ReplayReceive:
    """An ASGI `receive` callable that first hands back a fixed list of
    already-read messages, then falls through to the real `receive` for
    anything after - so a component that consumed some messages up front
    (here, to count body bytes) can still pass a channel downstream that
    behaves exactly like the original, unconsumed one."""

    def __init__(self, buffered: list[Message], receive: Receive) -> None:
        self._buffered = buffered
        self._receive = receive
        self._index = 0

    async def __call__(self) -> Message:
        if self._index < len(self._buffered):
            message = self._buffered[self._index]
            self._index += 1
            return message
        return await self._receive()


async def _read_and_replay_within_limit(receive: Receive, *, max_body_bytes: int) -> tuple[Receive | None, int]:
    """Consume `receive` until the body is complete (or the client
    disconnects), counting bytes as they arrive. Stops reading the
    instant the running total exceeds `max_body_bytes` - so an actually
    oversize body is never buffered past one message beyond the ceiling.

    Returns `(replay_receive, total_bytes)`. `replay_receive` is `None`
    when the ceiling was exceeded (nothing to replay - the caller rejects
    the request instead of forwarding it); otherwise it is a `receive`
    that reproduces every message already consumed here before falling
    through to the real channel.
    """

    buffered: list[Message] = []
    total = 0
    while True:
        message = await receive()
        buffered.append(message)
        if message["type"] != "http.request":
            # e.g. an early `http.disconnect` - nothing more to read;
            # forward exactly what happened, let downstream handle it.
            break
        total += len(message.get("body") or b"")
        if total > max_body_bytes:
            return None, total
        if not message.get("more_body", False):
            break

    return _ReplayReceive(buffered, receive), total


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
    correlation_id, exactly like a success response does.

    The size ceiling is enforced twice (module docstring): a fast
    `Content-Length`-based rejection here, before a single body byte is
    read, and `_read_and_replay_within_limit` counting bytes actually
    delivered for every protected-method request from that point on - so
    a missing, chunked, or lying `Content-Length` still cannot get an
    oversize body past this middleware."""

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

        # Layer 2 (module docstring): guard against a missing, chunked, or
        # understated/lying Content-Length by counting bytes actually
        # streamed off the wire, entirely within this middleware.
        replay_receive, total_bytes = await _read_and_replay_within_limit(
            receive, max_body_bytes=self._max_body_bytes
        )
        if replay_receive is None:
            response = error_response(
                status_code=413,
                error_code="payload_too_large",
                message=f"request body exceeds the {self._max_body_bytes}-byte limit",
                request_id=request_id,
                correlation_id=correlation_id,
            )
            await response(scope, receive, send)
            return

        await self._app(scope, replay_receive, send)
