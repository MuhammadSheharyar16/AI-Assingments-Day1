"""
Day 13 Task 4 -- the Tool Execution Policy: the typed document
(`ToolExecutionPolicyDocument`/`ToolExecutionPolicyRule`) and the
deterministic, default-deny decision engine (`ToolExecutionPolicy.
authorize()`, `src/aico/tools/policy.py`).

Proves, against the real committed `policy/tool_execution_policy.v1.json`
and `tools/registry.v1.json`, plus hand-built documents for the stages the
real committed fixtures never exercise (defense-in-depth):

  - the document's own typed validation: required `policy_version`,
    `default_decision` pinned to `"deny"`, no duplicate `rule_id`, no two
    rules governing the same `(tool_id, tool_version)` pair, invalid
    semver/enum values rejected;
  - `ToolExecutionPolicy.load()`'s success/failure paths;
  - every stage `authorize()` runs, each denying independently: tool
    disabled (`execution_cases.json` EXEC13-005), no matching rule, policy
    rule not active, rule denied outright, missing trusted permission
    (EXEC13-004), server alias not approved, risk level exceeds policy,
    unsafe retry for a non-idempotent tool (defense-in-depth);
  - the one allow path (EXEC13-001's own tool/permissions), and that
    `ALLOW` is only ever reached once every stage positively resolves --
    never a fall-through;
  - the `ToolExecutionPolicyInvariantError` a mismatched `(request, tool)`
    pair raises.

Task 5 (input schema validation), Task 6 (the MCP Gateway itself), and
Task 7 (the controlled executor that actually wires registry -> policy ->
schema -> gateway together) are out of scope here -- this file only proves
the policy document and its own decision engine.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aico.tools.errors import ToolExecutionPolicyInvariantError, ToolExecutionPolicyLoadError
from aico.tools.models import RiskLevel, ToolDefinition, ToolExecutionRequest
from aico.tools.policy import (
    ToolExecutionPolicy,
    ToolExecutionPolicyDocument,
    ToolExecutionPolicyStatus,
)
from aico.tools.registry import ToolRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_POLICY_PATH = REPO_ROOT / "policy" / "tool_execution_policy.v1.json"
COMMITTED_REGISTRY_PATH = REPO_ROOT / "tools" / "registry.v1.json"


def _load_real_policy() -> ToolExecutionPolicy:
    return ToolExecutionPolicy.load(COMMITTED_POLICY_PATH)


def _load_real_registry() -> ToolRegistry:
    return ToolRegistry.load(COMMITTED_REGISTRY_PATH)


def _minimal_valid_rule(**overrides: object) -> dict:
    rule = {
        "rule_id": "TOOL-R001",
        "tool_id": "supplier_status_lookup",
        "tool_version": "1.0.0",
        "allowed": True,
        "required_permissions": ["read_structured_supplier"],
        "allowed_server_aliases": ["synthetic-procurement-mcp"],
        "max_risk_level": "low",
        "require_idempotent_for_retry": True,
        "status": "active",
    }
    rule.update(overrides)
    return rule


def _minimal_valid_policy(**overrides: object) -> dict:
    policy = {
        "policy_version": "1.0",
        "status": "active",
        "default_decision": "deny",
        "rules": [_minimal_valid_rule()],
    }
    policy.update(overrides)
    return policy


def _request(**overrides: object) -> ToolExecutionRequest:
    fields = {
        "tool_id": "supplier_status_lookup",
        "tool_version": "1.0.0",
        "arguments": {"supplier_id": "SUP-ALPHA"},
        "trusted_permissions": ["read_structured_supplier"],
    }
    fields.update(overrides)
    return ToolExecutionRequest.model_validate(fields)


# ══════════════════════════════════════════════════════════════════════
# `ToolExecutionPolicyDocument` -- typed validation.
# ══════════════════════════════════════════════════════════════════════


class TestToolExecutionPolicyDocumentValidation:
    def test_minimal_valid_policy_is_accepted(self) -> None:
        document = ToolExecutionPolicyDocument.model_validate(_minimal_valid_policy())
        assert document.policy_version == "1.0"
        assert document.default_decision == "deny"
        assert len(document.rules) == 1

    def test_missing_policy_version_rejected(self) -> None:
        policy = _minimal_valid_policy()
        del policy["policy_version"]
        with pytest.raises(ValidationError):
            ToolExecutionPolicyDocument.model_validate(policy)

    def test_default_decision_must_be_deny(self) -> None:
        with pytest.raises(ValidationError):
            ToolExecutionPolicyDocument.model_validate(_minimal_valid_policy(default_decision="allow"))

    def test_duplicate_rule_id_rejected(self) -> None:
        rule_a = _minimal_valid_rule()
        rule_b = _minimal_valid_rule(tool_id="supplier_record_update")
        with pytest.raises(ValidationError, match="duplicate rule_id"):
            ToolExecutionPolicyDocument.model_validate(_minimal_valid_policy(rules=[rule_a, rule_b]))

    def test_ambiguous_tool_version_across_two_rules_rejected(self) -> None:
        rule_a = _minimal_valid_rule(rule_id="TOOL-R001")
        rule_b = _minimal_valid_rule(rule_id="TOOL-R999")
        with pytest.raises(ValidationError, match="ambiguous policy"):
            ToolExecutionPolicyDocument.model_validate(_minimal_valid_policy(rules=[rule_a, rule_b]))

    def test_invalid_semver_tool_version_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolExecutionPolicyDocument.model_validate(
                _minimal_valid_policy(rules=[_minimal_valid_rule(tool_version="latest")])
            )

    def test_unknown_status_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolExecutionPolicyDocument.model_validate(
                _minimal_valid_policy(rules=[_minimal_valid_rule(status="retired")])
            )

    def test_unknown_max_risk_level_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolExecutionPolicyDocument.model_validate(
                _minimal_valid_policy(rules=[_minimal_valid_rule(max_risk_level="critical")])
            )

    def test_empty_allowed_server_aliases_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolExecutionPolicyDocument.model_validate(
                _minimal_valid_policy(rules=[_minimal_valid_rule(allowed_server_aliases=[])])
            )

    def test_unknown_top_level_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolExecutionPolicyDocument.model_validate(_minimal_valid_policy(unexpected_field="nope"))


# ══════════════════════════════════════════════════════════════════════
# `ToolExecutionPolicy.load()`.
# ══════════════════════════════════════════════════════════════════════


class TestToolExecutionPolicyLoad:
    def test_loads_the_committed_policy(self) -> None:
        policy = _load_real_policy()
        assert policy.policy_version == "1.0"
        assert len(policy.rules) == 2

    def test_missing_file_raises_load_error(self, tmp_path: Path) -> None:
        with pytest.raises(ToolExecutionPolicyLoadError, match="not found"):
            ToolExecutionPolicy.load(tmp_path / "does_not_exist.json")

    def test_invalid_json_raises_load_error(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "policy.json"
        bad_file.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ToolExecutionPolicyLoadError, match="not valid JSON"):
            ToolExecutionPolicy.load(bad_file)

    def test_schema_invalid_content_raises_load_error(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "policy.json"
        bad_file.write_text(json.dumps(_minimal_valid_policy(default_decision="allow")), encoding="utf-8")
        with pytest.raises(ToolExecutionPolicyLoadError, match="failed validation"):
            ToolExecutionPolicy.load(bad_file)

    def test_find_rule_returns_none_for_ungoverned_pair(self) -> None:
        policy = _load_real_policy()
        assert policy.find_rule("model_invented_tool", "1.0.0") is None


# ══════════════════════════════════════════════════════════════════════
# `ToolExecutionPolicy.authorize()` -- against the real committed policy
# and registry.
# ══════════════════════════════════════════════════════════════════════


class TestAuthorizeAgainstCommittedPolicy:
    @pytest.fixture()
    def policy(self) -> ToolExecutionPolicy:
        return _load_real_policy()

    @pytest.fixture()
    def registry(self) -> ToolRegistry:
        return _load_real_registry()

    def test_allowed_active_tool_with_permission_and_alias(
        self, policy: ToolExecutionPolicy, registry: ToolRegistry
    ) -> None:
        """`execution_cases.json` EXEC13-001's own shape."""
        tool = registry.get_tool("supplier_status_lookup", "1.0.0")
        request = _request()
        decision = policy.authorize(request, tool)

        assert decision.decision is ToolExecutionPolicyStatus.ALLOW
        assert decision.allowed is True
        assert decision.reason_code == "rule_allowed"
        assert decision.rule_id == "TOOL-R001"
        assert decision.policy_version == "1.0"
        assert decision.require_idempotent_for_retry is True

    def test_disabled_tool_denies_regardless_of_permissions(
        self, policy: ToolExecutionPolicy, registry: ToolRegistry
    ) -> None:
        """`execution_cases.json` EXEC13-005: the disabled
        `supplier_record_update` tool must deny even when the caller holds
        exactly the permission its (also denying) rule would require."""
        tool = registry.get_tool("supplier_record_update", "1.0.0")
        request = _request(
            tool_id="supplier_record_update",
            tool_version="1.0.0",
            arguments={"supplier_id": "SUP-ALPHA", "status": "inactive"},
            trusted_permissions=["write_structured_supplier"],
        )
        decision = policy.authorize(request, tool)

        assert decision.decision is ToolExecutionPolicyStatus.DENY
        assert decision.reason_code == "tool_disabled"
        assert decision.require_idempotent_for_retry is False

    def test_missing_trusted_permission_denies(self, policy: ToolExecutionPolicy, registry: ToolRegistry) -> None:
        """`execution_cases.json` EXEC13-004."""
        tool = registry.get_tool("supplier_status_lookup", "1.0.0")
        request = _request(trusted_permissions=[])
        decision = policy.authorize(request, tool)

        assert decision.decision is ToolExecutionPolicyStatus.DENY
        assert decision.reason_code == "permission_denied"
        assert decision.rule_id == "TOOL-R001"

    def test_argument_permission_does_not_substitute_for_trusted_permission(
        self, policy: ToolExecutionPolicy, registry: ToolRegistry
    ) -> None:
        """Task 3's own trust rule, proven again at the policy boundary:
        a permission-looking value in `arguments` never authorizes."""
        tool = registry.get_tool("supplier_status_lookup", "1.0.0")
        request = _request(
            arguments={"supplier_id": "SUP-ALPHA", "role": "admin", "permissions": ["read_structured_supplier"]},
            trusted_permissions=[],
        )
        decision = policy.authorize(request, tool)
        assert decision.decision is ToolExecutionPolicyStatus.DENY
        assert decision.reason_code == "permission_denied"

    def test_no_matching_rule_denies(self, policy: ToolExecutionPolicy) -> None:
        """A tool the registry could plausibly carry but this policy names
        no rule for at all -- default-deny, never a fall-through."""
        unruled_tool = ToolDefinition.model_validate(
            {
                "tool_id": "some_other_tool",
                "tool_version": "1.0.0",
                "display_name": "Some Other Tool",
                "description": "Not governed by the committed policy.",
                "owner": "AICO",
                "status": "active",
                "transport": "mcp",
                "server_alias": "synthetic-procurement-mcp",
                "risk_level": "low",
                "side_effecting": False,
                "idempotent": True,
                "timeout_ms": 500,
                "retry_policy": {"max_attempts": 1, "retryable_categories": []},
                "required_permissions": [],
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
            }
        )
        request = _request(tool_id="some_other_tool", tool_version="1.0.0", trusted_permissions=[])
        decision = policy.authorize(request, unruled_tool)

        assert decision.decision is ToolExecutionPolicyStatus.DENY
        assert decision.reason_code == "no_matching_rule"
        assert decision.rule_id is None

    def test_invariant_error_on_mismatched_request_and_tool(
        self, policy: ToolExecutionPolicy, registry: ToolRegistry
    ) -> None:
        tool = registry.get_tool("supplier_status_lookup", "1.0.0")
        mismatched_request = _request(tool_id="supplier_record_update", tool_version="1.0.0")
        with pytest.raises(ToolExecutionPolicyInvariantError):
            policy.authorize(mismatched_request, tool)

    def test_decision_is_deterministic(self, policy: ToolExecutionPolicy, registry: ToolRegistry) -> None:
        tool = registry.get_tool("supplier_status_lookup", "1.0.0")
        request = _request()
        first = policy.authorize(request, tool)
        second = policy.authorize(request, tool)
        assert first == second


# ══════════════════════════════════════════════════════════════════════
# `ToolExecutionPolicy.authorize()` -- hand-built defense-in-depth stages
# the committed fixtures never exercise.
# ══════════════════════════════════════════════════════════════════════


class TestAuthorizeDefenseInDepthStages:
    def test_policy_rule_not_active_denies(self) -> None:
        registry = _load_real_registry()
        tool = registry.get_tool("supplier_status_lookup", "1.0.0")
        policy = ToolExecutionPolicy(
            ToolExecutionPolicyDocument.model_validate(
                _minimal_valid_policy(rules=[_minimal_valid_rule(status="disabled")])
            )
        )
        decision = policy.authorize(_request(), tool)

        assert decision.decision is ToolExecutionPolicyStatus.DENY
        assert decision.reason_code == "policy_rule_not_active"
        assert decision.rule_id == "TOOL-R001"

    def test_rule_denied_outright(self) -> None:
        registry = _load_real_registry()
        tool = registry.get_tool("supplier_status_lookup", "1.0.0")
        policy = ToolExecutionPolicy(
            ToolExecutionPolicyDocument.model_validate(_minimal_valid_policy(rules=[_minimal_valid_rule(allowed=False)]))
        )
        decision = policy.authorize(_request(), tool)

        assert decision.decision is ToolExecutionPolicyStatus.DENY
        assert decision.reason_code == "rule_denied"

    def test_server_alias_not_allowed_denies(self) -> None:
        registry = _load_real_registry()
        tool = registry.get_tool("supplier_status_lookup", "1.0.0")
        policy = ToolExecutionPolicy(
            ToolExecutionPolicyDocument.model_validate(
                _minimal_valid_policy(rules=[_minimal_valid_rule(allowed_server_aliases=["some-other-server"])])
            )
        )
        decision = policy.authorize(_request(), tool)

        assert decision.decision is ToolExecutionPolicyStatus.DENY
        assert decision.reason_code == "server_alias_not_allowed"

    def test_risk_level_exceeds_policy_denies(self) -> None:
        registry = _load_real_registry()
        tool = registry.get_tool("supplier_status_lookup", "1.0.0")
        high_risk_tool = tool.model_copy(update={"risk_level": RiskLevel.HIGH})
        policy = ToolExecutionPolicy(
            ToolExecutionPolicyDocument.model_validate(
                _minimal_valid_policy(rules=[_minimal_valid_rule(max_risk_level="low")])
            )
        )
        decision = policy.authorize(_request(), high_risk_tool)

        assert decision.decision is ToolExecutionPolicyStatus.DENY
        assert decision.reason_code == "risk_level_exceeds_policy"

    def test_unsafe_retry_for_non_idempotent_tool_denies(self) -> None:
        """Defense-in-depth only: Task 1's own `ToolDefinition` validator
        already refuses to register a tool shaped like this (retry-enabled
        alongside `idempotent=False`), so this hand-built object -- built
        valid, then adjusted via `model_copy` (which does not re-run
        validators) -- simulates a malformed input reaching this stage
        anyway, the same technique the module docstring describes as
        "never actually fires against the real committed registry"."""
        registry = _load_real_registry()
        tool = registry.get_tool("supplier_status_lookup", "1.0.0")
        assert tool.retry_policy.max_attempts > 1  # sanity: retry is enabled on the real tool
        unsafe_tool = tool.model_copy(update={"idempotent": False})

        policy = ToolExecutionPolicy(
            ToolExecutionPolicyDocument.model_validate(
                _minimal_valid_policy(rules=[_minimal_valid_rule(require_idempotent_for_retry=True)])
            )
        )
        decision = policy.authorize(_request(), unsafe_tool)

        assert decision.decision is ToolExecutionPolicyStatus.DENY
        assert decision.reason_code == "unsafe_retry_for_non_idempotent_tool"

    def test_empty_policy_denies_every_request(self) -> None:
        """Structural proof of "Execution policy defaults to deny": a
        policy declaring zero rules at all can never ALLOW anything."""
        registry = _load_real_registry()
        tool = registry.get_tool("supplier_status_lookup", "1.0.0")
        empty_policy = ToolExecutionPolicy(
            ToolExecutionPolicyDocument.model_validate(_minimal_valid_policy(rules=[]))
        )
        decision = empty_policy.authorize(_request(), tool)

        assert decision.decision is ToolExecutionPolicyStatus.DENY
        assert decision.reason_code == "no_matching_rule"

    def test_deny_never_populates_require_idempotent_for_retry(self) -> None:
        """Every `_deny()` path leaves `require_idempotent_for_retry` at
        its typed default (`False`), regardless of what the matched rule
        (if any) itself declares -- "nothing is granted on deny"."""
        registry = _load_real_registry()
        tool = registry.get_tool("supplier_status_lookup", "1.0.0")
        policy = ToolExecutionPolicy(
            ToolExecutionPolicyDocument.model_validate(
                _minimal_valid_policy(rules=[_minimal_valid_rule(allowed=False, require_idempotent_for_retry=True)])
            )
        )
        decision = policy.authorize(_request(), tool)
        assert decision.decision is ToolExecutionPolicyStatus.DENY
        assert decision.require_idempotent_for_retry is False
