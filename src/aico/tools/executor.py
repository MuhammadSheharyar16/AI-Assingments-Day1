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

Day 13 Task 9 -- retry safety. `_dispatch_with_retry()` wraps
`_dispatch_with_timeout()` in a bounded loop, up to `tool.retry_policy.
max_attempts` (Task 1) attempts total -- each attempt gets its own full
`timeout_ms` budget, no shared/shrinking deadline across attempts, and
(a deliberate simplification over `aico.platform.model_gateway`'s own
exponential-backoff retry -- Day 13 working rule: "Do not reuse Model
Gateway retry semantics blindly. Tool retry policy is its own boundary")
no backoff delay between attempts at all: Task 9 asks for *bounded,
policy-safe* retry, not backoff/jitter, so none is invented here. A
failure is only ever retried when all three independent signals agree it
is safe to:

    1. `tool.retry_policy.max_attempts > 1` -- the tool declares retry at
       all.
    2. `tool.idempotent and not tool.side_effecting` -- redundant with
       Task 1's own `ToolDefinition` validator (which already refuses to
       register a retry-enabled side-effecting/non-idempotent tool at
       all) and Task 4's policy-level defense-in-depth check one stage
       earlier; checked a third time here on the same principle those two
       already establish -- belt-and-suspenders, never trust a single
       layer alone for a safety property this consequential.
    3. the failure's own `ToolTransportFailureCategory` is one of `tool.
       retry_policy.retryable_categories` (Task 1) -- e.g. the committed
       `supplier_status_lookup` names `timeout`/`transport_unavailable`;
       `transport_error`/`cancelled` are never retried regardless of what
       a tool declares (Day 13 working rule: "Side-effecting/non-idempotent
       operations are not blindly retried" -- and a cancellation is a
       caller's own explicit request to stop, never a transient condition
       to paper over with another attempt).

Retry stops the moment any of those three is false, the attempt ceiling is
reached (-> the same typed failure category `_dispatch_with_timeout()`
already produced, "retry exhaustion returns typed failure" -- never a
different/generic exhaustion category), or `cancellation` is observed
cancelled between attempts. `ToolExecutionResult.retry_count` records how
many *additional* attempts beyond the first were actually made (`0` for a
first-try success or an immediately non-retryable failure) -- Task 6's own
`FakeToolTransport.call_count` is the lower-level, transport-side mirror
of the same number.

For the disabled/unsafe-side-effecting lab tool, "zero transport calls,
zero retry attempts" needs no special-casing here at all: Task 4's policy
stage already denies it before `_dispatch_with_retry()` is ever reached,
the identical "the disabled tool never gets this far" guarantee Task 6/7/8
already give it.

Day 13 Task 13 -- observability. `execute()` emits exactly one structured
log event per call -- `stage="tool_execution"` -- through
`aico.observability.logging.log_event()`, Day 6's own shared, already-
reviewed structured-logging facility (Task 13: "Preserve Day 6 correlation
context" is read literally: reuse its `request_id`/`correlation_id`/
`stage`/`outcome`/`latency_ms`/`error_category` field conventions rather
than inventing a parallel logging shape). This is a different reuse
decision than the retry/transport contract (`transport.py`'s own "own
boundary" reasoning): `aico.observability.logging` is a small, generic,
already-sanitizing-by-convention utility with no Model-Gateway-specific
semantics baked into it, so there is nothing tool-specific to blindly
inherit by calling it.

Every field logged is drawn from `Day 13 Task.pdf`'s own "Safe metadata
may include" list, computed purely from already-typed, already-sanitized
values -- `registry_version`/`policy_version` (constant per loaded
registry/policy), `tool_id`/`tool_version`/`server_alias`/`risk_level`
(from the resolved `ToolDefinition`, when one was resolved --
`None`/omitted for `tool_not_found`/`version_not_found`, where no such
definition exists), `input_validation_result`/`output_validation_result`
(`"not_reached"`/`"valid"`/`"invalid"`, derived purely from which stage a
`ToolExecutionResult.error_category` shows the pipeline actually reached
-- see `_stage_validation_results()`), `retry_count`, `outcome`
(`ToolExecutionResult.status.value`), `normalized_error` (passed as
`log_event()`'s own existing `error_category` parameter -- Day 6's
established field for exactly this purpose, not a second, parallel field
name), `request_id`/`correlation_id`, and `latency_ms` (the full
`execute()` call's own wall-clock duration).

Never logged, by construction -- there is no code path through which any
of the following could even reach `log_event()`'s call site below:
`request.arguments` (untrusted, may contain business data), the transport
`payload`/`ToolExecutionResult.payload` (external, untrusted, and on
success may carry the governed record itself), or any raw exception
message (`ToolExecutionResult.error_message` is deliberately *not* logged
here either, even though it is already sanitized -- Day 13's own stricter
"do not log raw tool payloads, protected records, secrets or tokens" is
read to mean the normalized *category* is what belongs in default
telemetry, not stage-specific free text, mirroring Day 6's own
`AskResponse.category`-not-message convention)."""
from __future__ import annotations

import queue
import threading
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aico.observability.logging import log_event
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


def _stage_validation_results(
    error_category: ToolExecutionErrorCategory | None,
) -> tuple[str, str]:
    """Derive `(input_validation_result, output_validation_result)` --
    each `"valid"`, `"invalid"`, or `"not_reached"` -- purely from which
    stage a `ToolExecutionResult.error_category` shows the pipeline
    actually reached (Task 13's own safe metadata list). A pure function
    of the already-typed result, not extra state threaded through
    `_run_pipeline()` -- the required stage order (Task 7) already fixes
    which stages a given category could only have come from."""
    if error_category is None:  # SUCCESS -- every stage was reached and passed.
        return "valid", "valid"

    if error_category in (
        ToolExecutionErrorCategory.TOOL_NOT_FOUND,
        ToolExecutionErrorCategory.VERSION_NOT_FOUND,
        ToolExecutionErrorCategory.TOOL_DISABLED,
        ToolExecutionErrorCategory.POLICY_DENIED,
    ):
        return "not_reached", "not_reached"

    if error_category is ToolExecutionErrorCategory.INPUT_INVALID:
        return "invalid", "not_reached"

    if error_category in (
        ToolExecutionErrorCategory.TIMEOUT,
        ToolExecutionErrorCategory.CANCELLED,
        ToolExecutionErrorCategory.TRANSPORT_UNAVAILABLE,
        ToolExecutionErrorCategory.TRANSPORT_ERROR,
    ):
        return "valid", "not_reached"  # input validation passed; transport never returned a payload to check

    return "valid", "invalid"  # ToolExecutionErrorCategory.OUTPUT_INVALID


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
    retry_count: int = Field(
        default=0, ge=0, description="Additional transport attempts beyond the first (Task 9). 0 unless a retry ran."
    )

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
        """Run the full, required-order pipeline for one request, and emit
        exactly one sanitized `stage="tool_execution"` observability event
        for it (Task 13 -- see module docstring). Never raises for an
        ordinary outcome -- success and every failure category are both
        normal, typed `ToolExecutionResult` values, and the event is
        always emitted, on every path, before returning."""
        start = time.monotonic()
        result = self._run_pipeline(request, cancellation=cancellation)
        latency_ms = (time.monotonic() - start) * 1000
        self._log_execution(request, result, latency_ms=latency_ms)
        return result

    def _log_execution(self, request: ToolExecutionRequest, result: ToolExecutionResult, *, latency_ms: float) -> None:
        """The one place a `tool_execution` observability event is ever
        built and emitted -- see module docstring's "Day 13 Task 13"
        paragraph for exactly which fields are safe and why."""
        tool: ToolDefinition | None = None
        try:
            tool = self._registry.get_tool(result.tool_id, result.tool_version)
        except (ToolNotFoundError, ToolVersionNotFoundError):
            pass  # unregistered tool/version -- no ToolDefinition metadata to attach

        input_validation_result, output_validation_result = _stage_validation_results(result.error_category)

        log_event(
            request_id=result.request_id,
            correlation_id=result.correlation_id,
            stage="tool_execution",
            outcome=result.status.value,
            latency_ms=latency_ms,
            error_category=result.error_category.value if result.error_category is not None else None,
            tool_id=result.tool_id,
            tool_version=result.tool_version,
            registry_version=self._registry.registry_version,
            policy_version=self._policy.policy_version,
            server_alias=tool.server_alias if tool is not None else None,
            risk_level=tool.risk_level.value if tool is not None else None,
            input_validation_result=input_validation_result,
            output_validation_result=output_validation_result,
            retry_count=result.retry_count,
        )

    def _run_pipeline(
        self, request: ToolExecutionRequest, *, cancellation: ToolCancellationToken | None = None
    ) -> ToolExecutionResult:
        """The full, required-order pipeline for one request -- unchanged
        from Task 7 other than its name (`execute()`, above, is now the
        public entrypoint, wrapping this with Task 13's own observability
        event); every "Do not create a second raw helper that bypasses
        this sequence" guarantee still applies to this method exactly as
        it did when it was named `execute()`."""

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
        # tool's own timeout and interruptible by cancellation (Task 8),
        # retried up to the tool's own bounded, policy-safe limit
        # (Task 9). The gateway itself enforces "registered, policy-
        # approved, input-schema-valid" as a precondition; every input it
        # needs was just produced above.
        transport_result, retry_count = self._dispatch_with_retry(
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
                retry_count=retry_count,
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
                retry_count=retry_count,
            )

        # Stage 7 -- typed result.
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            tool_id=tool.tool_id,
            tool_version=tool.tool_version,
            request_id=request.request_id,
            correlation_id=request.correlation_id,
            payload=validated_output,
            retry_count=retry_count,
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

    def _dispatch_with_retry(
        self,
        *,
        tool: ToolDefinition,
        request: ToolExecutionRequest,
        validated_arguments: dict[str, Any],
        policy_decision: ToolExecutionPolicyDecision,
        cancellation: ToolCancellationToken | None,
    ) -> tuple[ToolTransportSuccess | ToolTransportFailure, int]:
        """Run `_dispatch_with_timeout()` up to `tool.retry_policy.
        max_attempts` times, retrying only when every safety signal in the
        module docstring's "Day 13 Task 9" paragraph agrees it is safe.
        Returns `(result, retry_count)` -- `retry_count` is the number of
        *additional* attempts beyond the first that actually ran."""
        retry_safe = (
            tool.retry_policy.max_attempts > 1 and tool.idempotent and not tool.side_effecting
        )
        retryable_category_values = {category.value for category in tool.retry_policy.retryable_categories}

        attempt = 0
        while True:
            attempt += 1
            result = self._dispatch_with_timeout(
                tool=tool,
                request=request,
                validated_arguments=validated_arguments,
                policy_decision=policy_decision,
                cancellation=cancellation,
            )
            if isinstance(result, ToolTransportSuccess):
                return result, attempt - 1

            # result is a ToolTransportFailure.
            if not retry_safe:
                return result, attempt - 1
            if result.category.value not in retryable_category_values:
                return result, attempt - 1
            if attempt >= tool.retry_policy.max_attempts:
                return result, attempt - 1  # retry exhaustion -- the same typed failure category
            if cancellation is not None and cancellation.is_cancelled():
                return result, attempt - 1  # never retry past an observed cancellation

    @staticmethod
    def _failure(
        request: ToolExecutionRequest,
        *,
        category: ToolExecutionErrorCategory,
        message: str,
        tool: ToolDefinition | None = None,
        retry_count: int = 0,
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
            retry_count=retry_count,
        )
