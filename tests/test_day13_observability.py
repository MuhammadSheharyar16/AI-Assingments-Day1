"""
Day 13 Task 13 -- observability (`ToolExecutor._log_execution()`,
`src/aico/tools/executor.py`).

Mirrors `test_day06_observability.py`'s own `caplog`-based style: every
call to `ToolExecutor.execute()` emits exactly one structured JSON log
line on the same `"aico.api"` logger Day 6's `log_event()` already uses
(`aico.observability.logging`) -- "Preserve Day 6 correlation context" is
read to mean the field conventions and the logger itself, not a
parallel/second logging facility.

Proves:

  - a successful call logs `stage="tool_execution"`, `outcome="success"`,
    the resolved tool's own `tool_id`/`tool_version`/`server_alias`/
    `risk_level`, the loaded `registry_version`/`policy_version`,
    `input_validation_result`/`output_validation_result` both `"valid"`,
    `retry_count`, a non-negative `latency_ms`, and the request's own
    `request_id`/`correlation_id` -- with no `error_category`;
  - each representative failure category logs `outcome="failure"` and the
    correct `error_category`, with `input_validation_result`/
    `output_validation_result` correctly reflecting which stage the
    pipeline actually reached before failing (`"not_reached"` for a stage
    never entered, `"invalid"` for the stage that actually rejected it,
    `"valid"` for one it passed through on the way to a later failure);
  - a retried-then-successful call logs a non-zero `retry_count`;
  - `tool_id`/`server_alias`/`risk_level` are `None`/omitted-safe when no
    `ToolDefinition` was ever resolved (`tool_not_found`), never a
    fabricated value;
  - exactly one `tool_execution` event is emitted per `execute()` call --
    never zero (a silent failure path) and never more than one;
  - `request.arguments`, the transport payload/`ToolExecutionResult.
    payload`, and any raw exception/failure message text never appear in
    any logged event -- only the normalized `error_category` and the
    other safe fields above;
  - one call's `request_id`/`correlation_id` link its own `tool_execution`
    log line and nothing else (Day 6's own correlation guarantee, carried
    over unchanged).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from aico.observability.logging import API_LOGGER_NAME
from aico.tools.executor import ToolExecutionErrorCategory, ToolExecutor
from aico.tools.mcp_gateway import MCPGateway
from aico.tools.models import ToolExecutionRequest
from aico.tools.policy import ToolExecutionPolicy
from aico.tools.registry import ToolRegistry
from aico.tools.transport import FakeToolTransport

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_REGISTRY_PATH = REPO_ROOT / "tools" / "registry.v1.json"
COMMITTED_POLICY_PATH = REPO_ROOT / "policy" / "tool_execution_policy.v1.json"


def _log_payloads(caplog) -> list[dict]:
    return [json.loads(r.message) for r in caplog.records if r.name == API_LOGGER_NAME]


def _tool_execution_events(caplog) -> list[dict]:
    return [p for p in _log_payloads(caplog) if p["stage"] == "tool_execution"]


def _executor(steps) -> ToolExecutor:
    registry = ToolRegistry.load(COMMITTED_REGISTRY_PATH)
    policy = ToolExecutionPolicy.load(COMMITTED_POLICY_PATH)
    gateway = MCPGateway(FakeToolTransport(steps))
    return ToolExecutor(registry, policy, gateway)


def _lookup_request(**overrides: object) -> ToolExecutionRequest:
    fields = {
        "tool_id": "supplier_status_lookup",
        "tool_version": "1.0.0",
        "arguments": {"supplier_id": "SUP-ALPHA"},
        "trusted_permissions": ["read_structured_supplier"],
    }
    fields.update(overrides)
    return ToolExecutionRequest.model_validate(fields)


# ══════════════════════════════════════════════════════════════════════
# A successful call.
# ══════════════════════════════════════════════════════════════════════


def test_successful_call_logs_the_full_safe_metadata_set(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=API_LOGGER_NAME)
    payload = {"supplier_id": "SUP-ALPHA", "status": "active", "as_of": "2026-09-15T09:00:00Z"}
    executor = _executor([payload])
    request = _lookup_request()

    result = executor.execute(request)

    events = _tool_execution_events(caplog)
    assert len(events) == 1
    event = events[0]

    assert event["outcome"] == "success"
    assert event["tool_id"] == "supplier_status_lookup"
    assert event["tool_version"] == "1.0.0"
    assert event["server_alias"] == "synthetic-procurement-mcp"
    assert event["risk_level"] == "low"
    assert event["registry_version"] == "1.0"
    assert event["policy_version"] == "1.0"
    assert event["input_validation_result"] == "valid"
    assert event["output_validation_result"] == "valid"
    assert event["retry_count"] == 0
    assert isinstance(event["latency_ms"], (int, float))
    assert event["latency_ms"] >= 0
    assert event["request_id"] == request.request_id == result.request_id
    assert event["correlation_id"] == request.correlation_id == result.correlation_id
    assert "error_category" not in event or event["error_category"] is None


# ══════════════════════════════════════════════════════════════════════
# Failures -- each stage's own category and validation-result derivation.
# ══════════════════════════════════════════════════════════════════════


class TestFailureCategoriesLogCorrectly:
    def test_tool_not_found_has_no_tool_metadata(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger=API_LOGGER_NAME)
        executor = _executor([])
        request = _lookup_request(tool_id="model_invented_tool", tool_version="1.0.0", trusted_permissions=[])

        executor.execute(request)

        event = _tool_execution_events(caplog)[0]
        assert event["outcome"] == "failure"
        assert event["error_category"] == "tool_not_found"
        assert event["server_alias"] is None
        assert event["risk_level"] is None
        assert event["input_validation_result"] == "not_reached"
        assert event["output_validation_result"] == "not_reached"
        # registry_version/policy_version are still known -- they come
        # from the loaded documents, not the (unresolved) tool.
        assert event["registry_version"] == "1.0"
        assert event["policy_version"] == "1.0"

    def test_version_not_found_has_no_tool_metadata(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger=API_LOGGER_NAME)
        executor = _executor([])
        request = _lookup_request(tool_version="9.9.9")

        executor.execute(request)

        event = _tool_execution_events(caplog)[0]
        assert event["error_category"] == "version_not_found"
        assert event["server_alias"] is None
        assert event["input_validation_result"] == "not_reached"

    def test_tool_disabled_has_tool_metadata_but_stages_not_reached(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger=API_LOGGER_NAME)
        executor = _executor([])
        request = _lookup_request(
            tool_id="supplier_record_update",
            tool_version="1.0.0",
            arguments={"supplier_id": "SUP-ALPHA", "status": "inactive"},
            trusted_permissions=["write_structured_supplier"],
        )

        executor.execute(request)

        event = _tool_execution_events(caplog)[0]
        assert event["error_category"] == "tool_disabled"
        assert event["tool_id"] == "supplier_record_update"
        assert event["risk_level"] == "high"  # the tool WAS resolved -- only policy denied it
        assert event["input_validation_result"] == "not_reached"
        assert event["output_validation_result"] == "not_reached"

    def test_policy_denied_has_tool_metadata_but_stages_not_reached(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger=API_LOGGER_NAME)
        executor = _executor([])
        request = _lookup_request(trusted_permissions=[])

        executor.execute(request)

        event = _tool_execution_events(caplog)[0]
        assert event["error_category"] == "policy_denied"
        assert event["risk_level"] == "low"
        assert event["input_validation_result"] == "not_reached"
        assert event["output_validation_result"] == "not_reached"

    def test_input_invalid_marks_input_invalid_output_not_reached(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger=API_LOGGER_NAME)
        executor = _executor([])
        request = _lookup_request(arguments={})

        executor.execute(request)

        event = _tool_execution_events(caplog)[0]
        assert event["error_category"] == "input_invalid"
        assert event["input_validation_result"] == "invalid"
        assert event["output_validation_result"] == "not_reached"

    def test_transport_failure_marks_input_valid_output_not_reached(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger=API_LOGGER_NAME)
        executor = _executor(["transport_error"])
        request = _lookup_request()

        executor.execute(request)

        event = _tool_execution_events(caplog)[0]
        assert event["error_category"] == "transport_error"
        assert event["input_validation_result"] == "valid"
        assert event["output_validation_result"] == "not_reached"

    def test_output_invalid_marks_both_stages_reached(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger=API_LOGGER_NAME)
        executor = _executor([{"supplier_id": "SUP-ALPHA"}])  # missing status/as_of
        request = _lookup_request()

        executor.execute(request)

        event = _tool_execution_events(caplog)[0]
        assert event["error_category"] == "output_invalid"
        assert event["input_validation_result"] == "valid"
        assert event["output_validation_result"] == "invalid"


# ══════════════════════════════════════════════════════════════════════
# Retry count is logged.
# ══════════════════════════════════════════════════════════════════════


def test_retried_call_logs_its_retry_count(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=API_LOGGER_NAME)
    success_payload = {"supplier_id": "SUP-ALPHA", "status": "active", "as_of": "2026-09-15T09:00:00Z"}
    executor = _executor(["transport_unavailable", success_payload])
    request = _lookup_request()

    result = executor.execute(request)

    event = _tool_execution_events(caplog)[0]
    assert event["outcome"] == "success"
    assert event["retry_count"] == 1 == result.retry_count


# ══════════════════════════════════════════════════════════════════════
# Exactly one event per call.
# ══════════════════════════════════════════════════════════════════════


def test_exactly_one_event_is_emitted_per_call(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=API_LOGGER_NAME)
    executor = _executor([{"supplier_id": "SUP-ALPHA", "status": "active", "as_of": "2026-09-15T09:00:00Z"}])

    executor.execute(_lookup_request())

    assert len(_tool_execution_events(caplog)) == 1


def test_three_calls_emit_exactly_three_events(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=API_LOGGER_NAME)
    payload = {"supplier_id": "SUP-ALPHA", "status": "active", "as_of": "2026-09-15T09:00:00Z"}
    for _ in range(3):
        executor = _executor([payload])
        executor.execute(_lookup_request())

    assert len(_tool_execution_events(caplog)) == 3


# ══════════════════════════════════════════════════════════════════════
# No raw payload/argument/message content ever appears in a logged event.
# ══════════════════════════════════════════════════════════════════════


class TestNoRawContentLeak:
    def test_argument_values_never_appear_in_any_log_line(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger=API_LOGGER_NAME)
        secret_looking_value = "SUP-SECRET-ARGUMENT-VALUE-13579"
        executor = _executor([{"never": "reached"}])
        # Deliberately malformed (fails input schema) so the secret-looking
        # value never has a legitimate reason to appear anywhere either.
        request = _lookup_request(arguments={"supplier_id": secret_looking_value, "extra": "field"})

        executor.execute(request)

        log_text = "\n".join(json.dumps(p) for p in _log_payloads(caplog))
        assert secret_looking_value not in log_text

    def test_transport_payload_values_never_appear_in_the_success_log_line(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger=API_LOGGER_NAME)
        secret_looking_value = "2099-12-31T23:59:59Z-SECRET-MARKER"
        payload = {"supplier_id": "SUP-ALPHA", "status": "active", "as_of": secret_looking_value}
        executor = _executor([payload])

        result = executor.execute(_lookup_request())
        assert result.payload == payload  # sanity: the payload really was returned to the caller

        log_text = "\n".join(json.dumps(p) for p in _log_payloads(caplog))
        assert secret_looking_value not in log_text

    def test_raw_exception_message_never_appears_in_the_log_line(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger=API_LOGGER_NAME)
        secret_looking_detail = "INTERNAL-STACK-TRACE-DETAIL-24680-DO-NOT-LEAK"

        class _LeakyTransport:
            def execute(self, request, *, cancellation=None):
                raise RuntimeError(secret_looking_detail)

        registry = ToolRegistry.load(COMMITTED_REGISTRY_PATH)
        policy = ToolExecutionPolicy.load(COMMITTED_POLICY_PATH)
        gateway = MCPGateway(_LeakyTransport())
        executor = ToolExecutor(registry, policy, gateway)

        result = executor.execute(_lookup_request())
        assert result.error_category is ToolExecutionErrorCategory.TRANSPORT_ERROR

        log_text = "\n".join(json.dumps(p) for p in _log_payloads(caplog))
        assert secret_looking_detail not in log_text

    def test_error_message_field_itself_is_not_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Even the already-sanitized `ToolExecutionResult.error_message`
        is deliberately not part of the logged event -- only the
        normalized `error_category`."""
        caplog.set_level(logging.INFO, logger=API_LOGGER_NAME)
        executor = _executor([])
        result = executor.execute(_lookup_request(arguments={}))
        assert result.error_message  # sanity: a message does exist on the result

        event = _tool_execution_events(caplog)[0]
        assert "error_message" not in event


# ══════════════════════════════════════════════════════════════════════
# Correlation context (Day 6) is preserved.
# ══════════════════════════════════════════════════════════════════════


def test_request_id_and_correlation_id_link_only_their_own_event(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=API_LOGGER_NAME)
    payload = {"supplier_id": "SUP-ALPHA", "status": "active", "as_of": "2026-09-15T09:00:00Z"}

    executor_a = _executor([payload])
    request_a = _lookup_request(request_id="req-a", correlation_id="corr-a")
    executor_a.execute(request_a)

    executor_b = _executor([])
    request_b = _lookup_request(request_id="req-b", correlation_id="corr-b", arguments={})
    executor_b.execute(request_b)

    events = _tool_execution_events(caplog)
    assert len(events) == 2
    by_request_id = {event["request_id"]: event for event in events}

    assert by_request_id["req-a"]["correlation_id"] == "corr-a"
    assert by_request_id["req-a"]["outcome"] == "success"
    assert by_request_id["req-b"]["correlation_id"] == "corr-b"
    assert by_request_id["req-b"]["outcome"] == "failure"
