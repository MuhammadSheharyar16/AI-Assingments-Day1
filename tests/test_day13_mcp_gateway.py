"""
Day 13 Task 6 -- the MCP Gateway boundary (`MCPGateway`,
`src/aico/tools/mcp_gateway.py`) and the injected transport seam
(`ToolTransport`/`ToolCancellationToken`/`FakeToolTransport`,
`src/aico/tools/transport.py`).

Proves, wiring together every earlier task against the real committed
registry/policy:

  - a full approved call reaches `FakeToolTransport` exactly once and
    returns a typed `ToolTransportSuccess` (`execution_cases.json`
    EXEC13-001's own shape);
  - each `ToolTransportTimeoutError`/`ToolTransportUnavailableError`/
    `ToolTransportCancelledError`/unnormalized exception a transport
    raises is caught and normalized into its own
    `ToolTransportFailureCategory`, never escaping as a raw exception
    (`transport_failure_cases.json`'s own step vocabulary);
  - `MCPGatewayInvariantError` for every way the gateway's own
    precondition ("registered, policy-approved, input-schema-valid") can
    be violated: mismatched request/tool keys, a policy decision for a
    different tool, a non-`ALLOW` decision;
  - `FakeToolTransport.call_count` -- the concrete, observable proof a
    caller that never reaches `MCPGateway.execute()` at all (a denied/
    disabled/invalid-input case, decided one or more stages earlier) makes
    zero transport calls;
  - tenant scope is injected into the transport request from
    `resolve_effective_tenant_scope(request)`, never derived from
    `arguments`;
  - the structural half of "no model/RAG module may call transport
    directly": nothing outside `src/aico/tools/` imports
    `aico.tools.transport` at all.

Task 7 (the controlled executor that actually orchestrates registry ->
policy -> schema -> gateway for a raw `ToolExecutionRequest`) is out of
scope here -- this file wires the pieces together by hand to prove the
gateway/transport boundary itself. Task 8 (timeout/cancellation
enforcement) and Task 9 (retry) build on `FakeToolTransport`'s
`wait_until_cancelled`/failure-category vocabulary but their own
enforcement logic is not implemented yet.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from aico.tools.errors import MCPGatewayInvariantError
from aico.tools.mcp_gateway import MCPGateway, ToolTransportFailure, ToolTransportFailureCategory, ToolTransportSuccess
from aico.tools.models import ToolDefinition, ToolExecutionRequest
from aico.tools.policy import ToolExecutionPolicy, ToolExecutionPolicyDecision, ToolExecutionPolicyStatus
from aico.tools.registry import ToolRegistry
from aico.tools.schema_validator import validate_tool_input
from aico.tools.transport import FakeToolTransport, ToolCancellationToken, ToolTransportRequest

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_REGISTRY_PATH = REPO_ROOT / "tools" / "registry.v1.json"
COMMITTED_POLICY_PATH = REPO_ROOT / "policy" / "tool_execution_policy.v1.json"


def _lookup_tool() -> ToolDefinition:
    return ToolRegistry.load(COMMITTED_REGISTRY_PATH).get_tool("supplier_status_lookup", "1.0.0")


def _update_tool() -> ToolDefinition:
    return ToolRegistry.load(COMMITTED_REGISTRY_PATH).get_tool("supplier_record_update", "1.0.0")


def _allow_decision_for(tool: ToolDefinition, request: ToolExecutionRequest) -> ToolExecutionPolicyDecision:
    policy = ToolExecutionPolicy.load(COMMITTED_POLICY_PATH)
    decision = policy.authorize(request, tool)
    assert decision.decision is ToolExecutionPolicyStatus.ALLOW  # sanity for tests that need a real ALLOW
    return decision


def _lookup_request(**overrides: object) -> ToolExecutionRequest:
    fields = {
        "tool_id": "supplier_status_lookup",
        "tool_version": "1.0.0",
        "arguments": {"supplier_id": "SUP-ALPHA"},
        "trusted_permissions": ["read_structured_supplier"],
        "effective_tenant_scope": ["TENANT-A"],
    }
    fields.update(overrides)
    return ToolExecutionRequest.model_validate(fields)


# ══════════════════════════════════════════════════════════════════════
# A full approved call reaches transport exactly once.
# ══════════════════════════════════════════════════════════════════════


class TestSuccessfulExecution:
    def test_allowed_call_reaches_transport_once_and_returns_success(self) -> None:
        tool = _lookup_tool()
        request = _lookup_request()
        decision = _allow_decision_for(tool, request)
        validated = validate_tool_input(tool, request.arguments)
        assert isinstance(validated, dict)

        payload = {"supplier_id": "SUP-ALPHA", "status": "active", "as_of": "2026-09-15T09:00:00Z"}
        transport = FakeToolTransport([payload])
        gateway = MCPGateway(transport)

        result = gateway.execute(tool=tool, request=request, validated_arguments=validated, policy_decision=decision)

        assert isinstance(result, ToolTransportSuccess)
        assert result.payload == payload
        assert transport.call_count == 1

    def test_effective_tenant_scope_is_injected_from_request_not_arguments(self) -> None:
        """Task 3's own trust rule, proven once more at the gateway: tenant
        context reaches transport from `resolve_effective_tenant_scope()`,
        never from `arguments`. Uses a hand-built `validated_arguments`
        dict carrying a `tenant_id` key directly (bypassing schema
        validation, which the real schema's own `additionalProperties:
        false` would reject long before the gateway is ever reached --
        this test targets `MCPGateway.execute()`'s own tenant-injection
        behavior in isolation, not the full pipeline)."""
        tool = _lookup_tool()
        request = _lookup_request(effective_tenant_scope=["TENANT-A"])
        decision = _allow_decision_for(tool, request)
        validated = {"supplier_id": "SUP-ALPHA", "tenant_id": "TENANT-INJECTED"}

        captured: list[ToolTransportRequest] = []

        class _CapturingTransport:
            def execute(self, transport_request, *, cancellation=None):
                captured.append(transport_request)
                return {"supplier_id": "SUP-ALPHA", "status": "active", "as_of": "2026-09-15T09:00:00Z"}

        gateway = MCPGateway(_CapturingTransport())
        gateway.execute(tool=tool, request=request, validated_arguments=validated, policy_decision=decision)

        assert len(captured) == 1
        # The trusted, injected scope is exactly what the request declared --
        # never widened or replaced by whatever `arguments["tenant_id"]" says.
        assert captured[0].effective_tenant_scope == ("TENANT-A",)
        assert captured[0].effective_tenant_scope != ("TENANT-INJECTED",)
        # `arguments` itself is passed through unexamined -- the gateway
        # does not strip or specially treat a `tenant_id`-shaped key there,
        # it simply never *reads* one for tenant scope.
        assert captured[0].arguments.get("tenant_id") == "TENANT-INJECTED"


# ══════════════════════════════════════════════════════════════════════
# Transport failure normalization.
# ══════════════════════════════════════════════════════════════════════


class TestTransportFailureNormalization:
    def _execute_with_steps(self, steps, *, cancellation: ToolCancellationToken | None = None):
        tool = _lookup_tool()
        request = _lookup_request()
        decision = _allow_decision_for(tool, request)
        validated = validate_tool_input(tool, request.arguments)
        transport = FakeToolTransport(steps)
        gateway = MCPGateway(transport)
        result = gateway.execute(
            tool=tool, request=request, validated_arguments=validated, policy_decision=decision, cancellation=cancellation
        )
        return result, transport

    def test_timeout_step_normalizes_to_timeout_category(self) -> None:
        result, transport = self._execute_with_steps(["timeout"])
        assert isinstance(result, ToolTransportFailure)
        assert result.category is ToolTransportFailureCategory.TIMEOUT
        assert transport.call_count == 1

    def test_transport_unavailable_step_normalizes(self) -> None:
        result, transport = self._execute_with_steps(["transport_unavailable"])
        assert isinstance(result, ToolTransportFailure)
        assert result.category is ToolTransportFailureCategory.TRANSPORT_UNAVAILABLE

    def test_unnormalized_transport_error_is_caught_and_normalized(self) -> None:
        """`transport_failure_cases.json`'s own `"transport_error"` step:
        a raw, unnormalized exception a transport raises must still never
        escape `MCPGateway.execute()` as itself."""
        result, transport = self._execute_with_steps(["transport_error"])
        assert isinstance(result, ToolTransportFailure)
        assert result.category is ToolTransportFailureCategory.TRANSPORT_ERROR

    def test_cancelled_step_normalizes_to_cancelled_category(self) -> None:
        """`transport_failure_cases.json` TR13-004's own shape: a transport
        call blocked on `wait_until_cancelled`, unblocked by cancelling
        the shared token from outside."""
        import threading

        token = ToolCancellationToken()

        def _cancel_shortly() -> None:
            import time

            time.sleep(0.05)
            token.cancel()

        threading.Thread(target=_cancel_shortly, daemon=True).start()

        result, transport = self._execute_with_steps(["wait_until_cancelled"], cancellation=token)
        assert isinstance(result, ToolTransportFailure)
        assert result.category is ToolTransportFailureCategory.CANCELLED

    def test_no_raw_exception_ever_escapes_execute(self) -> None:
        """Structural proof across every known step: `execute()` itself
        never raises for a transport outcome, only for the invariant
        precondition (`MCPGatewayInvariantError`, covered separately)."""
        for step in ("timeout", "transport_unavailable", "transport_error"):
            result, _ = self._execute_with_steps([step])
            assert isinstance(result, ToolTransportFailure)


# ══════════════════════════════════════════════════════════════════════
# `MCPGatewayInvariantError` -- the gateway's own enforced precondition.
# ══════════════════════════════════════════════════════════════════════


class TestGatewayInvariant:
    def test_mismatched_request_and_tool_raises(self) -> None:
        tool = _lookup_tool()
        request = _lookup_request()
        decision = _allow_decision_for(tool, request)
        mismatched_request = _lookup_request(tool_id="supplier_record_update", tool_version="1.0.0")

        gateway = MCPGateway(FakeToolTransport([]))
        with pytest.raises(MCPGatewayInvariantError):
            gateway.execute(
                tool=tool, request=mismatched_request, validated_arguments={}, policy_decision=decision
            )

    def test_policy_decision_for_a_different_tool_raises(self) -> None:
        tool = _lookup_tool()
        request = _lookup_request()
        other_tool = _update_tool()
        other_request = _lookup_request(
            tool_id="supplier_record_update",
            tool_version="1.0.0",
            arguments={"supplier_id": "SUP-ALPHA", "status": "inactive"},
            trusted_permissions=["write_structured_supplier"],
        )
        mismatched_decision = ToolExecutionPolicy.load(COMMITTED_POLICY_PATH).authorize(other_request, other_tool)

        gateway = MCPGateway(FakeToolTransport([]))
        with pytest.raises(MCPGatewayInvariantError):
            gateway.execute(tool=tool, request=request, validated_arguments={}, policy_decision=mismatched_decision)

    def test_non_allow_decision_raises(self) -> None:
        """A caller must never invoke the gateway with a `DENY` decision --
        this is a precondition violation, not a normal deny outcome."""
        tool = _lookup_tool()
        request = _lookup_request(trusted_permissions=[])
        deny_decision = ToolExecutionPolicy.load(COMMITTED_POLICY_PATH).authorize(request, tool)
        assert deny_decision.decision is ToolExecutionPolicyStatus.DENY

        gateway = MCPGateway(FakeToolTransport([]))
        with pytest.raises(MCPGatewayInvariantError):
            gateway.execute(tool=tool, request=request, validated_arguments={}, policy_decision=deny_decision)

    def test_invariant_violation_makes_zero_transport_calls(self) -> None:
        tool = _lookup_tool()
        request = _lookup_request(trusted_permissions=[])
        deny_decision = ToolExecutionPolicy.load(COMMITTED_POLICY_PATH).authorize(request, tool)
        transport = FakeToolTransport([{"never": "reached"}])
        gateway = MCPGateway(transport)

        with pytest.raises(MCPGatewayInvariantError):
            gateway.execute(tool=tool, request=request, validated_arguments={}, policy_decision=deny_decision)

        assert transport.call_count == 0


# ══════════════════════════════════════════════════════════════════════
# `FakeToolTransport.call_count` -- the concrete zero-transport-calls proof.
# ══════════════════════════════════════════════════════════════════════


class TestFakeTransportCallCount:
    def test_unused_transport_has_zero_calls(self) -> None:
        transport = FakeToolTransport([{"ok": True}])
        assert transport.call_count == 0

    def test_call_count_increments_even_on_failure(self) -> None:
        transport = FakeToolTransport(["timeout"])
        gateway = MCPGateway(transport)
        tool = _lookup_tool()
        request = _lookup_request()
        decision = _allow_decision_for(tool, request)
        validated = validate_tool_input(tool, request.arguments)
        assert isinstance(validated, dict)

        gateway.execute(tool=tool, request=request, validated_arguments=validated, policy_decision=decision)
        assert transport.call_count == 1

    def test_exhausted_steps_raise_a_clear_misconfiguration_error(self) -> None:
        transport = FakeToolTransport([])
        request = ToolTransportRequest(
            tool_id="supplier_status_lookup",
            tool_version="1.0.0",
            server_alias="synthetic-procurement-mcp",
            arguments={"supplier_id": "SUP-ALPHA"},
            timeout_ms=900,
            request_id="req-1",
            correlation_id="corr-1",
        )
        with pytest.raises(RuntimeError, match="no more configured steps"):
            transport.execute(request)
        assert transport.call_count == 1

    def test_unrecognized_step_raises(self) -> None:
        transport = FakeToolTransport(["not_a_real_step"])
        request = ToolTransportRequest(
            tool_id="supplier_status_lookup",
            tool_version="1.0.0",
            server_alias="synthetic-procurement-mcp",
            arguments={},
            timeout_ms=900,
            request_id="req-1",
            correlation_id="corr-1",
        )
        with pytest.raises(ValueError, match="unrecognized fake transport step"):
            transport.execute(request)

    def test_wait_until_cancelled_without_token_raises_immediately(self) -> None:
        transport = FakeToolTransport(["wait_until_cancelled"])
        request = ToolTransportRequest(
            tool_id="supplier_status_lookup",
            tool_version="1.0.0",
            server_alias="synthetic-procurement-mcp",
            arguments={},
            timeout_ms=900,
            request_id="req-1",
            correlation_id="corr-1",
        )
        with pytest.raises(RuntimeError, match="requires a cancellation token"):
            transport.execute(request)


# ══════════════════════════════════════════════════════════════════════
# "No model/RAG module may call transport directly" -- structural proof.
# ══════════════════════════════════════════════════════════════════════


def test_no_module_outside_aico_tools_imports_transport() -> None:
    src_root = REPO_ROOT / "src" / "aico"
    tools_dir = src_root / "tools"

    offending: list[str] = []
    for path in src_root.rglob("*.py"):
        if tools_dir in path.parents or path.parent == tools_dir:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "aico.tools.transport" in node.module:
                offending.append(str(path))
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "aico.tools.transport" in alias.name:
                        offending.append(str(path))

    assert offending == []


def test_gateway_module_is_the_only_importer_of_transport_within_aico_tools() -> None:
    """Within `aico.tools` itself, only `mcp_gateway.py` (and, naturally,
    `transport.py`'s own tests) ever imports the transport module -- the
    registry/policy/schema layers stay transport-agnostic."""
    tools_dir = REPO_ROOT / "src" / "aico" / "tools"
    importers: list[str] = []
    for path in tools_dir.glob("*.py"):
        if path.name in ("transport.py", "__init__.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "aico.tools.transport":
                importers.append(path.name)

    assert importers == ["mcp_gateway.py"]
