"""
Day 13 Task 7 -- the controlled executor (`ToolExecutor`,
`src/aico/tools/executor.py`) -- the single pipeline entrypoint wiring
Tasks 2/4/5/6 together in the required order: registry -> policy -> input
schema validation -> MCP Gateway -> transport -> output schema validation
-> typed result.

Not one of the required-structure's named test files (`test_day13_tool_
registry.py` / `test_day13_tool_policy.py` / `test_day13_input_schema.py` /
`test_day13_mcp_gateway.py` / `test_day13_transport_failures.py` /
`test_day13_output_schema.py` / `test_day13_no_direct_execution.py` /
`test_day13_regression.py`) maps onto Task 7's own end-to-end wiring --
each of those proves one boundary in isolation. This file is the
"equivalent previously accepted filename" allowance
(`Day 13 Task.pdf`: "Equivalent previously accepted filenames are allowed
if documented in README.md") for the executor itself, documented in
`README.md`.

Proves, against the real committed registry/policy and a deterministic
`FakeToolTransport`:

  - `execution_cases.json` EXEC13-001..006 each produce the fixture's own
    `expected` outcome and `expected_transport_calls`;
  - the required stage order: a failure at any stage produces the correct
    `ToolExecutionErrorCategory` and makes zero transport calls for every
    case before the gateway (tool_not_found, version_not_found,
    tool_disabled, policy_denied, input_invalid);
  - a successful call makes exactly one transport call and returns the
    already output-schema-validated payload;
  - `ToolExecutionResult`'s "nothing granted on failure" guarantee:
    `payload` is `None` for every failure, `error_category`/
    `error_message` are `None` for success;
  - `ToolExecutor` exposes no second helper that could bypass the
    sequence (structural check: `execute` is its only public method).

Timeout/cancellation enforcement (Task 8), retry (Task 9), the full
normalized-error sweep (Task 10), and output-schema-specific coverage
(Task 11) each build on this same executor and get their own dedicated
files -- this file only proves Task 7's own required wiring/order.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aico.tools.executor import ToolExecutionErrorCategory, ToolExecutionResult, ToolExecutionStatus, ToolExecutor
from aico.tools.mcp_gateway import MCPGateway
from aico.tools.models import ToolExecutionRequest
from aico.tools.policy import ToolExecutionPolicy
from aico.tools.registry import ToolRegistry
from aico.tools.transport import FakeToolTransport

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_REGISTRY_PATH = REPO_ROOT / "tools" / "registry.v1.json"
COMMITTED_POLICY_PATH = REPO_ROOT / "policy" / "tool_execution_policy.v1.json"
EXECUTION_CASES_PATH = REPO_ROOT / "data" / "day13_pack" / "fixtures" / "execution_cases.json"


def _load_execution_cases() -> list[dict]:
    return json.loads(EXECUTION_CASES_PATH.read_text(encoding="utf-8"))["cases"]


def _make_executor(steps: list) -> tuple[ToolExecutor, FakeToolTransport]:
    registry = ToolRegistry.load(COMMITTED_REGISTRY_PATH)
    policy = ToolExecutionPolicy.load(COMMITTED_POLICY_PATH)
    transport = FakeToolTransport(steps)
    gateway = MCPGateway(transport)
    return ToolExecutor(registry, policy, gateway), transport


_EXPECTED_TO_CATEGORY = {
    "tool_not_found": ToolExecutionErrorCategory.TOOL_NOT_FOUND,
    "tool_disabled": ToolExecutionErrorCategory.TOOL_DISABLED,
    "policy_denied": ToolExecutionErrorCategory.POLICY_DENIED,
    "input_invalid": ToolExecutionErrorCategory.INPUT_INVALID,
}


# ══════════════════════════════════════════════════════════════════════
# `execution_cases.json` EXEC13-001..006 -- the full pipeline end to end.
# ══════════════════════════════════════════════════════════════════════


class TestExecutionCasesFixture:
    def test_valid_lookup_succeeds(self) -> None:
        case = next(c for c in _load_execution_cases() if c["id"] == "EXEC13-001")
        executor, transport = _make_executor([case["fake_transport_result"]])
        request = ToolExecutionRequest.model_validate(case["request"])

        result = executor.execute(request)

        assert result.status is ToolExecutionStatus.SUCCESS
        assert result.succeeded is True
        assert result.payload == case["fake_transport_result"]
        assert result.error_category is None
        assert result.error_message is None
        assert transport.call_count == case["expected_transport_calls"]

    def test_missing_required_input_is_input_invalid_with_zero_transport_calls(self) -> None:
        case = next(c for c in _load_execution_cases() if c["id"] == "EXEC13-002")
        executor, transport = _make_executor([])
        request = ToolExecutionRequest.model_validate(case["request"])

        result = executor.execute(request)

        assert result.status is ToolExecutionStatus.FAILURE
        assert result.error_category is _EXPECTED_TO_CATEGORY[case["expected"]]
        assert result.payload is None
        assert transport.call_count == case["expected_transport_calls"] == 0

    def test_extra_privilege_argument_is_input_invalid_with_zero_transport_calls(self) -> None:
        case = next(c for c in _load_execution_cases() if c["id"] == "EXEC13-003")
        executor, transport = _make_executor([])
        request = ToolExecutionRequest.model_validate(case["request"])

        result = executor.execute(request)

        assert result.status is ToolExecutionStatus.FAILURE
        assert result.error_category is _EXPECTED_TO_CATEGORY[case["expected"]]
        assert transport.call_count == case["expected_transport_calls"] == 0

    def test_permission_denied_case_is_policy_denied_with_zero_transport_calls(self) -> None:
        case = next(c for c in _load_execution_cases() if c["id"] == "EXEC13-004")
        executor, transport = _make_executor([])
        request = ToolExecutionRequest.model_validate(case["request"])

        result = executor.execute(request)

        assert result.status is ToolExecutionStatus.FAILURE
        assert result.error_category is _EXPECTED_TO_CATEGORY[case["expected"]]
        assert transport.call_count == case["expected_transport_calls"] == 0

    def test_disabled_tool_case_is_tool_disabled_with_zero_transport_calls(self) -> None:
        case = next(c for c in _load_execution_cases() if c["id"] == "EXEC13-005")
        executor, transport = _make_executor([{"never": "reached"}])
        request = ToolExecutionRequest.model_validate(case["request"])

        result = executor.execute(request)

        assert result.status is ToolExecutionStatus.FAILURE
        assert result.error_category is _EXPECTED_TO_CATEGORY[case["expected"]]
        assert transport.call_count == case["expected_transport_calls"] == 0

    def test_unknown_tool_case_is_tool_not_found_with_zero_transport_calls(self) -> None:
        case = next(c for c in _load_execution_cases() if c["id"] == "EXEC13-006")
        executor, transport = _make_executor([])
        request = ToolExecutionRequest.model_validate(case["request"])

        result = executor.execute(request)

        assert result.status is ToolExecutionStatus.FAILURE
        assert result.error_category is _EXPECTED_TO_CATEGORY[case["expected"]]
        assert transport.call_count == case["expected_transport_calls"] == 0

    def test_every_execution_case_is_covered(self) -> None:
        """Guards against a future fixture update silently gaining a case
        this file forgets to test."""
        case_ids = {c["id"] for c in _load_execution_cases()}
        assert case_ids == {f"EXEC13-00{n}" for n in range(1, 7)}


# ══════════════════════════════════════════════════════════════════════
# Unknown tool version specifically (distinct from unknown tool_id).
# ══════════════════════════════════════════════════════════════════════


def test_unknown_version_of_a_known_tool_is_version_not_found() -> None:
    executor, transport = _make_executor([])
    request = ToolExecutionRequest.model_validate(
        {
            "tool_id": "supplier_status_lookup",
            "tool_version": "9.9.9",
            "arguments": {"supplier_id": "SUP-ALPHA"},
            "trusted_permissions": ["read_structured_supplier"],
        }
    )

    result = executor.execute(request)

    assert result.status is ToolExecutionStatus.FAILURE
    assert result.error_category is ToolExecutionErrorCategory.VERSION_NOT_FOUND
    assert transport.call_count == 0


# ══════════════════════════════════════════════════════════════════════
# Output schema validation is wired into the required sequence.
# ══════════════════════════════════════════════════════════════════════


def test_invalid_output_is_output_invalid_never_returned_as_success() -> None:
    """Malformed transport output must never surface as `SUCCESS` --
    Task 7/11's own critical property."""
    executor, transport = _make_executor([{"supplier_id": "SUP-ALPHA"}])  # missing status/as_of
    request = ToolExecutionRequest.model_validate(
        {
            "tool_id": "supplier_status_lookup",
            "tool_version": "1.0.0",
            "arguments": {"supplier_id": "SUP-ALPHA"},
            "trusted_permissions": ["read_structured_supplier"],
        }
    )

    result = executor.execute(request)

    assert result.status is ToolExecutionStatus.FAILURE
    assert result.error_category is ToolExecutionErrorCategory.OUTPUT_INVALID
    assert result.payload is None
    assert transport.call_count == 1  # the call itself did reach transport; only the payload was rejected


# ══════════════════════════════════════════════════════════════════════
# "Nothing granted on failure" / "nothing extra on success".
# ══════════════════════════════════════════════════════════════════════


def test_every_failure_leaves_payload_none() -> None:
    scenarios = [
        {
            "steps": [],
            "request": {
                "tool_id": "model_invented_tool",
                "tool_version": "1.0.0",
                "arguments": {},
                "trusted_permissions": [],
            },
        },
        {
            "steps": [],
            "request": {
                "tool_id": "supplier_status_lookup",
                "tool_version": "1.0.0",
                "arguments": {},
                "trusted_permissions": ["read_structured_supplier"],
            },
        },
    ]
    for scenario in scenarios:
        executor, _ = _make_executor(scenario["steps"])
        request = ToolExecutionRequest.model_validate(scenario["request"])
        result = executor.execute(request)
        assert result.status is ToolExecutionStatus.FAILURE
        assert result.payload is None
        assert result.error_category is not None
        assert result.error_message is not None


def test_success_leaves_error_fields_none() -> None:
    executor, _ = _make_executor([{"supplier_id": "SUP-ALPHA", "status": "active", "as_of": "2026-09-15T09:00:00Z"}])
    request = ToolExecutionRequest.model_validate(
        {
            "tool_id": "supplier_status_lookup",
            "tool_version": "1.0.0",
            "arguments": {"supplier_id": "SUP-ALPHA"},
            "trusted_permissions": ["read_structured_supplier"],
        }
    )
    result = executor.execute(request)
    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.error_category is None
    assert result.error_message is None
    assert result.payload is not None


def test_result_is_frozen() -> None:
    executor, _ = _make_executor([{"supplier_id": "SUP-ALPHA", "status": "active", "as_of": "2026-09-15T09:00:00Z"}])
    request = ToolExecutionRequest.model_validate(
        {
            "tool_id": "supplier_status_lookup",
            "tool_version": "1.0.0",
            "arguments": {"supplier_id": "SUP-ALPHA"},
            "trusted_permissions": ["read_structured_supplier"],
        }
    )
    result = executor.execute(request)
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        result.status = ToolExecutionStatus.FAILURE


# ══════════════════════════════════════════════════════════════════════
# Determinism and "no second raw helper that bypasses this sequence".
# ══════════════════════════════════════════════════════════════════════


def test_execute_is_deterministic_for_a_denial() -> None:
    executor, _ = _make_executor([])
    request = ToolExecutionRequest.model_validate(
        {
            "tool_id": "supplier_status_lookup",
            "tool_version": "1.0.0",
            "arguments": {"supplier_id": "SUP-ALPHA"},
            "trusted_permissions": [],
        }
    )
    first = executor.execute(request)
    second = executor.execute(request)
    assert first == second


def test_tool_executor_exposes_no_bypassing_helper() -> None:
    """`ToolExecutor` has exactly one public entrypoint -- no
    `execute_unchecked()`/`_raw_execute()`/direct transport/gateway
    accessor a caller could reach around the required sequence with."""
    public_callables = [
        name
        for name in dir(ToolExecutor)
        if not name.startswith("_") and callable(getattr(ToolExecutor, name, None))
    ]
    assert public_callables == ["execute"]


def test_result_type_field_shape() -> None:
    field_names = set(ToolExecutionResult.model_fields)
    assert field_names == {
        "status",
        "tool_id",
        "tool_version",
        "request_id",
        "correlation_id",
        "payload",
        "error_category",
        "error_message",
        "retry_count",  # Task 9
    }
