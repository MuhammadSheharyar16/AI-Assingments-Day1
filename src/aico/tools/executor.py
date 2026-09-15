"""
Day 13 Task 7 -- the controlled executor: the single pipeline entrypoint
that turns a typed `ToolExecutionRequest` into a typed `ToolExecutionResult`,
running every earlier task's boundary in the required order
(`Day 13 Task.pdf`, TASK 7):

    typed request
    -> registry           (Task 2 -- exact (tool_id, tool_version) lookup)
    -> policy              (Task 4 -- default-deny authorization)
    -> input schema validation  (Task 5)
    -> MCP Gateway          (Task 6 -- the one approved transport boundary)
    -> transport            (injected, never called directly by this module)
    -> output schema validation (Task 7/11 -- `validate_tool_output()`)
    -> typed result

`ToolExecutor.execute()` is the *only* method this class exposes, and the
*only* place any of the six stages above is ever invoked in sequence --
"Do not create a second raw helper that bypasses this sequence" (Task 7)
is read literally: there is no `execute_unchecked()`, no way to call the
gateway or transport through this module except by running every earlier
stage first, and no early-return path that skips a stage rather than
failing through it.

Every stage's failure short-circuits immediately into a typed `FAILURE`
`ToolExecutionResult` -- never an exception a caller has to catch, and
never a fall-through to the next stage. The category each stage's failure
is tagged with already uses Day 13's own normalized names (`Day 13
Task.pdf`, TASK 10's own list: `tool_not_found` / `tool_disabled` /
`version_not_found` / `policy_denied` / `input_invalid` / `timeout` /
`cancelled` / `transport_unavailable` / `transport_error` /
`output_invalid`) -- Task 10 is a dedicated later task to *prove and
extend* this taxonomy's coverage (retry exhaustion, additional edge
cases), not to invent it from nothing; `ToolExecutor` cannot produce a
well-typed result without it existing already, so it is defined here,
where the mapping from each stage's own outcome to one of these ten names
is decided:

    - registry: `ToolNotFoundError` -> `tool_not_found`;
      `ToolVersionNotFoundError` -> `version_not_found`.
    - policy: a `DENY` decision whose `reason_code == "tool_disabled"` ->
      `tool_disabled` (the tool's own registered status); every other
      `DENY` reason (`no_matching_rule`, `policy_rule_not_active`,
      `rule_denied`, `permission_denied`, `server_alias_not_allowed`,
      `risk_level_exceeds_policy`, `unsafe_retry_for_non_idempotent_tool`)
      -> `policy_denied`.
    - input schema validation: any `ToolSchemaValidationFailure` ->
      `input_invalid`.
    - MCP Gateway/transport: `ToolTransportFailureCategory` maps 1:1 onto
      its identically-named `ToolExecutionErrorCategory` member
      (`timeout`/`transport_unavailable`/`transport_error`/`cancelled`) --
      the same normalized value, one layer up.
    - output schema validation: any `ToolSchemaValidationFailure` ->
      `output_invalid`.

Day 13 Task 8 -- timeout and cancellation. Every gateway call now runs
through `_dispatch_with_timeout()`, the tool-boundary analog of
`aico.platform.model_gateway`'s own `_dispatch_with_cancellation()`
(reimplemented locally, not imported -- see `transport.py`'s module
docstring for why `aico.tools` never reaches into `aico.platform`):
`MCPGateway.execute()` runs on a daemon background thread while this
method polls, once per `cancellation_poll_seconds` tick, for whichever
happens first --

    - the tool's own bounded budget (`ToolDefinition.timeout_ms`,
      Task 1) elapses -> the *internal* cancellation token passed to the
      gateway/transport is cancelled (so a cooperative transport, e.g.
      `FakeToolTransport`'s `wait_until_cancelled` step, can actually stop
      on its own) and this method immediately returns a typed
      `ToolExecutionErrorCategory.TIMEOUT` failure -- "timeout enforced",
      "timeout normalized" (never a raw `TimeoutError`, always this one
      typed category);
    - the caller's own, externally supplied `cancellation` token is
      cancelled -> bridged into the same internal token (so it reaches the
      fake transport exactly the way `wait_until_cancelled` expects --
      "request cancellation reaches fake transport") and this method
      returns a typed `ToolExecutionErrorCategory.CANCELLED` failure;
    - the background call actually finishes -> its real
      `ToolTransportSuccess`/`ToolTransportFailure` result is returned, as
      today.

Either way, `execute()` never waits for the background thread past its
own deadline/cancellation instant -- the identical "the caller is never
left waiting on it" guarantee `_dispatch_with_cancellation()` gives.
Python cannot forcibly stop an arbitrary blocking call on another thread,
so a slow or non-cooperative transport (`transport.py`'s `DelayedStep`,
Task 8's own "use a slow fake transport" tool) keeps running to completion
in the background even after this method has already returned -- its
eventual result is written to a queue nothing is listening to anymore and
is simply discarded, which is exactly what proves "cancelled transport
does not later produce normal success": there is no code path by which a
late background result could still reach the caller once the deadline/
cancellation instant has already produced and returned a typed failure.

Task 9 (bounded, policy-safe retry) extends this same `execute()` method
in place -- today `_dispatch_with_timeout()` makes exactly one gateway
call per request; Task 9 adds a bounded retry loop around it for the one
tool/failure-category combination `RetryPolicy` (Task 1) actually permits,
not a second code path."""
from __future__ import annotations

import queue
import threading
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aico.tools.errors import ToolNotFoundError, ToolSchemaValidationFailure, ToolVersionNotFoundError
from aico.tools.mcp_gateway import MCPGateway, ToolTransportFailure, ToolTransportFailureCategory, ToolTransportSuccess
from aico.tools.models import ToolDefinition, ToolExecutionRequest
from aico.tools.policy import ToolExecutionPolicy, ToolExecutionPolicyDecision, ToolExecutionPolicyStatus
from aico.tools.registry import ToolRegistry
from aico.tools.schema_validator import validate_tool_input, validate_tool_output
from aico.tools.transport import ToolCancellationToken


class ToolExecutionStatus(str, Enum):
    """The two possible `ToolExecutor.execute()` outcomes. Deliberately
    not extensible at the type level -- a third status would need an
    engine change, never an ad hoc string."""

    SUCCESS = "success"
    FAILURE = "failure"


class ToolExecutionErrorCategory(str, Enum):
    """Day 13's normalized error taxonomy (`Day 13 Task.pdf`, TASK 10's
    own list, defined here out of necessity -- see module docstring). Every
    `FAILURE` `ToolExecutionResult` carries exactly one of these; there is
    no eleventh, ad hoc category a stage can invent."""

    TOOL_NOT_FOUND = "tool_not_found"
    TOOL_DISABLED = "tool_disabled"
    VERSION_NOT_FOUND = "version_not_found"
    POLICY_DENIED = "policy_denied"
    INPUT_INVALID = "input_invalid"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    TRANSPORT_UNAVAILABLE = "transport_unavailable"
    TRANSPORT_ERROR = "transport_error"
    OUTPUT_INVALID = "output_invalid"


class ToolExecutionResult(BaseModel):
    """The typed final result of one governed tool execution attempt --
    `Day 13 Task.pdf` page 5's own pipeline diagram: "... -> Output Schema
    Validation -> Typed ToolExecutionResult". `payload` is populated only
    for `SUCCESS` (the already output-schema-validated transport result);
    `error_category`/`error_message` only for `FAILURE` -- "nothing is
    granted on failure", the identical guarantee `GateBDecision`/
    `ToolExecutionPolicyDecision` give their own denied outcomes.
    `error_message` is always built from an already-sanitized upstream
    value (a policy `reason_code`, or a `ToolSchemaValidationFailure`'s/
    `ToolTransportFailure`'s own sanitized `message`) -- never raw
    arguments/transport payload content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ToolExecutionStatus
    tool_id: str = Field(min_length=1)
    tool_version: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    payload: dict[str, Any] | None = Field(
        default=None, description="The validated transport output. Populated only when status is SUCCESS."
    )
    error_category: ToolExecutionErrorCategory | None = Field(
        default=None, description="Populated only when status is FAILURE."
    )
    error_message: str | None = Field(default=None, description="Sanitized detail. Populated only when status is FAILURE.")

    @property
    def succeeded(self) -> bool:
        return self.status is ToolExecutionStatus.SUCCESS


class ToolExecutor:
    """The one controlled pipeline entrypoint -- see module docstring for
    the required stage order and what "no second raw helper" means here.
    Construct once against a loaded `ToolRegistry`/`ToolExecutionPolicy`
    and an `MCPGateway` wrapping an injected transport; reused for every
    request."""

    def __init__(
        self,
        registry: ToolRegistry,
        policy: ToolExecutionPolicy,
        gateway: MCPGateway,
        *,
        cancellation_poll_seconds: float = 0.02,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._gateway = gateway
        self._cancellation_poll_seconds = cancellation_poll_seconds

    def execute(
        self, request: ToolExecutionRequest, *, cancellation: ToolCancellationToken | None = None
    ) -> ToolExecutionResult:
        """Run the full, required-order pipeline for one request. Never
        raises for an ordinary outcome -- success and every failure
        category are both normal, typed `ToolExecutionResult` values."""

        # Stage 1 -- registry (Task 2). Missing/unknown tool/version fails
        # closed immediately; every later stage needs a resolved `tool`.
        try:
            tool = self._registry.get_tool(request.tool_id, request.tool_version)
        except ToolNotFoundError:
            return self._failure(
                request, category=ToolExecutionErrorCategory.TOOL_NOT_FOUND, message="unknown tool_id"
            )
        except ToolVersionNotFoundError:
            return self._failure(
                request, category=ToolExecutionErrorCategory.VERSION_NOT_FOUND, message="unknown tool_version"
            )

        # Stage 2 -- policy (Task 4). Default-deny; a disabled tool and
        # every other denial reason each normalize onto their own category.
        decision = self._policy.authorize(request, tool)
        if decision.decision is not ToolExecutionPolicyStatus.ALLOW:
            category = (
                ToolExecutionErrorCategory.TOOL_DISABLED
                if decision.reason_code == "tool_disabled"
                else ToolExecutionErrorCategory.POLICY_DENIED
            )
            return self._failure(request, tool=tool, category=category, message=decision.reason_code)

        # Stage 3 -- input schema validation (Task 5). Invalid input makes
        # zero transport calls -- the gateway is not reached at all below.
        validated_input = validate_tool_input(tool, request.arguments)
        if isinstance(validated_input, ToolSchemaValidationFailure):
            return self._failure(
                request,
                tool=tool,
                category=ToolExecutionErrorCategory.INPUT_INVALID,
                message=str(validated_input),
            )

        # Stage 4/5 -- MCP Gateway / transport (Task 6), bounded by the
        # tool's own timeout and interruptible by cancellation (Task 8).
        # The gateway itself enforces "registered, policy-approved,
        # input-schema-valid" as a precondition; every input it needs was
        # just produced above.
        transport_result = self._dispatch_with_timeout(
            tool=tool,
            request=request,
            validated_arguments=validated_input,
            policy_decision=decision,
            cancellation=cancellation,
        )
        if isinstance(transport_result, ToolTransportFailure):
            return self._failure(
                request,
                tool=tool,
                category=ToolExecutionErrorCategory(transport_result.category.value),
                message=transport_result.message,
            )

        # Stage 6 -- output schema validation (Task 7/11). External,
        # untrusted transport output is never returned as success unless
        # it validates.
        validated_output = validate_tool_output(tool, transport_result.payload)
        if isinstance(validated_output, ToolSchemaValidationFailure):
            return self._failure(
                request,
                tool=tool,
                category=ToolExecutionErrorCategory.OUTPUT_INVALID,
                message=str(validated_output),
            )

        # Stage 7 -- typed result.
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            tool_id=tool.tool_id,
            tool_version=tool.tool_version,
            request_id=request.request_id,
            correlation_id=request.correlation_id,
            payload=validated_output,
        )

    def _dispatch_with_timeout(
        self,
        *,
        tool: ToolDefinition,
        request: ToolExecutionRequest,
        validated_arguments: dict[str, Any],
        policy_decision: ToolExecutionPolicyDecision,
        cancellation: ToolCancellationToken | None,
    ) -> ToolTransportSuccess | ToolTransportFailure:
        """Run `MCPGateway.execute()` bounded by `tool.timeout_ms` and
        interruptible by `cancellation` -- see module docstring's "Day 13
        Task 8" paragraph for the full mechanism. `MCPGateway.execute()`
        itself never raises for an ordinary transport outcome (Task 6's
        own guarantee); the `except BaseException` branch below exists
        purely as a last-resort safety net for the background thread
        itself, not a path this pipeline is expected to exercise."""
        internal_token = ToolCancellationToken()
        outcome: queue.Queue = queue.Queue(maxsize=1)

        def _run() -> None:
            try:
                outcome.put(
                    (
                        "result",
                        self._gateway.execute(
                            tool=tool,
                            request=request,
                            validated_arguments=validated_arguments,
                            policy_decision=policy_decision,
                            cancellation=internal_token,
                        ),
                    )
                )
            except BaseException as exc:  # pragma: no cover -- defensive; see docstring
                outcome.put(("error", exc))

        threading.Thread(target=_run, daemon=True, name=f"tool-executor-{tool.tool_id}").start()

        deadline_seconds = tool.timeout_ms / 1000.0
        start = time.monotonic()
        while True:
            if cancellation is not None and cancellation.is_cancelled():
                internal_token.cancel()
                return ToolTransportFailure(
                    category=ToolTransportFailureCategory.CANCELLED,
                    message="execution was cancelled while in flight",
                )
            if time.monotonic() - start >= deadline_seconds:
                internal_token.cancel()
                return ToolTransportFailure(
                    category=ToolTransportFailureCategory.TIMEOUT,
                    message=f"tool call exceeded its {tool.timeout_ms}ms timeout budget",
                )
            try:
                kind, payload = outcome.get(timeout=self._cancellation_poll_seconds)
            except queue.Empty:
                continue
            if kind == "error":  # pragma: no cover -- defensive; see docstring
                raise payload
            return payload

    @staticmethod
    def _failure(
        request: ToolExecutionRequest,
        *,
        category: ToolExecutionErrorCategory,
        message: str,
        tool: ToolDefinition | None = None,
    ) -> ToolExecutionResult:
        """Every failure path funnels through here so "nothing is granted
        on failure" is enforced in exactly one place -- `payload` stays at
        its typed default (`None`) for every failure, never populated by
        accident of which stage rejected the request. Falls back to the
        request's own (unregistered, untrusted) `tool_id`/`tool_version`
        when no `tool` was ever resolved (the registry stage itself
        failed) -- still safe to echo back, an identifier, never payload
        content."""
        return ToolExecutionResult(
            status=ToolExecutionStatus.FAILURE,
            tool_id=tool.tool_id if tool is not None else request.tool_id,
            tool_version=tool.tool_version if tool is not None else request.tool_version,
            request_id=request.request_id,
            correlation_id=request.correlation_id,
            error_category=category,
            error_message=message,
        )
