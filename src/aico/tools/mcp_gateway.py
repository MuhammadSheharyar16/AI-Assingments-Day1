"""
Day 13 Task 6 -- the MCP Gateway boundary: the one approved seam through
which a governed tool call ever reaches transport (`mcp_execution_rules.md`:
"ToolExecutionRequest -> Tool Registry -> Tool Policy -> Input Schema ->
MCP Gateway -> Transport -> ..."; Day 13 working rule: "Application/domain
code does not call an MCP transport directly").

`MCPGateway` is constructed once with an injected `ToolTransport`
(`transport.py`) -- production code would wire a real MCP client adapter
here (the assignment's own optional stretch goal); every mandatory test
wires a `FakeToolTransport`. There is exactly one way to reach transport
through this module, `MCPGateway.execute()`; nothing here exposes the
injected transport itself for a caller to reach around it.

"The gateway receives only a request already: registered, policy-approved,
input-schema-valid" is enforced here, not merely documented --
`execute()`'s own signature requires the caller to already hold a resolved
`ToolDefinition` (registered), an `ALLOW` `ToolExecutionPolicyDecision`
naming the same tool (policy-approved), and the already schema-validated
arguments dict Task 5's `validate_tool_input()` returned (input-schema-
valid, since a `ToolSchemaValidationFailure` is not a `dict[str, Any]` and
cannot be passed where one is required) -- a mismatched or non-`ALLOW`
input raises `MCPGatewayInvariantError` rather than silently proceeding to
transport. Task 7's controlled executor is expected to build every one of
these from the same resolved `(request, tool)` pair, in that order, and
never call `MCPGateway.execute()` any other way.

Every exception a transport raises is caught and normalized into a typed
`ToolTransportSuccess | ToolTransportFailure` result here -- `execute()`
never lets a raw transport exception escape to its own caller (Day 13
working rule: mirrors `aico.platform.model_gateway`'s own "the gateway
wraps anything else as a last resort so a raw exception never reaches a
caller"). `ToolTransportTimeoutError`/`ToolTransportUnavailableError`/
`ToolTransportCancelledError` (`errors.py`) map onto their own specific
`ToolTransportFailureCategory`; any other exception (a real bug, or an
unnormalized transport failure -- `transport_failure_cases.json`'s own
`"transport_error"` step) is still caught, categorized as the more general
`TRANSPORT_ERROR`, and never re-raised."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aico.tools.errors import (
    MCPGatewayInvariantError,
    ToolTransportCancelledError,
    ToolTransportTimeoutError,
    ToolTransportUnavailableError,
)
from aico.tools.models import ToolDefinition, ToolExecutionRequest, resolve_effective_tenant_scope
from aico.tools.policy import ToolExecutionPolicyDecision, ToolExecutionPolicyStatus
from aico.tools.transport import ToolCancellationToken, ToolTransport, ToolTransportRequest


class ToolTransportFailureCategory(str, Enum):
    """The closed set of transport-level failure categories `MCPGateway`
    ever returns. A narrow, transport-specific subset of Day 13's full
    normalized error taxonomy (Task 10) -- registry/policy/schema
    categories are decided one or more stages before the gateway is ever
    reached, never here."""

    TIMEOUT = "timeout"
    TRANSPORT_UNAVAILABLE = "transport_unavailable"
    TRANSPORT_ERROR = "transport_error"
    CANCELLED = "cancelled"


class ToolTransportSuccess(BaseModel):
    """A successful transport call's raw result. `payload` is untrusted
    and unvalidated -- Task 11's output schema validation, not this
    module's job, decides whether it is safe to return to a caller."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    payload: dict[str, Any] = Field(default_factory=dict)


class ToolTransportFailure(BaseModel):
    """A failed transport call, normalized into one closed category with a
    sanitized, value-free message -- never the raw exception `MCPGateway.
    execute()` actually caught."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: ToolTransportFailureCategory
    message: str = Field(min_length=1)


class MCPGateway:
    """The single approved MCP transport boundary. See module docstring."""

    def __init__(self, transport: ToolTransport) -> None:
        self._transport = transport

    def execute(
        self,
        *,
        tool: ToolDefinition,
        request: ToolExecutionRequest,
        validated_arguments: dict[str, Any],
        policy_decision: ToolExecutionPolicyDecision,
        cancellation: ToolCancellationToken | None = None,
    ) -> ToolTransportSuccess | ToolTransportFailure:
        """Execute one already-approved tool call. Never raises for an
        ordinary transport outcome -- success and every typed transport
        failure category are both normal, typed results (see module
        docstring). Only raises `MCPGatewayInvariantError` when the
        supplied inputs do not actually satisfy "registered, policy-
        approved, input-schema-valid" -- see that error's own docstring."""
        if request.key != tool.key:
            raise MCPGatewayInvariantError(
                f"request key {request.key!r} does not match resolved tool key {tool.key!r}"
            )
        if (policy_decision.tool_id, policy_decision.tool_version) != tool.key:
            raise MCPGatewayInvariantError(
                f"policy decision for {(policy_decision.tool_id, policy_decision.tool_version)!r} "
                f"does not correspond to resolved tool {tool.key!r}"
            )
        if policy_decision.decision is not ToolExecutionPolicyStatus.ALLOW:
            raise MCPGatewayInvariantError(
                f"MCPGateway.execute() called with a non-ALLOW policy decision "
                f"(reason_code={policy_decision.reason_code!r})"
            )

        transport_request = ToolTransportRequest(
            tool_id=tool.tool_id,
            tool_version=tool.tool_version,
            server_alias=tool.server_alias,
            arguments=validated_arguments,
            effective_tenant_scope=resolve_effective_tenant_scope(request),
            timeout_ms=tool.timeout_ms,
            request_id=request.request_id,
            correlation_id=request.correlation_id,
        )

        try:
            payload = self._transport.execute(transport_request, cancellation=cancellation)
        except ToolTransportTimeoutError:
            return ToolTransportFailure(category=ToolTransportFailureCategory.TIMEOUT, message="transport call timed out")
        except ToolTransportUnavailableError:
            return ToolTransportFailure(
                category=ToolTransportFailureCategory.TRANSPORT_UNAVAILABLE, message="transport server unavailable"
            )
        except ToolTransportCancelledError:
            return ToolTransportFailure(
                category=ToolTransportFailureCategory.CANCELLED, message="transport call was cancelled while in flight"
            )
        except Exception:  # noqa: BLE001 -- deliberate: transport is external/untrusted, see module docstring
            return ToolTransportFailure(
                category=ToolTransportFailureCategory.TRANSPORT_ERROR, message="transport raised an unexpected error"
            )

        return ToolTransportSuccess(payload=payload)
