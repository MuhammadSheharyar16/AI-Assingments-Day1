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
"""
from aico.tools.errors import (
    ToolNotFoundError,
    ToolRegistryError,
    ToolRegistryLoadError,
    ToolVersionNotFoundError,
)
from aico.tools.models import (
    RetryableFailureCategory,
    RetryPolicy,
    RiskLevel,
    ToolDefinition,
    ToolRegistryDocument,
    ToolStatus,
    ToolTransportKind,
)
from aico.tools.registry import DEFAULT_REGISTRY_PATH, ToolRegistry

__all__ = [
    "RetryPolicy",
    "RetryableFailureCategory",
    "RiskLevel",
    "ToolDefinition",
    "ToolRegistryDocument",
    "ToolStatus",
    "ToolTransportKind",
    "ToolRegistry",
    "DEFAULT_REGISTRY_PATH",
    "ToolRegistryError",
    "ToolRegistryLoadError",
    "ToolNotFoundError",
    "ToolVersionNotFoundError",
]
