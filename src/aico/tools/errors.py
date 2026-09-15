"""
Day 13 Task 2 -- typed Tool Registry failures.

`ToolRegistryLoadError`/`ToolNotFoundError`/`ToolVersionNotFoundError` are
the identical pattern `OntologyLoadError`/`OntologyLookupError`
(`aico.control.errors`) already establishes for the ontology boundary: a
caller distinguishes "the committed registry file itself is missing/
invalid" from "a caller asked this registry to resolve a tool/version it
does not govern" with one `except ToolRegistryLoadError` /
`except (ToolNotFoundError, ToolVersionNotFoundError)`, never by inspecting
Pydantic's own `ValidationError` shape or treating a `KeyError` as a
control-flow signal.

`ToolNotFoundError` and `ToolVersionNotFoundError` are kept as two
distinct exception types, not one parameterized lookup error, because
Day 13's own normalized error taxonomy (Task 10) treats them as two
distinct categories -- `tool_not_found` (no such `tool_id` at all) and
`version_not_found` (the `tool_id` is registered, but not at this exact
`tool_version`) -- and this module is where that distinction is first
drawn, at the registry boundary itself, so nothing downstream has to
re-derive it from a single generic error's message text."""
from __future__ import annotations


class ToolRegistryError(Exception):
    """Base class for every typed failure `ToolRegistry` raises. Never
    raised directly -- see `ToolRegistryLoadError` / `ToolNotFoundError` /
    `ToolVersionNotFoundError`."""


class ToolRegistryLoadError(ToolRegistryError):
    """Raised by `ToolRegistry.load()` (Task 2) when the committed
    `tools/registry.v1.json` cannot be read, is not valid JSON, or fails
    `ToolRegistryDocument`'s typed validation (Task 1: duplicate tool/
    version, missing owner, invalid semantic version, unknown status/
    transport, invalid timeout/retry metadata, invalid/missing schema, an
    unsafe retry configuration on a side-effecting/non-idempotent tool,
    ...). Carries a single sanitized message describing the problem --
    callers reason about "the tool registry failed to load", never about
    Pydantic's own exception shape, and there is never a silent fallback
    to an empty/default registry."""


class ToolNotFoundError(ToolRegistryError):
    """Raised by `ToolRegistry.get_tool()` / `versions_for()` /
    `is_active()` when `tool_id` names no registered tool at all --
    Day 13's own `tool_not_found` normalized error category (Task 10).
    A model-invented tool name (`execution_cases.json` EXEC13-006:
    `model_invented_tool`) fails here -- fail closed, never a synthesized
    default definition."""

    def __init__(self, tool_id: str):
        self.tool_id = tool_id
        super().__init__(f"unknown tool_id: {tool_id!r}")


class ToolVersionNotFoundError(ToolRegistryError):
    """Raised by `ToolRegistry.get_tool()` / `is_active()` when `tool_id`
    is registered but `tool_version` is not one of its registered
    versions -- Day 13's own `version_not_found` normalized error
    category (Task 10). Distinct from `ToolNotFoundError`: "this tool
    exists, but not at the exact version requested" (Day 13 working rule:
    "Tool version is explicit") is a different, more specific failure
    than "this tool does not exist at all", and a caller may reasonably
    want to react to the two differently (e.g. surfacing which versions
    *are* available)."""

    def __init__(self, tool_id: str, tool_version: str):
        self.tool_id = tool_id
        self.tool_version = tool_version
        super().__init__(f"tool {tool_id!r} has no registered version {tool_version!r}")
