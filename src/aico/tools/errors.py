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
re-derive it from a single generic error's message text.

Day 13 Task 4 -- typed Tool Execution Policy failures.
`ToolExecutionPolicyLoadError`/`ToolExecutionPolicyError` are the identical
pattern one layer over, for `ToolExecutionPolicy`
(`policy.py`) and the committed `policy/tool_execution_policy.v1.json` --
mirrors `PolicyLoadError`'s/`GateBError`'s role for Gate-B's own policy and
authorization boundary (`aico.control.errors`).

Day 13 Task 5 -- `ToolSchemaValidationFailure`. Not an exception -- a
frozen, JSON-safe *value* `schema_validator.py`'s `validate_tool_input()`
returns as an ordinary function result (`dict[str, Any] |
ToolSchemaValidationFailure`), the identical pattern
`aico.contracts.errors.ValidationFailure` already establishes for Day 4's
own parse/contract boundary (`validator.py`'s `validate_contract()`).
Carries only sanitized fields -- `category`/`field_path`/a fixed-template
`message` -- and never the raw submitted argument value itself (Day 13
working rule: "Tool arguments/results containing protected data are not
dumped into default logs"); see `schema_validator.py`'s own docstring for
why `jsonschema`'s own auto-generated messages are not used verbatim."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SchemaValidationCategory = Literal[
    "missing_field", "extra_field", "wrong_type", "invalid_enum", "invalid_constraint", "other"
]

SCHEMA_VALIDATION_CATEGORIES: tuple[SchemaValidationCategory, ...] = (
    "missing_field",
    "extra_field",
    "wrong_type",
    "invalid_enum",
    "invalid_constraint",
    "other",
)


@dataclass(frozen=True)
class ToolSchemaValidationFailure:
    """A typed, safe-to-log schema-validation failure. `field_path` is a
    dotted path into the validated payload (e.g. `"supplier_id"`),
    `None` when the failure isn't about one specific field (e.g. more than
    one additional property, or a failure at the payload's own top
    level)."""

    category: SchemaValidationCategory
    message: str
    field_path: str | None = None

    def __str__(self) -> str:  # safe by construction -- see module docstring
        where = f" at {self.field_path}" if self.field_path else ""
        return f"[{self.category}]{where} {self.message}"


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


class ToolExecutionPolicyError(Exception):
    """Base class for every typed failure `ToolExecutionPolicy` raises.
    Never raised directly -- see `ToolExecutionPolicyLoadError` /
    `ToolExecutionPolicyInvariantError`."""


class ToolExecutionPolicyLoadError(ToolExecutionPolicyError):
    """Raised by `ToolExecutionPolicy.load()` (Task 4) when the committed
    `policy/tool_execution_policy.v1.json` cannot be read, is not valid
    JSON, or fails `ToolExecutionPolicyDocument`'s typed validation
    (duplicate `rule_id`, more than one rule governing the same
    `(tool_id, tool_version)` pair, an invalid semantic version, an
    invalid `status`/`max_risk_level` enum, a `default_decision` other than
    `deny`, ...). Carries a single sanitized message describing the
    problem; there is never a silent fallback to an empty/default/
    permissive policy."""


class ToolExecutionPolicyInvariantError(ToolExecutionPolicyError):
    """Raised by `ToolExecutionPolicy.authorize()` (Task 4) when it is
    given inputs that violate an invariant it depends on -- specifically, a
    `ToolExecutionRequest` and a `ToolDefinition` naming different
    `(tool_id, tool_version)` pairs. Task 7's controlled executor never
    produces such a mismatch by construction (it always resolves the
    `ToolDefinition` from the registry using the request's own `key`
    immediately before calling `authorize()`); this exists to fail loudly
    rather than silently authorize the wrong tool if some other caller
    ever passes a mismatched pair by hand -- the identical role `GateBError`
    plays for Gate-B (`aico.control.errors`). Never raised for an ordinary
    authorization outcome: allow and deny are both normal, typed
    `ToolExecutionPolicyDecision` results, not exceptions."""


class ToolTransportError(Exception):
    """Base class for every typed failure a `ToolTransport` implementation
    (`transport.py`, Task 6) may raise for a known, normalizable
    transport-level condition -- mirrors `aico.platform.model_gateway`'s
    own `Transport` contract: "a transport is expected to raise normalized
    failures; the gateway wraps anything else as a last resort so a raw
    exception never reaches a caller." Never raised directly -- see
    `ToolTransportTimeoutError` / `ToolTransportUnavailableError` /
    `ToolTransportCancelledError`. Any *other* exception a transport raises
    (unnormalized -- a real bug, or a condition this taxonomy does not yet
    name) is still caught by `MCPGateway.execute()`, just categorized as
    the more general `transport_error` rather than one of these three."""


class ToolTransportTimeoutError(ToolTransportError):
    """Raised by a `ToolTransport` implementation when a call did not
    complete within its own understanding of the tool's timeout budget.
    `MCPGateway.execute()` (Task 6) catches this and returns a typed
    `ToolTransportFailure(category=TIMEOUT)`, never letting it escape as a
    raw exception."""


class ToolTransportUnavailableError(ToolTransportError):
    """Raised by a `ToolTransport` implementation for a transient
    "server unreachable" condition -- the one category (alongside
    `timeout`) `RetryPolicy.retryable_categories` (Task 1) may ever name.
    `MCPGateway.execute()` catches this and returns a typed
    `ToolTransportFailure(category=TRANSPORT_UNAVAILABLE)`."""


class ToolTransportCancelledError(ToolTransportError):
    """Raised by a `ToolTransport` implementation when it observes its own
    `ToolCancellationToken` set while a call was in flight.
    `MCPGateway.execute()` catches this and returns a typed
    `ToolTransportFailure(category=CANCELLED)`."""


class MCPGatewayError(Exception):
    """Base class for every typed failure `MCPGateway` raises. Never
    raised directly -- see `MCPGatewayInvariantError`."""


class MCPGatewayInvariantError(MCPGatewayError):
    """Raised by `MCPGateway.execute()` (Task 6) when it is given inputs
    that violate an invariant it depends on: a `ToolExecutionRequest`/
    `ToolDefinition` naming different `(tool_id, tool_version)` pairs, a
    `ToolExecutionPolicyDecision` that does not correspond to the same
    resolved tool, or one whose `decision` is not `ALLOW` (Day 13 working
    rule: "The gateway receives only a request already: registered,
    policy-approved, input-schema-valid" -- this is that precondition,
    enforced rather than merely documented). Task 7's controlled executor
    never produces such a mismatch by construction, since it always builds
    the policy decision from the same resolved `(request, tool)` pair
    immediately before calling `MCPGateway.execute()`; this exists to fail
    loudly rather than silently invoke transport for a request that was
    never actually approved -- the identical role
    `ToolExecutionPolicyInvariantError` plays one stage earlier. Never
    raised for an ordinary transport outcome: success and every typed
    transport failure category are both normal, typed
    `ToolTransportSuccess`/`ToolTransportFailure` results, not
    exceptions."""
