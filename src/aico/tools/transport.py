"""
Day 13 Task 6 -- the transport seam: what an MCP transport adapter (real
or fake) must provide, and `FakeToolTransport`, the deterministic,
in-process double every mandatory Day 13 test is built against (`Day 13
Task.pdf`'s own build outcome: "deterministic fake transport"; working
rule: "A real remote MCP server is not required for mandatory
acceptance").

Mirrors `aico.platform.model_gateway`'s own `Transport`/`CancellationToken`
contract one boundary over, deliberately reimplemented here rather than
imported (Day 13 working rule: "Do not reuse Model Gateway retry semantics
blindly. Tool retry policy is its own boundary" -- the same reasoning
applies to the transport contract itself, not only retry; `aico.tools`
stays self-contained, never reaching across into `aico.platform`):

    - `ToolTransport` is a `Protocol` a real adapter and every fake
      satisfy structurally -- nothing here is provider/MCP-SDK-shaped.
    - A transport implementation raises `ToolTransportTimeoutError` /
      `ToolTransportUnavailableError` / `ToolTransportCancelledError`
      (`errors.py`) for a known, normalizable condition, or any other
      exception for an unnormalized one -- `MCPGateway.execute()`
      (`mcp_gateway.py`) is the one place any of these is ever caught and
      turned into a typed `ToolTransportFailure`, never a raw exception
      reaching a caller above the gateway.
    - `ToolCancellationToken` is the identical cooperative,
      `threading.Event`-based signal `aico.platform.model_gateway.
      CancellationToken` already establishes -- a caller holds it, passes
      it into a call, and `.cancel()`s it (from another thread, or on its
      own deadline) to ask an in-flight call to stop. What it still cannot
      do: force a blocking call to literally stop -- only cooperative,
      polling-aware code (this module's own `FakeToolTransport`
      `wait_until_cancelled` step included) actually observes it.

`FakeToolTransport` is configured with a fixed, ordered sequence of steps
consumed one per `execute()` call -- never randomness, never real
network/disk I/O -- covering every shape `transport_failure_cases.json`
names:

    - a `dict` step        -> returned as the call's success payload.
    - `"timeout"`           -> raises `ToolTransportTimeoutError`
                               (`TR13-001`).
    - `"transport_unavailable"` -> raises `ToolTransportUnavailableError`
                               (`TR13-002`/`TR13-003`).
    - `"transport_error"`  -> raises a plain, unnormalized `RuntimeError`
                               -- simulating exactly the "raw transport
                               exception" `MCPGateway.execute()`'s own
                               catch-all is responsible for normalizing
                               (Task 10's "Transport normalization" case).
    - `"wait_until_cancelled"` -> blocks (polling, never busy-waiting)
                               until the caller's own `ToolCancellationToken`
                               is set, then raises
                               `ToolTransportCancelledError` (`TR13-004`) --
                               requires a `cancellation` token to be passed
                               at all; a bounded internal safety wait
                               (`max_wait_seconds`) still raises rather
                               than hanging a test suite forever if nothing
                               ever cancels it.

`call_count` is exposed so a test can assert the critical invariant a
disabled tool or an invalid-input/invalid-policy request must satisfy:
"transport calls = 0" -- Task 6 makes this directly observable rather than
inferred from a mock library's own call-tracking."""
from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from aico.tools.errors import (
    ToolTransportCancelledError,
    ToolTransportTimeoutError,
    ToolTransportUnavailableError,
)


class ToolCancellationToken:
    """Cooperative cancellation signal -- see module docstring. Never
    forces an in-flight call to stop; only cooperative, polling-aware code
    (this module's `FakeToolTransport.execute()` included) observes it."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


class ToolTransportRequest(BaseModel):
    """The minimal, already-approved data a `ToolTransport` needs to
    perform one call -- built by `MCPGateway.execute()` from an
    already-registered/policy-approved/input-schema-valid
    `(ToolDefinition, ToolExecutionRequest)` pair, never constructed
    elsewhere. `effective_tenant_scope` is the trusted tenant context Task
    3's `resolve_effective_tenant_scope()` resolves, injected here
    separately from `arguments` (Day 13 working rule: "If tenant context
    is needed by transport, inject trusted effective scope separately from
    untrusted arguments") -- `arguments` itself is the already
    schema-validated dict Task 5's `validate_tool_input()` returned, never
    the raw, unvalidated `ToolExecutionRequest.arguments`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_id: str = Field(min_length=1)
    tool_version: str = Field(min_length=1)
    server_alias: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    effective_tenant_scope: tuple[str, ...] = Field(default_factory=tuple)
    timeout_ms: int = Field(gt=0)
    request_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)


class ToolTransport(Protocol):
    """Everything `MCPGateway` needs from a transport -- satisfied by a
    real MCP client adapter (the assignment's own optional stretch goal)
    and by `FakeToolTransport` alike. Returns the raw success payload
    (`dict[str, Any]`, untrusted/unvalidated -- Task 11's own job) or
    raises; see module docstring for which exceptions are known/
    normalizable versus caught as a generic, unnormalized failure."""

    def execute(
        self, request: ToolTransportRequest, *, cancellation: ToolCancellationToken | None = None
    ) -> dict[str, Any]: ...


_TIMEOUT = "timeout"
_TRANSPORT_UNAVAILABLE = "transport_unavailable"
_TRANSPORT_ERROR = "transport_error"
_WAIT_UNTIL_CANCELLED = "wait_until_cancelled"

FakeTransportStep = dict[str, Any] | str


class FakeToolTransport:
    """Deterministic, in-process `ToolTransport` double -- see module
    docstring for the exact step vocabulary. Construct with a fixed
    ordered sequence; each `execute()` call consumes exactly one step."""

    def __init__(
        self,
        steps: Sequence[FakeTransportStep] = (),
        *,
        cancellation_poll_seconds: float = 0.01,
        max_wait_seconds: float = 5.0,
    ) -> None:
        self._steps: list[FakeTransportStep] = list(steps)
        self._call_count = 0
        self._cancellation_poll_seconds = cancellation_poll_seconds
        self._max_wait_seconds = max_wait_seconds

    @property
    def call_count(self) -> int:
        """How many times `execute()` has actually been called -- the one
        number Day 13's own critical invariants ("invalid input -> transport
        calls = 0", "disabled tool -> transport calls = 0", "policy denial
        -> transport calls = 0") are checked against."""
        return self._call_count

    @property
    def remaining_steps(self) -> int:
        return len(self._steps)

    def execute(
        self, request: ToolTransportRequest, *, cancellation: ToolCancellationToken | None = None
    ) -> dict[str, Any]:
        self._call_count += 1
        if not self._steps:
            raise RuntimeError(
                f"FakeToolTransport has no more configured steps (call #{self._call_count} for "
                f"{request.tool_id!r}@{request.tool_version!r}) -- test misconfiguration"
            )
        step = self._steps.pop(0)

        if isinstance(step, dict):
            return step

        if step == _TIMEOUT:
            raise ToolTransportTimeoutError(f"fake transport: simulated timeout for {request.tool_id!r}")

        if step == _TRANSPORT_UNAVAILABLE:
            raise ToolTransportUnavailableError(
                f"fake transport: simulated transport_unavailable for {request.tool_id!r}"
            )

        if step == _TRANSPORT_ERROR:
            raise RuntimeError(f"fake transport: simulated unnormalized error for {request.tool_id!r}")

        if step == _WAIT_UNTIL_CANCELLED:
            if cancellation is None:
                raise RuntimeError(
                    "FakeToolTransport step 'wait_until_cancelled' requires a cancellation token to be passed"
                )
            waited = 0.0
            while not cancellation.is_cancelled():
                time.sleep(self._cancellation_poll_seconds)
                waited += self._cancellation_poll_seconds
                if waited >= self._max_wait_seconds:
                    raise RuntimeError(
                        "FakeToolTransport waited too long for cancellation -- test misconfiguration?"
                    )
            raise ToolTransportCancelledError(f"fake transport: cancelled while in flight for {request.tool_id!r}")

        raise ValueError(f"unrecognized fake transport step: {step!r}")
