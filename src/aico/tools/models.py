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

## Day 13 Task 3 -- the typed execution request.

`ToolExecutionRequest` (near the end of this file) is the one typed shape
every tool invocation enters the governed pipeline as -- built by a
governed application/workflow, never by a model or by unchecked request-
body parsing (the pipeline diagram, `Day 13 Task.pdf` page 5: "Governed
Application / Workflow -> Typed ToolExecutionRequest -> Tool Registry ->
...")). It is `frozen=True`: nothing downstream (policy, schema
validation, the MCP Gateway, transport) is permitted to rewrite a field
mid-pipeline.

Its four "Trust rules" (`Day 13 Task.pdf` Task 3) are each satisfied by
where a value the pipeline actually uses may come from, not by scanning
`arguments` for forbidden keys:

    - "arguments.role cannot grant permission"          -> permission is
      only ever read from `trusted_permissions`, a field the caller sets
      directly, independent of whatever keys `arguments` happens to
      carry. `resolve_trusted_permissions()` below is the one function
      that ever reads a request's authorized permission set, and it never
      touches `arguments` -- there is no code path through which a value
      placed in `arguments` (e.g. `{"role": "admin"}`,
      `execution_cases.json` EXEC13-003) could reach it.
    - "arguments.tenant_id cannot change trusted tenant
      scope"                                            -> the identical
      pattern, one field over: `effective_tenant_scope` is caller-set,
      independent of `arguments`, and `resolve_effective_tenant_scope()`
      is the one function that ever reads it.
    - "model text cannot grant permission" /
      "session memory cannot grant permission"          -> `ToolExecutionRequest`
      has no field that accepts raw model output or session memory content
      at all -- there is nothing for either to be assigned *to* that this
      pipeline would ever treat as authorization. A governed caller that
      wants to honor something a model said is expected to have already
      turned it into `arguments` (untrusted, schema-validated business
      input, Task 5) -- never into `trusted_permissions`/
      `effective_tenant_scope`.

"If tenant context is needed by transport, inject trusted effective scope
separately from untrusted arguments" is exactly why `effective_tenant_scope`
exists as its own field rather than as a documented convention for what
key to put in `arguments` -- the MCP Gateway (Task 6) reads tenant scope
from this field, never from `arguments`.
"""
from __future__ import annotations

import re
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


def _validate_semver_string(value: str, *, field_name: str) -> str:
    """The one place a semantic-version string is ever checked
    (`tool_registry_requirements.md`: "invalid version rejected"). Shared
    by `ToolDefinition.tool_version` (Task 1) and
    `ToolExecutionRequest.tool_version` (Task 3) -- Day 13's working rule
    "Tool version is explicit" applies equally to a registered definition
    and to a request naming one, so both are held to the identical strict
    `MAJOR.MINOR.PATCH` shape rather than each re-implementing its own
    check."""
    if not _SEMVER_PATTERN.match(value):
        raise ValueError(f"{field_name} must be MAJOR.MINOR.PATCH, got {value!r}")
    return value


def _generate_request_id() -> str:
    """A fresh, opaque request identifier for `ToolExecutionRequest.
    request_id`/`correlation_id` when the caller does not supply one --
    the identical "correlation context may be generated, authorization
    context may not" split `aico.api.correlation` already draws for the
    HTTP boundary (Day 6 Task 3), reimplemented locally rather than
    imported from `aico.api` so `aico.tools` never depends on the outer
    API layer."""
    return str(uuid.uuid4())


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
        return _validate_semver_string(value, field_name="tool_version")

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


def _validate_nonempty_string_tuple(value: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    """Shared entry-level guard for `ToolExecutionRequest.trusted_permissions`
    / `effective_tenant_scope` -- each entry must itself be a real,
    non-empty identifier, the same "unchecked dictionaries/strings are not
    acceptable at this boundary" reading every other governed collection
    in this codebase gets."""
    if any(not entry for entry in value):
        raise ValueError(f"{field_name} entries must be non-empty strings")
    return value


class ToolExecutionRequest(BaseModel):
    """Day 13 Task 3 -- the typed shape every tool invocation enters the
    governed execution pipeline as (`request_id` / `correlation_id` /
    `tool_id` / `tool_version` / `arguments` / `trusted_permissions` /
    `effective_tenant_scope` / `idempotency_key` / `execution_context`).
    See this module's own "Day 13 Task 3" docstring section above for how
    each Trust rule is satisfied by this type's shape.

    `frozen=True`: once a governed application/workflow builds one, no
    stage of the pipeline (Tool Registry lookup, policy, input schema
    validation, the MCP Gateway, transport, output schema validation) may
    rewrite a field -- e.g. widen `trusted_permissions` after a policy
    denial, or swap `tool_version` mid-flight. A stage that needs a
    *different* request (retry with a new `idempotency_key`, say) builds a
    new `ToolExecutionRequest`, it never mutates this one."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(
        default_factory=_generate_request_id, min_length=1, description="Identifies this one execution attempt."
    )
    correlation_id: str = Field(
        default_factory=_generate_request_id,
        min_length=1,
        description="Identifies the logical operation this execution is part of.",
    )
    tool_id: str = Field(min_length=1, description="Registered tool_id this request asks to execute.")
    tool_version: str = Field(description="Exact registered tool_version this request asks to execute.")
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="Untrusted, caller-supplied tool arguments -- schema-validated (Task 5), never a source of trust.",
    )
    trusted_permissions: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Permission ids from trusted application/control-plane context. Never derived from arguments.",
    )
    effective_tenant_scope: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Trusted effective tenant scope, injected separately from arguments.",
    )
    idempotency_key: str | None = Field(
        default=None, min_length=1, description="Caller-supplied key identifying a retry of the same logical call."
    )
    execution_context: dict[str, Any] = Field(
        default_factory=dict,
        description="Trusted, caller-supplied operational metadata (e.g. channel/environment) -- never authorization.",
    )

    @field_validator("tool_version")
    @classmethod
    def _validate_semver(cls, value: str) -> str:
        return _validate_semver_string(value, field_name="tool_version")

    @field_validator("trusted_permissions")
    @classmethod
    def _validate_trusted_permissions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_nonempty_string_tuple(value, field_name="trusted_permissions")

    @field_validator("effective_tenant_scope")
    @classmethod
    def _validate_effective_tenant_scope(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_nonempty_string_tuple(value, field_name="effective_tenant_scope")

    @property
    def key(self) -> tuple[str, str]:
        """The `(tool_id, tool_version)` pair this request asks the Tool
        Registry to resolve -- `registry.get_tool(*request.key)`."""
        return (self.tool_id, self.tool_version)


def resolve_trusted_permissions(request: ToolExecutionRequest) -> tuple[str, ...]:
    """The one place a `ToolExecutionRequest`'s authorized permission set
    is ever read (Day 13 Task 7/8's "Do not hardcode behavior in multiple
    unrelated files", the identical discipline `policy_models.py`'s own
    `is_data_classification_permitted()` follows). Always
    `request.trusted_permissions` -- `request.arguments` is never
    consulted, no matter what keys it carries (Day 13 working rule:
    "arguments.role cannot grant permission"; "Request body, model output
    and session memory cannot self-assert tool permission"). Task 4's
    execution policy is expected to call this rather than reading
    `request.trusted_permissions` directly, so there is exactly one place
    in the codebase this decision is ever made."""
    return request.trusted_permissions


def resolve_effective_tenant_scope(request: ToolExecutionRequest) -> tuple[str, ...]:
    """The one place a `ToolExecutionRequest`'s effective tenant scope is
    ever read -- the identical pattern `resolve_trusted_permissions()`
    establishes, one field over. Always `request.effective_tenant_scope`;
    `request.arguments` is never consulted (Day 13 working rule:
    "arguments.tenant_id cannot change trusted tenant scope"). The MCP
    Gateway (Task 6) is expected to call this, not read the field
    directly, when it needs to inject tenant context into transport
    separately from untrusted arguments."""
    return request.effective_tenant_scope
