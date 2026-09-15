"""
Day 13 Task 1 -- typed Tool Registry model.

`tool_registry_requirements.md` (`day13_pack/`) names the field list every
executable tool must define and the checks a registry document must fail
closed on. `ToolDefinition` below matches that field list one-for-one, and
`ToolRegistryDocument` is the versioned collection Task 2's `registry.py`
will load `tools/registry.v1.json` through. Every "Reject" bullet from
`tool_registry_requirements.md` is enforced *inside* these types, the same
"unchecked dictionaries are not acceptable at this boundary" reading
`policy_models.py` already gives Gate-B/Gate-D's own policy documents:

    - duplicate tool/version rejected      -> `ToolRegistryDocument`'s
                                               `model_validator` below.
    - missing owner rejected               -> `ToolDefinition.owner` is a
                                               required, non-empty field.
    - invalid version rejected             -> `ToolDefinition.tool_version`
                                               is checked against a strict
                                               `MAJOR.MINOR.PATCH` pattern.
    - unknown status rejected              -> `ToolStatus` (closed enum);
                                               an unrecognized status string
                                               cannot even parse.
    - unknown transport rejected           -> `ToolTransportKind` (closed
                                               enum, `mcp` only today --
                                               "one approved tool-transport
                                               boundary", `mcp_execution_
                                               rules.md`); an unrecognized
                                               transport string cannot even
                                               parse.
    - invalid timeout/retry metadata
      rejected                            -> `ToolDefinition.timeout_ms` is
                                               `Field(gt=0)`; `RetryPolicy.
                                               max_attempts` is bounded, and
                                               `retryable_categories` is
                                               drawn from the closed,
                                               genuinely-transient
                                               `RetryableFailureCategory`
                                               set -- a policy-denial or
                                               input-validation failure can
                                               never be declared "retryable"
                                               at the type level.
    - invalid/missing schema rejected      -> `ToolDefinition.input_schema`/
                                               `output_schema` are checked
                                               for basic JSON-Schema-object
                                               shape (`_validate_object_
                                               schema_shape`) -- present,
                                               `type: object`, well-formed
                                               `properties`/`required`/
                                               `additionalProperties`. Full
                                               argument/payload validation
                                               against these schemas is
                                               Task 5's `schema_validator.py`
                                               job, not this module's; this
                                               module only guarantees a
                                               registered tool's schemas are
                                               themselves structurally sane
                                               before anything is ever
                                               validated against them.
    - unsafe retry configuration for a
      side-effecting/non-idempotent tool
      rejected                            -> `ToolDefinition`'s own
                                               `model_validator` below: a
                                               tool that is `side_effecting`
                                               or not `idempotent` may not
                                               declare `retry_policy.
                                               max_attempts > 1` or any
                                               `retryable_categories` at
                                               all. Fixture
                                               `registry_validation_cases.
                                               json` REG13-005 (a
                                               non-idempotent side-effect
                                               tool declaring
                                               `max_attempts=3`) is rejected
                                               here, satisfying that case's
                                               `invalid_or_policy_rejected`
                                               expectation at the registry
                                               boundary itself (Task 4's
                                               policy is a second,
                                               independent layer over the
                                               *disabled* `supplier_record_
                                               update` tool specifically,
                                               not a substitute for this
                                               check).
    - registry version required            -> `ToolRegistryDocument.
                                               registry_version` is a
                                               required, non-empty field.

`ToolDefinition`/`ToolRegistryDocument` are plain immutable-by-convention
Pydantic values -- this module only defines and self-validates the shape;
Task 2's `registry.py` is what guarantees no runtime request/model output
can register a new tool or mutate a loaded one.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


class ToolStatus(str, Enum):
    """The closed set of governed tool lifecycle statuses
    (`tool_registry_requirements.md`: "unknown status rejected"). `ACTIVE`
    is the only status the supplied `supplier_status_lookup` tool uses;
    `DISABLED` is the supplied `supplier_record_update` lab tool's status
    (`mcp_execution_rules.md`: "Disabled tool makes zero transport calls").
    `DEPRECATED` exists so a later registry version can wind a tool down
    without deleting its id out from under anything that still references
    it -- the same allowance `LifecycleStatus` (`aico.control.ontology`)
    gives a domain/concept/intent."""

    ACTIVE = "active"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"


class ToolTransportKind(str, Enum):
    """The closed set of governed tool transports. `MCP` is the only value
    today -- Day 13's own build outcome names one approved tool-transport
    boundary (`mcp_execution_rules.md`: "Application/model code never
    calls transport directly"; the MCP Gateway, Task 6). A registry entry
    naming any other transport cannot even parse, which is this module's
    reading of `tool_registry_requirements.md`'s "unknown ... transport
    rejected" bullet."""

    MCP = "mcp"


class RiskLevel(str, Enum):
    """The closed set of governed tool risk levels
    (`tool_registry_v1.json` uses `low`/`high`; `MEDIUM` exists for a
    tool whose risk sits between the two, the same headroom `ToolStatus.
    DEPRECATED` gives lifecycle status)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RetryableFailureCategory(str, Enum):
    """The closed set of transport failure categories a tool's own
    `RetryPolicy.retryable_categories` may name. Deliberately a narrow
    subset of Task 10's full normalized-error taxonomy -- only genuinely
    transient transport conditions belong here; `policy_denied`/
    `input_invalid`/`tool_disabled`/`output_invalid` and the like are
    never safe to retry regardless of what a registry entry might declare,
    so they are not members of this enum at all (an entry naming one
    cannot even parse), rather than being a runtime check this module
    would otherwise have to duplicate everywhere retry safety matters."""

    TIMEOUT = "timeout"
    TRANSPORT_UNAVAILABLE = "transport_unavailable"


class RetryPolicy(BaseModel):
    """A tool's own declared retry policy (`tool_registry_requirements.md`
    field list: `retry_policy`). `max_attempts` bounds how many times the
    controlled executor (Task 7/9) may invoke transport for one logical
    request -- `1` means "never retry". Capped at `5` so no registry entry
    can declare unbounded retry; `mcp_execution_rules.md`: "Retry is
    bounded and only allowed when execution is retry-safe" is enforced
    twice over -- structurally here (the cap itself), and again by
    `ToolDefinition`'s own validator below for any side-effecting/
    non-idempotent tool, which may not retry at all no matter what value
    is declared here."""

    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(
        ge=1, le=5, description="Maximum transport invocations for one logical request; 1 means no retry."
    )
    retryable_categories: list[RetryableFailureCategory] = Field(
        default_factory=list,
        description="Transient failure categories this tool may safely be retried for.",
    )


def _validate_object_schema_shape(schema: Any, *, field_name: str) -> dict[str, Any]:
    """The one place a registered tool's `input_schema`/`output_schema` is
    checked for basic, structural JSON-Schema-object sanity
    (`tool_registry_requirements.md`: "invalid/missing schema" rejected).
    Deliberately shallow -- `type: object`, well-formed `properties` /
    `required` / `additionalProperties` -- not a full JSON Schema
    validator; validating a tool *payload* against an already-registered
    schema is Task 5's `schema_validator.py` job, built on top of a real
    schema-validation library, not this module's."""
    if not isinstance(schema, dict):
        raise ValueError(f"{field_name} must be a JSON Schema object")

    if schema.get("type") != "object":
        raise ValueError(f"{field_name} must declare type 'object'")

    properties = schema.get("properties")
    if properties is not None and not isinstance(properties, dict):
        raise ValueError(f"{field_name}.properties must be an object when present")

    required = schema.get("required")
    if required is not None:
        if not isinstance(required, list) or not all(isinstance(r, str) and r for r in required):
            raise ValueError(f"{field_name}.required must be a list of non-empty strings when present")
        if properties is not None:
            unknown_required = [r for r in required if r not in properties]
            if unknown_required:
                raise ValueError(f"{field_name}.required references unknown properties: {unknown_required}")

    additional_properties = schema.get("additionalProperties")
    if additional_properties is not None and not isinstance(additional_properties, bool):
        raise ValueError(f"{field_name}.additionalProperties must be a boolean when present")

    return schema


class ToolDefinition(BaseModel):
    """A single registered tool/version (`tool_registry_requirements.md`
    field list). One `(tool_id, tool_version)` pair identifies exactly one
    definition -- `ToolRegistryDocument`'s own validator below rejects a
    duplicate pair, and version selection is always exact (Day 13 rule:
    "Tool version is explicit"), never a latest/range resolution this
    module would have to invent."""

    model_config = ConfigDict(extra="forbid")

    tool_id: str = Field(min_length=1, description="Stable tool identifier, unique together with tool_version.")
    tool_version: str = Field(description="Exact semantic version (MAJOR.MINOR.PATCH) of this tool definition.")
    display_name: str = Field(min_length=1, description="Human-readable tool name.")
    description: str = Field(min_length=1, description="Human-readable description of what this tool does.")
    owner: str = Field(min_length=1, description="Accountable owner of this tool; required for every definition.")
    status: ToolStatus
    transport: ToolTransportKind
    server_alias: str = Field(min_length=1, description="Approved MCP server alias this tool executes through.")
    risk_level: RiskLevel
    side_effecting: bool = Field(description="Whether this tool mutates external state.")
    idempotent: bool = Field(description="Whether repeated identical invocations are safe.")
    timeout_ms: int = Field(gt=0, description="Bounded per-invocation timeout in milliseconds.")
    retry_policy: RetryPolicy
    required_permissions: list[str] = Field(
        default_factory=list, description="Trusted permission ids a caller must hold to execute this tool."
    )
    input_schema: dict[str, Any] = Field(description="JSON Schema object this tool's arguments must satisfy.")
    output_schema: dict[str, Any] = Field(description="JSON Schema object this tool's transport result must satisfy.")

    @field_validator("tool_version")
    @classmethod
    def _validate_semver(cls, value: str) -> str:
        if not _SEMVER_PATTERN.match(value):
            raise ValueError(f"tool_version must be MAJOR.MINOR.PATCH, got {value!r}")
        return value

    @field_validator("required_permissions")
    @classmethod
    def _validate_permissions_nonempty(cls, value: list[str]) -> list[str]:
        if any(not p for p in value):
            raise ValueError("required_permissions entries must be non-empty strings")
        return value

    @field_validator("input_schema")
    @classmethod
    def _validate_input_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_object_schema_shape(value, field_name="input_schema")

    @field_validator("output_schema")
    @classmethod
    def _validate_output_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_object_schema_shape(value, field_name="output_schema")

    @model_validator(mode="after")
    def _validate_retry_safety(self) -> ToolDefinition:
        unsafe_to_retry = self.side_effecting or not self.idempotent
        if unsafe_to_retry and (self.retry_policy.max_attempts > 1 or self.retry_policy.retryable_categories):
            raise ValueError(
                f"tool {self.tool_id!r}@{self.tool_version!r} is side_effecting/non-idempotent "
                "and may not declare a retry-enabled retry_policy"
            )
        return self

    @property
    def key(self) -> tuple[str, str]:
        """The `(tool_id, tool_version)` identity pair Task 2's registry
        looks entries up by -- exact version, never latest/range."""
        return (self.tool_id, self.tool_version)


class ToolRegistryDocument(BaseModel):
    """The full versioned, typed Tool Registry document
    (`tool_registry_requirements.md`; `tools/registry.v1.json`). Loaded and
    exposed read-only by Task 2's `registry.py`; nothing at runtime is
    permitted to construct or mutate one from a request, model output, or
    session memory (Day 13 working rule: "Request body, model output and
    session memory cannot self-assert tool permission" -- registration is
    the same category of trust decision)."""

    model_config = ConfigDict(extra="forbid")

    registry_version: str = Field(min_length=1, description="Required governed tool registry version identifier.")
    tools: list[ToolDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_no_duplicate_tool_versions(self) -> ToolRegistryDocument:
        seen: set[tuple[str, str]] = set()
        for tool in self.tools:
            if tool.key in seen:
                raise ValueError(f"duplicate tool/version: {tool.tool_id!r}@{tool.tool_version!r}")
            seen.add(tool.key)
        return self
