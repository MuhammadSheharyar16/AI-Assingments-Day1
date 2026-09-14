"""
Day 6 Task 5 — cancellation propagation.

`GroundedAnswerService.answer()` (Day 5) and `ModelGateway.chat()` (Day 3)
already accept an optional `CancellationToken` (model_gateway.py) that the
gateway checks before every call attempt and retry - Task 5 only needed a
way to *set* that token when the HTTP client actually goes away, and to
thread it from the HTTP layer down into the pipeline call (see
`prompt_builder.BuiltPrompt.to_chat_request`'s new `cancellation`
parameter and `GroundedAnswerService.answer`'s new `cancellation`
parameter). This module is that HTTP-layer half.

`run_cancellable` runs a blocking call (`GroundedAnswerService.answer` is
synchronous - Day 5 stays synchronous, per the working rule "keep Day 5 as
the internal RAG application flow") in the thread pool while concurrently
watching `Request.is_disconnected()` in the event loop. The moment a
disconnect is observed, the given `CancellationToken` is set - the
in-flight call in the thread pool does not stop instantly (Python cannot
force-interrupt a blocked thread), but any cancellation-aware work inside
it (the Model Gateway's retry loop, or a fake transport that polls the
token during its own "expensive work" - see
`tests/test_day06_cancellation.py`) observes it and stops on its own next
check, rather than continuing to a normal, fabricated-after-the-fact
completion.

Implementation note: this uses `anyio`'s own task group / cancel scope
(`anyio.create_task_group`), not a raw `asyncio.create_task` +
`task.cancel()`. Starlette's `Request.is_disconnected()` itself opens a
nested `anyio.CancelScope` internally; cancelling the watcher task via a
bare `asyncio.Task.cancel()` while it may be suspended inside that nested
scope produced an intermittent hang under load (observed as
`uv run pytest` occasionally never returning) - anyio's structured
concurrency (a task group whose scope is cancelled once the blocking call
finishes) uses the same cancellation bookkeeping Starlette's internals
already rely on, and does not exhibit that hang.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import anyio
from starlette.requests import Request

from aico.platform.model_gateway import CancellationToken

T = TypeVar("T")

# How often to poll for a client disconnect while the blocking pipeline
# call is in flight. Small enough that a disconnect is noticed quickly;
# large enough not to busy-loop.
DISCONNECT_POLL_SECONDS = 0.05


async def run_cancellable(request: Request, blocking_call: Callable[[CancellationToken], T]) -> T:
    """Run `blocking_call(cancellation)` in the thread pool while watching
    `request` for a client disconnect. `blocking_call` receives the
    `CancellationToken` it should thread down into the pipeline (e.g.
    `lambda token: service.answer(question, token)`).

    A disconnect sets the token but does not raise here or short-circuit
    the return value - what a cancelled `blocking_call` returns (Day 5's
    `GroundedAnswerService.answer` returns a `TypedFailure` when the
    gateway observes cancellation) is exactly what this returns. This
    function's only job is propagating the signal, never fabricating or
    swallowing an outcome.

    Day 12 Task 11: `blocking_call` raising on purpose (Gate-D's typed
    `GateDSafeFailureError`/`GateDRejectedError` -- `api/errors.py`'s
    `ApiError` family, meant to be caught by FastAPI's own registered
    `ApiError` exception handler) is a new scenario no earlier caller of
    this function ever exercised - every pre-Day-12 `blocking_call` only
    ever *returns* a typed result (`Blocked`/`Clarify`/`TypedFailure`/...),
    never raises. `anyio.create_task_group()` wraps whatever a task
    running inside it raises in a `BaseExceptionGroup`, even for a single
    exception - left alone, that would reach FastAPI as an opaque group
    `register_error_handlers`'s specific `ApiError`/`RequestValidationError`/
    `StarletteHTTPException` handlers never match, silently falling through
    to the generic 500 handler regardless of what `blocking_call` actually
    raised. Unwrapped below so a real `ApiError` subclass still reaches
    FastAPI as itself.
    """

    cancellation = CancellationToken()
    result: list[T] = []

    async def _run_blocking_call() -> None:
        result.append(await anyio.to_thread.run_sync(blocking_call, cancellation))

    async def _watch_for_disconnect() -> None:
        while True:
            if await request.is_disconnected():
                cancellation.cancel()
                return
            await anyio.sleep(DISCONNECT_POLL_SECONDS)

    try:
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(_watch_for_disconnect)
            await _run_blocking_call()
            # The blocking call is done - stop watching. Cancelling this
            # task group's own scope (rather than reaching for the watcher
            # task directly) is the anyio-idiomatic way to stop a sibling
            # task, and is what keeps this safe to mix with Starlette's own
            # anyio-based `is_disconnected()` internals (see module docstring).
            task_group.cancel_scope.cancel()
    except BaseExceptionGroup as exc_group:
        # Exactly one exception in practice - `_watch_for_disconnect` never
        # raises on its own (it only sets `cancellation`/returns), and its
        # own cancellation (from `cancel_scope.cancel()` above) is not
        # itself an exception anyio surfaces here. Re-raise that one
        # exception directly so callers/handlers see it as itself, never
        # as an opaque group; the `len() > 1` branch is an unreached
        # defensive fallback, not a case this function's own call sites
        # are known to produce today.
        if len(exc_group.exceptions) == 1:
            raise exc_group.exceptions[0] from None
        raise

    return result[0]
