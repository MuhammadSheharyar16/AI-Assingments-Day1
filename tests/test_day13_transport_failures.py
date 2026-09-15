"""
Day 13 Task 8 -- timeout and cancellation enforcement
(`ToolExecutor._dispatch_with_timeout()`, `src/aico/tools/executor.py`).
Day 13 Task 9 -- retry safety (`ToolExecutor._dispatch_with_retry()`, same
file).

Proves, against the real committed registry/policy and deterministic fake
transports:

  - "timeout enforced": a tool call bounded by `ToolDefinition.timeout_ms`
    is not waited on past its own deadline, even when the underlying
    transport is far slower (`transport.py`'s `DelayedStep`, Task 8's own
    "use a slow fake transport" instruction) -- `ToolExecutor.execute()`
    returns well before the slow transport would have finished on its own;
  - "timeout normalized": the result is always a typed
    `ToolExecutionErrorCategory.TIMEOUT` `ToolExecutionResult`, never a raw
    `TimeoutError`/exception escaping `execute()`;
  - "request cancellation reaches fake transport": an externally supplied
    `ToolCancellationToken`, cancelled from another thread while a call is
    blocked on `FakeToolTransport`'s own `wait_until_cancelled` step,
    unblocks both the fake transport itself and `execute()`'s own caller
    (`transport_failure_cases.json` TR13-004's own shape);
  - "cancelled transport does not later produce normal success": once a
    deadline/cancellation has already produced a typed failure result and
    `execute()` has returned, a slow/non-cooperative transport's eventual,
    later-arriving success payload is never delivered to any caller -- it
    is simply discarded (there is nobody left listening);
  - Task 9: a transient, retryable transport failure for the active,
    read-only/idempotent lab tool retries and, given a subsequent success,
    the call succeeds (`transport_failure_cases.json` TR13-002); retry
    count is bounded to `tool.retry_policy.max_attempts` and exhaustion
    returns the same typed failure category, never a different/generic
    one (TR13-001/TR13-003); a non-retryable category (`transport_error`)
    is never retried even for the same tool; and the disabled/unsafe
    side-effecting tool makes zero transport calls and zero retry attempts
    (TR13-005) because Task 4's policy stage denies it before the retry
    loop is ever reached.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from aico.tools.executor import ToolExecutionErrorCategory, ToolExecutionResult, ToolExecutionStatus, ToolExecutor
from aico.tools.mcp_gateway import MCPGateway, ToolTransportFailure, ToolTransportFailureCategory
from aico.tools.models import RetryPolicy, ToolExecutionRequest
from aico.tools.policy import ToolExecutionPolicy, ToolExecutionPolicyDecision, ToolExecutionPolicyStatus
from aico.tools.registry import ToolRegistry
from aico.tools.transport import DelayedStep, FakeToolTransport, ToolCancellationToken

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_REGISTRY_PATH = REPO_ROOT / "tools" / "registry.v1.json"
COMMITTED_POLICY_PATH = REPO_ROOT / "policy" / "tool_execution_policy.v1.json"
TRANSPORT_FAILURE_CASES_PATH = REPO_ROOT / "data" / "day13_pack" / "fixtures" / "transport_failure_cases.json"


def _load_transport_failure_cases() -> list[dict]:
    return json.loads(TRANSPORT_FAILURE_CASES_PATH.read_text(encoding="utf-8"))["cases"]


def _lookup_request(**overrides: object) -> ToolExecutionRequest:
    fields = {
        "tool_id": "supplier_status_lookup",
        "tool_version": "1.0.0",
        "arguments": {"supplier_id": "SUP-ALPHA"},
        "trusted_permissions": ["read_structured_supplier"],
    }
    fields.update(overrides)
    return ToolExecutionRequest.model_validate(fields)


def _executor(steps, *, cancellation_poll_seconds: float = 0.01) -> tuple[ToolExecutor, FakeToolTransport]:
    registry = ToolRegistry.load(COMMITTED_REGISTRY_PATH)
    policy = ToolExecutionPolicy.load(COMMITTED_POLICY_PATH)
    transport = FakeToolTransport(steps)
    gateway = MCPGateway(transport)
    return ToolExecutor(registry, policy, gateway, cancellation_poll_seconds=cancellation_poll_seconds), transport


def _fast_tool_executor(steps, *, timeout_ms: int, cancellation_poll_seconds: float = 0.005):
    """An executor whose registry resolves `supplier_status_lookup` with a
    much shorter `timeout_ms` than the committed 900ms -- so timeout tests
    run in well under a second instead of waiting nearly a full second.
    Retry is also disabled (`max_attempts=1`) here: these tests are about
    the timeout wrapper in isolation, and the real tool's own `timeout` is
    a retryable category (Task 9) -- without disabling retry, a single
    configured slow step would be consumed by attempt 1's timeout and then
    exhausted on attempt 2 (`retry safety` is proven separately in
    `TestRetrySafety`, not here)."""
    real_registry = ToolRegistry.load(COMMITTED_REGISTRY_PATH)
    fast_tool = real_registry.get_tool("supplier_status_lookup", "1.0.0").model_copy(
        update={
            "timeout_ms": timeout_ms,
            "retry_policy": RetryPolicy(max_attempts=1, retryable_categories=[]),
        }
    )

    class _FastRegistry:
        def get_tool(self, tool_id: str, tool_version: str):
            return real_registry.get_tool(tool_id, tool_version) if tool_id != "supplier_status_lookup" else fast_tool

    policy = ToolExecutionPolicy.load(COMMITTED_POLICY_PATH)
    transport = FakeToolTransport(steps)
    gateway = MCPGateway(transport)
    executor = ToolExecutor(_FastRegistry(), policy, gateway, cancellation_poll_seconds=cancellation_poll_seconds)
    return executor, transport, fast_tool


# ══════════════════════════════════════════════════════════════════════
# Timeout enforced / normalized -- a slow fake transport (`DelayedStep`).
# ══════════════════════════════════════════════════════════════════════


class TestTimeoutEnforcement:
    def test_slow_transport_times_out_before_it_finishes(self) -> None:
        """The tool's own 50ms budget elapses long before the transport's
        own 400ms delay would ever return -- `execute()` must not wait for
        it."""
        success_payload = {"supplier_id": "SUP-ALPHA", "status": "active", "as_of": "2026-09-15T09:00:00Z"}
        executor, transport, fast_tool = _fast_tool_executor(
            [DelayedStep(delay_seconds=0.4, step=success_payload)], timeout_ms=50
        )
        request = _lookup_request()

        start = time.monotonic()
        result = executor.execute(request)
        elapsed = time.monotonic() - start

        assert isinstance(result, ToolExecutionResult)
        assert result.status is ToolExecutionStatus.FAILURE
        assert result.error_category is ToolExecutionErrorCategory.TIMEOUT
        assert result.payload is None
        # Returned close to the 50ms budget, nowhere near the 400ms delay.
        assert elapsed < 0.3, f"execute() waited {elapsed:.3f}s -- should have returned near the 50ms budget"

    def test_timeout_result_is_a_typed_result_never_a_raw_exception(self) -> None:
        """"Timeout normalized": `execute()` never raises `TimeoutError`
        (or anything else) for a timeout -- it always returns the one typed
        `ToolExecutionResult`/`TIMEOUT` shape."""
        executor, _, _ = _fast_tool_executor([DelayedStep(delay_seconds=0.3, step={"ok": True})], timeout_ms=30)
        request = _lookup_request()

        result = executor.execute(request)  # must not raise

        assert result.status is ToolExecutionStatus.FAILURE
        assert result.error_category is ToolExecutionErrorCategory.TIMEOUT
        assert isinstance(result.error_message, str) and result.error_message

    def test_fast_transport_within_budget_still_succeeds(self) -> None:
        """The timeout wrapper must not itself slow down or break an
        ordinary, fast call."""
        payload = {"supplier_id": "SUP-ALPHA", "status": "active", "as_of": "2026-09-15T09:00:00Z"}
        executor, transport, _ = _fast_tool_executor([payload], timeout_ms=900)
        request = _lookup_request()

        result = executor.execute(request)

        assert result.status is ToolExecutionStatus.SUCCESS
        assert result.payload == payload
        assert transport.call_count == 1

    def test_cancelled_transport_does_not_later_produce_normal_success(self) -> None:
        """Once the deadline has already produced a typed `TIMEOUT` result
        and `execute()` has returned, the slow transport's own eventual,
        later-arriving success payload must never surface anywhere --
        there is nobody left listening for it."""
        success_payload = {"supplier_id": "SUP-ALPHA", "status": "active", "as_of": "2026-09-15T09:00:00Z"}
        executor, transport, _ = _fast_tool_executor(
            [DelayedStep(delay_seconds=0.25, step=success_payload)], timeout_ms=40
        )
        request = _lookup_request()

        result = executor.execute(request)
        assert result.status is ToolExecutionStatus.FAILURE
        assert result.error_category is ToolExecutionErrorCategory.TIMEOUT

        # Let the background transport call actually finish its delay and
        # "produce" the success payload -- into a queue nothing reads
        # anymore. The already-returned, immutable `result` is still the
        # typed timeout failure; nothing mutates it, and no second value
        # is ever handed to this (or any) caller for the same call.
        time.sleep(0.35)
        assert result.status is ToolExecutionStatus.FAILURE
        assert result.error_category is ToolExecutionErrorCategory.TIMEOUT
        assert result.payload is None
        with pytest.raises(ValidationError):
            result.status = ToolExecutionStatus.SUCCESS  # frozen -- cannot be "upgraded" even if something tried


# ══════════════════════════════════════════════════════════════════════
# Cancellation reaches the fake transport.
# ══════════════════════════════════════════════════════════════════════


class TestCancellationPropagation:
    def test_external_cancellation_reaches_fake_transport(self) -> None:
        """`transport_failure_cases.json` TR13-004's own shape: a call
        blocked on `wait_until_cancelled`, unblocked by cancelling the
        caller's own token from another thread."""
        executor, transport = _executor(["wait_until_cancelled"])
        request = _lookup_request()
        token = ToolCancellationToken()

        def _cancel_shortly() -> None:
            time.sleep(0.05)
            token.cancel()

        threading.Thread(target=_cancel_shortly, daemon=True).start()

        start = time.monotonic()
        result = executor.execute(request, cancellation=token)
        elapsed = time.monotonic() - start

        assert result.status is ToolExecutionStatus.FAILURE
        assert result.error_category is ToolExecutionErrorCategory.CANCELLED
        assert result.payload is None
        assert elapsed < 1.0, f"execute() took {elapsed:.3f}s to observe cancellation"
        assert transport.call_count == 1

    def test_no_cancellation_token_supplied_is_unaffected(self) -> None:
        """Calls made with no `cancellation` argument at all behave exactly
        as before Task 8 -- only the tool's own timeout budget applies."""
        payload = {"supplier_id": "SUP-ALPHA", "status": "active", "as_of": "2026-09-15T09:00:00Z"}
        executor, transport = _executor([payload])
        request = _lookup_request()

        result = executor.execute(request)

        assert result.status is ToolExecutionStatus.SUCCESS
        assert result.payload == payload


# ══════════════════════════════════════════════════════════════════════
# Day 13 Task 9 -- retry safety.
# ══════════════════════════════════════════════════════════════════════


class TestRetrySafety:
    def test_transient_failure_then_success_retries_and_succeeds(self) -> None:
        """`transport_failure_cases.json` TR13-002: the real committed
        `supplier_status_lookup` tool declares `transport_unavailable` as
        retryable (`tools/registry.v1.json`) -- a single transient failure
        followed by a success must resolve to `SUCCESS` after exactly one
        retry."""
        case = next(c for c in _load_transport_failure_cases() if c["id"] == "TR13-002")
        executor, transport = _executor(case["transport_sequence"])
        request = _lookup_request()

        result = executor.execute(request)

        assert result.status is ToolExecutionStatus.SUCCESS
        assert result.payload == case["transport_sequence"][1]
        assert result.retry_count == 1
        assert transport.call_count == case["expected_transport_calls"] == 2

    def test_retry_count_is_bounded_to_max_attempts(self) -> None:
        """`transport_failure_cases.json` TR13-003: two transient failures
        in a row exhaust the tool's own `max_attempts=2` -- never a third
        attempt, never an unbounded loop."""
        case = next(c for c in _load_transport_failure_cases() if c["id"] == "TR13-003")
        executor, transport = _executor(case["transport_sequence"])
        request = _lookup_request()

        result = executor.execute(request)

        assert result.status is ToolExecutionStatus.FAILURE
        assert result.error_category is ToolExecutionErrorCategory.TRANSPORT_UNAVAILABLE
        assert result.retry_count == 1  # one retry beyond the first attempt = 2 attempts total
        assert transport.call_count == case["max_transport_calls"] == 2

    def test_retry_exhaustion_returns_the_same_typed_failure_category(self) -> None:
        """`transport_failure_cases.json` TR13-001: repeated `"timeout"`
        steps exhaust retry and resolve to `TIMEOUT` -- never a distinct
        "retry_exhausted" category invented on top of it."""
        case = next(c for c in _load_transport_failure_cases() if c["id"] == "TR13-001")
        executor, transport = _executor(case["transport_sequence"])
        request = _lookup_request()

        result = executor.execute(request)

        assert result.status is ToolExecutionStatus.FAILURE
        assert result.error_category is ToolExecutionErrorCategory.TIMEOUT
        assert transport.call_count == case["max_transport_calls"] == 2

    def test_non_retryable_category_is_never_retried(self) -> None:
        """`transport_error` is not in `supplier_status_lookup`'s own
        `retryable_categories` -- one call, one immediate typed failure,
        no retry attempted even though the tool itself is otherwise
        retry-eligible (idempotent, `max_attempts > 1`)."""
        executor, transport = _executor(["transport_error"])
        request = _lookup_request()

        result = executor.execute(request)

        assert result.status is ToolExecutionStatus.FAILURE
        assert result.error_category is ToolExecutionErrorCategory.TRANSPORT_ERROR
        assert result.retry_count == 0
        assert transport.call_count == 1

    def test_cancellation_observed_between_attempts_stops_retrying(self) -> None:
        """A caller-cancelled token must stop the retry loop even mid-way
        through an otherwise retryable failure sequence -- never retry
        past an observed cancellation."""
        executor, transport = _executor(["transport_unavailable", "transport_unavailable"])
        request = _lookup_request()
        token = ToolCancellationToken()
        token.cancel()  # already cancelled before the first attempt even starts

        result = executor.execute(request, cancellation=token)

        assert result.status is ToolExecutionStatus.FAILURE
        assert result.error_category is ToolExecutionErrorCategory.CANCELLED
        assert transport.call_count == 1

    def test_disabled_tool_makes_zero_retry_attempts(self) -> None:
        """TR13-005: the disabled, side-effecting `supplier_record_update`
        tool never reaches the retry loop at all -- Task 4's policy stage
        denies it first."""
        executor, transport = _executor([{"never": "reached"}, {"never": "reached"}])
        request = _lookup_request(
            tool_id="supplier_record_update",
            tool_version="1.0.0",
            arguments={"supplier_id": "SUP-ALPHA", "status": "inactive"},
            trusted_permissions=["write_structured_supplier"],
        )

        result = executor.execute(request)

        assert result.error_category is ToolExecutionErrorCategory.TOOL_DISABLED
        assert result.retry_count == 0
        assert transport.call_count == 0

    def test_successful_first_attempt_has_zero_retry_count(self) -> None:
        payload = {"supplier_id": "SUP-ALPHA", "status": "active", "as_of": "2026-09-15T09:00:00Z"}
        executor, transport = _executor([payload])
        request = _lookup_request()

        result = executor.execute(request)

        assert result.status is ToolExecutionStatus.SUCCESS
        assert result.retry_count == 0
        assert transport.call_count == 1

    def test_unsafe_tool_is_denied_by_policy_before_the_retry_loop_is_ever_reached(self) -> None:
        """A hand-built, retry-enabled-but-non-idempotent tool never even
        reaches `_dispatch_with_retry()` through the full pipeline: Task
        4's own policy defense-in-depth (`unsafe_retry_for_non_idempotent_
        tool`) denies it one stage earlier, so the retry loop's own gate
        (proven directly, below) is never the only thing standing between
        this shape and an unsafe retry."""
        real_tool = ToolRegistry.load(COMMITTED_REGISTRY_PATH).get_tool("supplier_status_lookup", "1.0.0")
        unsafe_tool = real_tool.model_copy(update={"idempotent": False})

        class _UnsafeRegistry:
            def get_tool(self, tool_id: str, tool_version: str):
                return unsafe_tool

        policy = ToolExecutionPolicy.load(COMMITTED_POLICY_PATH)
        transport = FakeToolTransport(["transport_unavailable", "transport_unavailable"])
        gateway = MCPGateway(transport)
        executor = ToolExecutor(_UnsafeRegistry(), policy, gateway)
        request = _lookup_request()

        result = executor.execute(request)

        assert result.status is ToolExecutionStatus.FAILURE
        assert result.error_category is ToolExecutionErrorCategory.POLICY_DENIED
        assert result.error_message == "unsafe_retry_for_non_idempotent_tool"
        assert result.retry_count == 0
        assert transport.call_count == 0

    def test_dispatch_with_retry_itself_refuses_to_retry_a_non_idempotent_tool(self) -> None:
        """The retry loop's own three-signal gate, proven directly against
        `_dispatch_with_retry()` (bypassing the policy stage on purpose --
        see the test above for why the full pipeline never actually
        reaches this method with such a tool): even handed an `ALLOW`
        decision, a non-idempotent tool is never retried."""
        real_tool = ToolRegistry.load(COMMITTED_REGISTRY_PATH).get_tool("supplier_status_lookup", "1.0.0")
        unsafe_tool = real_tool.model_copy(update={"idempotent": False})

        policy = ToolExecutionPolicy.load(COMMITTED_POLICY_PATH)
        transport = FakeToolTransport(["transport_unavailable", "transport_unavailable"])
        gateway = MCPGateway(transport)
        executor = ToolExecutor(ToolRegistry.load(COMMITTED_REGISTRY_PATH), policy, gateway)
        request = _lookup_request()

        forced_allow_decision = ToolExecutionPolicyDecision(
            decision=ToolExecutionPolicyStatus.ALLOW,
            reason_code="rule_allowed",
            tool_id=unsafe_tool.tool_id,
            tool_version=unsafe_tool.tool_version,
            rule_id="TOOL-R001",
            policy_version=policy.policy_version,
            require_idempotent_for_retry=True,
        )

        result, retry_count = executor._dispatch_with_retry(  # noqa: SLF001 -- deliberate white-box test
            tool=unsafe_tool,
            request=request,
            validated_arguments=request.arguments,
            policy_decision=forced_allow_decision,
            cancellation=None,
        )

        assert isinstance(result, ToolTransportFailure)
        assert result.category is ToolTransportFailureCategory.TRANSPORT_UNAVAILABLE
        assert retry_count == 0
        assert transport.call_count == 1


# ══════════════════════════════════════════════════════════════════════
# `transport_failure_cases.json` -- fixture coverage.
# ══════════════════════════════════════════════════════════════════════


class TestTransportFailureCasesFixture:
    def test_tr13_004_cancelled(self) -> None:
        case = next(c for c in _load_transport_failure_cases() if c["id"] == "TR13-004")
        executor, transport = _executor(case["transport_sequence"])
        request = _lookup_request()
        token = ToolCancellationToken()

        def _cancel_shortly() -> None:
            time.sleep(0.05)
            token.cancel()

        threading.Thread(target=_cancel_shortly, daemon=True).start()
        result = executor.execute(request, cancellation=token)

        assert result.error_category is ToolExecutionErrorCategory.CANCELLED
        assert case["expected"] == "cancelled"

    def test_tr13_005_disabled_tool_never_retries_or_calls_transport(self) -> None:
        case = next(c for c in _load_transport_failure_cases() if c["id"] == "TR13-005")
        executor, transport = _executor([{"never": "reached"}])
        request = _lookup_request(
            tool_id="supplier_record_update",
            tool_version="1.0.0",
            arguments={"supplier_id": "SUP-ALPHA", "status": "inactive"},
            trusted_permissions=["write_structured_supplier"],
        )

        result = executor.execute(request)

        assert result.error_category is ToolExecutionErrorCategory.TOOL_DISABLED
        assert transport.call_count == case["expected_transport_calls"] == 0

    def test_every_fixture_case_id_is_known(self) -> None:
        case_ids = {c["id"] for c in _load_transport_failure_cases()}
        assert case_ids == {"TR13-001", "TR13-002", "TR13-003", "TR13-004", "TR13-005"}
