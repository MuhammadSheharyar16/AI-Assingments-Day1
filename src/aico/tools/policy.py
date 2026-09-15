"""
Day 13 Task 4 -- the Tool Execution Policy: the deterministic, default-deny
authorization boundary a resolved `(ToolExecutionRequest, ToolDefinition)`
pair must pass before the MCP Gateway (Task 6) is ever invoked
(`mcp_execution_rules.md`: "ToolExecutionRequest -> Tool Registry -> Tool
Policy -> Input Schema -> MCP Gateway -> ..."). Combines the typed,
self-validating policy document (the `ToolExecutionPolicyDocument`/
`ToolExecutionPolicyRule` shape Task 1's own field list mirrors) and the
decision engine (`ToolExecutionPolicy.authorize()`) in one file -- Day 13's
required structure names a single `policy.py`, not a separate
document/registry-vs-decision split the way Gate-B's `policy_models.py`
(document) + `gate_b.py` (decision) are two files; this module folds both
together the same way `evidence/policy.py` does for Gate-C's own policy
document (its decision engine, `GateC.evaluate()`, still lives in
`gate_c.py` -- the difference here is Day 13 gives the *decision engine*
itself no separate file either).

Policy considers (`Day 13 Task.pdf`, TASK 4), each traced to where
`ToolExecutionPolicy.authorize()` checks it:

    - registered tool/version              -> the caller supplies an
                                               already-registry-resolved
                                               `ToolDefinition`; `authorize()`
                                               matches a policy rule by the
                                               exact same `(tool_id,
                                               tool_version)` pair
                                               (`find_rule()`), never a
                                               latest/range resolution.
    - active/disabled status               -> `tool.status is not ACTIVE`
                                               denies immediately with
                                               `tool_disabled`
                                               (`execution_cases.json`
                                               EXEC13-005: the disabled
                                               `supplier_record_update`
                                               tool, zero transport calls) --
                                               checked *before* any rule
                                               lookup, so a disabled tool is
                                               denied the same way
                                               regardless of what policy
                                               authoring exists for it.
                                               `rule.status is not ACTIVE`
                                               (the policy *rule's* own
                                               lifecycle, independent of the
                                               tool's) is checked
                                               immediately after and treated
                                               as "no usable rule" --
                                               default-deny, not a
                                               `tool_disabled` outcome.
    - required trusted permission          -> every `rule.
                                               required_permissions` entry
                                               must be present in
                                               `resolve_trusted_permissions
                                               (request)` (Task 3) -- never
                                               `request.arguments`
                                               (`execution_cases.json`
                                               EXEC13-004: `permission_denied`).
    - effective scope compatibility        -> `tool.server_alias` must be
                                               one of `rule.
                                               allowed_server_aliases` --
                                               the approved-transport-
                                               boundary half of "effective
                                               scope"; tenant scope
                                               (`resolve_effective_tenant_scope`,
                                               Task 3) is carried through
                                               unchanged into the MCP
                                               Gateway (Task 6), this policy
                                               has no per-tool tenant rule
                                               to narrow it against.
    - risk level                           -> `tool.risk_level` must not
                                               exceed `rule.max_risk_level`
                                               (`_risk_rank`).
    - side-effecting/idempotent metadata   -> when `rule.
                                               require_idempotent_for_retry`
                                               is set, a tool that is not
                                               `idempotent` may not have a
                                               retry-enabled `retry_policy`
                                               -- defense-in-depth: Task 1's
                                               own `ToolDefinition` validator
                                               already refuses to register
                                               such a tool at all, so this
                                               never actually fires against
                                               the real committed registry
                                               (the identical "never
                                               actually fires against the
                                               real committed policy today"
                                               role `GateB`'s own
                                               `permission_not_granted`
                                               stage plays), satisfying
                                               `registry_validation_cases.json`
                                               REG13-005's `invalid_or_
                                               policy_rejected` either way.
    - approved transport/server alias      -> the identical
                                               `allowed_server_aliases`
                                               check as "effective scope
                                               compatibility" above -- one
                                               governed concept, one check.

"Default decision is deny": `ToolExecutionPolicyDocument.default_decision`
is pinned `Literal["deny"]` (a policy file cannot even declare anything
else), and `authorize()`'s every early-return before the final `_allow()`
call is a `_deny()` -- there is no fall-through path that reaches `_allow()`
without every stage above having positively resolved."""
from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from aico.tools.errors import ToolExecutionPolicyInvariantError, ToolExecutionPolicyLoadError
from aico.tools.models import (
    RiskLevel,
    ToolDefinition,
    ToolExecutionRequest,
    ToolStatus,
    _validate_semver_string,
    resolve_trusted_permissions,
)

# Relative to the process working directory, matching
# `ToolRegistry.DEFAULT_REGISTRY_PATH`'s convention -- `uv run` always runs
# from the repository root.
DEFAULT_TOOL_EXECUTION_POLICY_PATH = Path("policy/tool_execution_policy.v1.json")

# LOW < MEDIUM < HIGH -- the one place a `RiskLevel` is ever ranked against
# another (Day 13 working rule analog of `policy_models.py`'s "Do not
# hardcode behavior in multiple unrelated files": one ranking, not one
# reimplemented per caller).
_RISK_LEVEL_RANK: dict[RiskLevel, int] = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}


class ToolExecutionPolicyRule(BaseModel):
    """One governed tool execution rule (`tool_execution_policy_v1.json`
    field list): whether the exact `(tool_id, tool_version)` pair may
    execute at all, which trusted permissions/server aliases/risk ceiling
    that requires, and whether this tool must be idempotent to ever be
    retried."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1, description="Non-empty, unique rule identifier.")
    tool_id: str = Field(min_length=1, description="tool_id this rule governs.")
    tool_version: str = Field(description="Exact tool_version this rule governs.")
    allowed: bool = Field(description="Whether this rule, once matched, authorizes execution.")
    required_permissions: tuple[str, ...] = Field(
        default_factory=tuple, description="Trusted permission ids a request must carry to execute this tool."
    )
    allowed_server_aliases: tuple[str, ...] = Field(
        min_length=1, description="Approved MCP server aliases this tool may execute through."
    )
    max_risk_level: RiskLevel = Field(description="Highest risk_level this rule permits for the matched tool.")
    require_idempotent_for_retry: bool = Field(
        description="Whether a retry-enabled retry_policy requires the matched tool to be idempotent."
    )
    status: ToolStatus = Field(description="This rule's own lifecycle status, independent of the tool's.")

    @field_validator("tool_version")
    @classmethod
    def _validate_tool_version_semver(cls, value: str) -> str:
        return _validate_semver_string(value, field_name="tool_version")

    @field_validator("required_permissions")
    @classmethod
    def _validate_required_permissions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not entry for entry in value):
            raise ValueError("required_permissions entries must be non-empty strings")
        return value

    @field_validator("allowed_server_aliases")
    @classmethod
    def _validate_allowed_server_aliases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not entry for entry in value):
            raise ValueError("allowed_server_aliases entries must be non-empty strings")
        return value

    @property
    def key(self) -> tuple[str, str]:
        return (self.tool_id, self.tool_version)


class ToolExecutionPolicyDocument(BaseModel):
    """The full versioned, typed Tool Execution Policy document. Loaded and
    exposed read-only by `ToolExecutionPolicy` below; nothing at runtime is
    permitted to construct or mutate one from a request, model output, or
    session memory."""

    model_config = ConfigDict(extra="forbid")

    policy_version: str = Field(min_length=1, description="Required governed policy version identifier.")
    status: ToolStatus
    default_decision: Literal["deny"] = Field(
        description="Fixed default-deny outcome -- a policy file cannot declare any other default."
    )
    rules: tuple[ToolExecutionPolicyRule, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _validate_policy_integrity(self) -> ToolExecutionPolicyDocument:
        seen_rule_ids: set[str] = set()
        seen_tool_versions: set[tuple[str, str]] = set()
        for rule in self.rules:
            if rule.rule_id in seen_rule_ids:
                raise ValueError(f"duplicate rule_id: {rule.rule_id!r}")
            seen_rule_ids.add(rule.rule_id)

            if rule.key in seen_tool_versions:
                raise ValueError(
                    f"ambiguous policy: more than one rule governs tool {rule.tool_id!r}@{rule.tool_version!r}"
                )
            seen_tool_versions.add(rule.key)
        return self


class ToolExecutionPolicyStatus(str, Enum):
    """The two possible `ToolExecutionPolicy.authorize()` outcomes.
    Deliberately not extensible at the type level -- a third status would
    need a policy/engine change, never an ad hoc string."""

    ALLOW = "allow"
    DENY = "deny"


class ToolExecutionPolicyDecision(BaseModel):
    """`ToolExecutionPolicy.authorize()`'s typed result. `ALLOW` is the
    only status carrying a populated `rule_id`/`require_idempotent_for_retry`
    from an actually-matched, actually-allowed rule -- a `DENY` may still
    carry `rule_id` when a rule *matched* but denied on some later ground
    (e.g. a missing permission), distinct from `rule_id=None`, which means
    no rule governs this `(tool_id, tool_version)` pair at all (Day 13's
    own default-deny outcome)."""

    model_config = ConfigDict(extra="forbid")

    decision: ToolExecutionPolicyStatus
    reason_code: str = Field(min_length=1, description="Stable, sanitized reason this decision was reached.")
    tool_id: str = Field(min_length=1)
    tool_version: str = Field(min_length=1)
    rule_id: str | None = Field(default=None, description="The matched rule_id, when one matched.")
    policy_version: str = Field(min_length=1)
    require_idempotent_for_retry: bool = Field(
        default=False, description="The matched rule's own flag; only meaningful when decision is ALLOW."
    )

    @property
    def allowed(self) -> bool:
        return self.decision is ToolExecutionPolicyStatus.ALLOW


class ToolExecutionPolicy:
    """The loaded, read-only Tool Execution Policy: `load()` in production
    code (loads and validates the committed file); tests may instead build
    a `ToolExecutionPolicyDocument` directly and pass it to the plain
    constructor -- the constructor itself never touches disk."""

    def __init__(self, document: ToolExecutionPolicyDocument):
        self._document = document
        self._rules_by_key: dict[tuple[str, str], ToolExecutionPolicyRule] = {
            rule.key: rule for rule in document.rules
        }

    # ── Loading ──────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: str | Path = DEFAULT_TOOL_EXECUTION_POLICY_PATH) -> ToolExecutionPolicy:
        """Load and validate the committed policy file at `path` (default
        `policy/tool_execution_policy.v1.json`; see `policy/README.md`).
        Raises `ToolExecutionPolicyLoadError` for anything wrong with the
        file itself: missing, unreadable, not valid JSON, or failing
        `ToolExecutionPolicyDocument`'s typed validation. Never falls back
        to an empty/default/permissive policy."""
        resolved = Path(path)
        if not resolved.exists():
            raise ToolExecutionPolicyLoadError(f"tool execution policy not found: {resolved}")
        try:
            raw_text = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            raise ToolExecutionPolicyLoadError(f"could not read tool execution policy {resolved}: {exc}") from exc

        try:
            raw_data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ToolExecutionPolicyLoadError(f"tool execution policy {resolved} is not valid JSON: {exc}") from exc

        try:
            document = ToolExecutionPolicyDocument.model_validate(raw_data)
        except ValidationError as exc:
            raise ToolExecutionPolicyLoadError(f"tool execution policy {resolved} failed validation: {exc}") from exc

        return cls(document)

    # ── Read-only access ─────────────────────────────────────────────

    @property
    def policy_version(self) -> str:
        return self._document.policy_version

    @property
    def rules(self) -> tuple[ToolExecutionPolicyRule, ...]:
        return tuple(self._document.rules)

    def find_rule(self, tool_id: str, tool_version: str) -> ToolExecutionPolicyRule | None:
        """The governed rule for the exact `(tool_id, tool_version)` pair,
        or `None` when this policy declares no rule for it at all -- a
        normal, expected outcome (default-deny), never an exception."""
        return self._rules_by_key.get((tool_id, tool_version))

    # ── Decision ─────────────────────────────────────────────────────

    def authorize(self, request: ToolExecutionRequest, tool: ToolDefinition) -> ToolExecutionPolicyDecision:
        """Authorize one already-registry-resolved `(request, tool)` pair.
        Never raises for an ordinary input -- every outcome, allow or deny,
        is a normal, typed `ToolExecutionPolicyDecision` result (Day 13
        working rule: "Execution policy defaults to deny"; there is no
        fall-through path to `ALLOW`). Only raises
        `ToolExecutionPolicyInvariantError` when `request`/`tool` name
        different `(tool_id, tool_version)` pairs -- an invariant Task 7's
        controlled executor never violates by construction; see that
        error's own docstring."""
        if request.key != tool.key:
            raise ToolExecutionPolicyInvariantError(
                f"request key {request.key!r} does not match resolved tool key {tool.key!r}"
            )

        version = self._document.policy_version
        tool_id, tool_version = tool.tool_id, tool.tool_version

        # Stage 0 -- the tool's own registered status (cheapest, most
        # certain: independent of whatever policy authoring exists for it;
        # `execution_cases.json` EXEC13-005).
        if tool.status is not ToolStatus.ACTIVE:
            return self._deny(reason_code="tool_disabled", tool_id=tool_id, tool_version=tool_version, version=version)

        # Stage 1 -- a governed rule must exist for this exact pair.
        rule = self.find_rule(tool_id, tool_version)
        if rule is None:
            return self._deny(
                reason_code="no_matching_rule", tool_id=tool_id, tool_version=tool_version, version=version
            )

        # Stage 2 -- the rule's own lifecycle status (independent of the tool's).
        if rule.status is not ToolStatus.ACTIVE:
            return self._deny(
                reason_code="policy_rule_not_active",
                tool_id=tool_id,
                tool_version=tool_version,
                version=version,
                rule_id=rule.rule_id,
            )

        # Stage 3 -- the rule itself may deny outright.
        if not rule.allowed:
            return self._deny(
                reason_code="rule_denied", tool_id=tool_id, tool_version=tool_version, version=version, rule_id=rule.rule_id
            )

        # Stage 4 -- required trusted permission (Task 3: never `arguments`).
        trusted_permissions = set(resolve_trusted_permissions(request))
        missing_permissions = [p for p in rule.required_permissions if p not in trusted_permissions]
        if missing_permissions:
            return self._deny(
                reason_code="permission_denied",
                tool_id=tool_id,
                tool_version=tool_version,
                version=version,
                rule_id=rule.rule_id,
            )

        # Stage 5 -- approved transport/server alias (effective scope compatibility).
        if tool.server_alias not in rule.allowed_server_aliases:
            return self._deny(
                reason_code="server_alias_not_allowed",
                tool_id=tool_id,
                tool_version=tool_version,
                version=version,
                rule_id=rule.rule_id,
            )

        # Stage 6 -- risk level ceiling.
        if _RISK_LEVEL_RANK[tool.risk_level] > _RISK_LEVEL_RANK[rule.max_risk_level]:
            return self._deny(
                reason_code="risk_level_exceeds_policy",
                tool_id=tool_id,
                tool_version=tool_version,
                version=version,
                rule_id=rule.rule_id,
            )

        # Stage 7 -- side-effecting/idempotent metadata (defense-in-depth;
        # Task 1's own ToolDefinition validator already refuses to register
        # a tool shaped like this, see module docstring).
        retry_enabled = tool.retry_policy.max_attempts > 1
        if rule.require_idempotent_for_retry and retry_enabled and not tool.idempotent:
            return self._deny(
                reason_code="unsafe_retry_for_non_idempotent_tool",
                tool_id=tool_id,
                tool_version=tool_version,
                version=version,
                rule_id=rule.rule_id,
            )

        # Stage 8 -- allow.
        return ToolExecutionPolicyDecision(
            decision=ToolExecutionPolicyStatus.ALLOW,
            reason_code="rule_allowed",
            tool_id=tool_id,
            tool_version=tool_version,
            rule_id=rule.rule_id,
            policy_version=version,
            require_idempotent_for_retry=rule.require_idempotent_for_retry,
        )

    @staticmethod
    def _deny(
        *, reason_code: str, tool_id: str, tool_version: str, version: str, rule_id: str | None = None
    ) -> ToolExecutionPolicyDecision:
        """Every deny path funnels through here so "nothing is granted on
        deny" is enforced in exactly one place -- `require_idempotent_for_retry`
        stays at its typed default (`False`) for every deny, never
        populated by accident of which stage rejected the request."""
        return ToolExecutionPolicyDecision(
            decision=ToolExecutionPolicyStatus.DENY,
            reason_code=reason_code,
            tool_id=tool_id,
            tool_version=tool_version,
            rule_id=rule_id,
            policy_version=version,
        )
