"""
Day 13 governed tool boundary. `src/aico/tools/` is where a
`ToolExecutionRequest` is resolved against the registered, versioned,
schema-valid, policy-approved tool it names, before the MCP Gateway is
ever allowed to invoke transport (`Day 13 Task.pdf`: "A model never
receives a raw capability to execute arbitrary tools").

Task 1 exports the typed Tool Registry model (`models.py`): a governed
tool definition is `ToolDefinition` (`tool_id` / `tool_version` / `owner`
/ `status` / `transport` / `server_alias` / `risk_level` /
`side_effecting` / `idempotent` / `timeout_ms` / `retry_policy` /
`required_permissions` / `input_schema` / `output_schema`), and
`ToolRegistryDocument` is the versioned collection of them -- both
self-validating against every rule in `tool_registry_requirements.md`.

Task 2 adds `ToolRegistry` (`registry.py`), the read-only, typed loader/
lookup service the rest of this boundary is built against -- loading the
committed `tools/registry.v1.json` through Task 1's types, exact
`(tool_id, tool_version)` lookup, active/disabled state, and a typed
`tool_not_found`/`version_not_found` failure distinction (`errors.py`) for
anything this document does not govern.

Task 3 adds `ToolExecutionRequest` (`models.py`) -- the typed, frozen
shape every tool invocation enters this pipeline as (`request_id` /
`correlation_id` / `tool_id` / `tool_version` / `arguments` /
`trusted_permissions` / `effective_tenant_scope` / `idempotency_key` /
`execution_context`) -- plus `resolve_trusted_permissions()` /
`resolve_effective_tenant_scope()`, the only two functions that ever read
a request's authorization context; neither ever consults `arguments`
(Day 13 working rule: "arguments.role cannot grant permission").

Task 4 adds `ToolExecutionPolicy` (`policy.py`) -- the deterministic,
default-deny decision engine: loads the committed
`policy/tool_execution_policy.v1.json` through
`ToolExecutionPolicyDocument`/`ToolExecutionPolicyRule`, and
`authorize(request, tool)` decides `allow`/`deny` from registered tool/
version, active/disabled status (the tool's own and the rule's), required
trusted permission, approved server alias, risk-level ceiling, and
side-effecting/idempotent retry safety -- returning a typed
`ToolExecutionPolicyDecision`, never a fall-through to allow.

Task 5 adds `validate_tool_input()` (`schema_validator.py`) -- the one
place a request's `arguments` is ever checked against its resolved tool's
registered `input_schema`, returning either the arguments dict or a typed
`ToolSchemaValidationFailure` (`errors.py`); never a bare `jsonschema`
exception, never a value in that failure that echoes the raw submitted
argument (only schema-declared property names/categories).

Task 6 adds `MCPGateway` (`mcp_gateway.py`) -- the single approved MCP
transport boundary, and `ToolTransport`/`ToolCancellationToken`/
`FakeToolTransport` (`transport.py`) -- the injected transport seam and
the deterministic, in-process double every mandatory test is built
against. `MCPGateway.execute()` requires an already-registered
`ToolDefinition`, an `ALLOW` `ToolExecutionPolicyDecision`, and already
schema-validated arguments (raising `MCPGatewayInvariantError` otherwise),
and normalizes every transport exception into a typed
`ToolTransportSuccess`/`ToolTransportFailure` -- never a raw exception,
never a fall-through to transport for an unapproved request.
"""
from aico.tools.errors import (
    SCHEMA_VALIDATION_CATEGORIES,
    MCPGatewayError,
    MCPGatewayInvariantError,
    ToolExecutionPolicyError,
    ToolExecutionPolicyInvariantError,
    ToolExecutionPolicyLoadError,
    ToolNotFoundError,
    ToolRegistryError,
    ToolRegistryLoadError,
    ToolSchemaValidationFailure,
    ToolTransportCancelledError,
    ToolTransportError,
    ToolTransportTimeoutError,
    ToolTransportUnavailableError,
    ToolVersionNotFoundError,
)
from aico.tools.mcp_gateway import MCPGateway, ToolTransportFailure, ToolTransportFailureCategory, ToolTransportSuccess
from aico.tools.models import (
    RetryableFailureCategory,
    RetryPolicy,
    RiskLevel,
    ToolDefinition,
    ToolExecutionRequest,
    ToolRegistryDocument,
    ToolStatus,
    ToolTransportKind,
    resolve_effective_tenant_scope,
    resolve_trusted_permissions,
)
from aico.tools.policy import (
    DEFAULT_TOOL_EXECUTION_POLICY_PATH,
    ToolExecutionPolicy,
    ToolExecutionPolicyDecision,
    ToolExecutionPolicyDocument,
    ToolExecutionPolicyRule,
    ToolExecutionPolicyStatus,
)
from aico.tools.registry import DEFAULT_REGISTRY_PATH, ToolRegistry
from aico.tools.schema_validator import validate_tool_input
from aico.tools.transport import FakeToolTransport, ToolCancellationToken, ToolTransport, ToolTransportRequest

__all__ = [
    "RetryPolicy",
    "RetryableFailureCategory",
    "RiskLevel",
    "ToolDefinition",
    "ToolRegistryDocument",
    "ToolStatus",
    "ToolTransportKind",
    "ToolExecutionRequest",
    "resolve_trusted_permissions",
    "resolve_effective_tenant_scope",
    "ToolRegistry",
    "DEFAULT_REGISTRY_PATH",
    "ToolRegistryError",
    "ToolRegistryLoadError",
    "ToolNotFoundError",
    "ToolVersionNotFoundError",
    "ToolExecutionPolicy",
    "ToolExecutionPolicyDocument",
    "ToolExecutionPolicyRule",
    "ToolExecutionPolicyDecision",
    "ToolExecutionPolicyStatus",
    "DEFAULT_TOOL_EXECUTION_POLICY_PATH",
    "ToolExecutionPolicyError",
    "ToolExecutionPolicyLoadError",
    "ToolExecutionPolicyInvariantError",
    "validate_tool_input",
    "ToolSchemaValidationFailure",
    "SCHEMA_VALIDATION_CATEGORIES",
    "MCPGateway",
    "ToolTransportSuccess",
    "ToolTransportFailure",
    "ToolTransportFailureCategory",
    "MCPGatewayError",
    "MCPGatewayInvariantError",
    "ToolTransport",
    "ToolTransportRequest",
    "ToolCancellationToken",
    "FakeToolTransport",
    "ToolTransportError",
    "ToolTransportTimeoutError",
    "ToolTransportUnavailableError",
    "ToolTransportCancelledError",
]
