"""
Day 13 Task 1 -- the typed Tool Registry model (`ToolDefinition` /
`ToolRegistryDocument`, `src/aico/tools/models.py`).
Day 13 Task 2 -- the Tool Registry service (`ToolRegistry`,
`src/aico/tools/registry.py`).

Mirrors `test_day12_gate_d_policy.py`'s two-section style: the first
section proves `ToolRegistryDocument`'s own typed validation directly
against Pydantic, driven by the real committed pack fixture
(`data/day13_pack/fixtures/tool_registry_v1.json`) plus
`registry_validation_cases.json`'s own named mutations
(REG13-001..REG13-005) -- each replayed against a hand-built minimal valid
registry rather than the fixture file itself, so every test only ever
changes the one thing it is proving is rejected. The second section proves
`ToolRegistry` end to end: loading the real committed `tools/registry.v1.json`,
its read-only accessors, exact-version lookup, active/disabled state, and
its typed `tool_not_found`/`version_not_found` failure distinction.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from aico.tools.errors import ToolNotFoundError, ToolRegistryLoadError, ToolVersionNotFoundError
from aico.tools.models import (
    RetryableFailureCategory,
    RetryPolicy,
    RiskLevel,
    ToolDefinition,
    ToolRegistryDocument,
    ToolStatus,
    ToolTransportKind,
)
from aico.tools.registry import ToolRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_REGISTRY_PATH = REPO_ROOT / "tools" / "registry.v1.json"
PACK_FIXTURE_PATH = REPO_ROOT / "data" / "day13_pack" / "fixtures" / "tool_registry_v1.json"
VALIDATION_CASES_PATH = REPO_ROOT / "data" / "day13_pack" / "fixtures" / "registry_validation_cases.json"


def _load_pack_fixture_dict() -> dict:
    return json.loads(PACK_FIXTURE_PATH.read_text(encoding="utf-8"))


def _minimal_valid_tool(**overrides: object) -> dict:
    """A small, hand-built valid tool definition for isolated negative-path
    mutation tests -- distinct from the real fixture tools so each test
    only ever changes the one thing it is proving is rejected."""
    tool = {
        "tool_id": "supplier_status_lookup",
        "tool_version": "1.0.0",
        "display_name": "Synthetic Supplier Status Lookup",
        "description": "Read-only lookup of synthetic supplier status.",
        "owner": "AICO Synthetic Procurement Platform",
        "status": "active",
        "transport": "mcp",
        "server_alias": "synthetic-procurement-mcp",
        "risk_level": "low",
        "side_effecting": False,
        "idempotent": True,
        "timeout_ms": 900,
        "retry_policy": {"max_attempts": 2, "retryable_categories": ["transport_unavailable", "timeout"]},
        "required_permissions": ["read_structured_supplier"],
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["supplier_id"],
            "properties": {"supplier_id": {"type": "string"}},
        },
        "output_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["supplier_id", "status", "as_of"],
            "properties": {
                "supplier_id": {"type": "string"},
                "status": {"type": "string", "enum": ["active", "inactive", "under_review"]},
                "as_of": {"type": "string"},
            },
        },
    }
    tool.update(overrides)
    return tool


def _minimal_valid_registry() -> dict:
    return {"registry_version": "1.0", "tools": [_minimal_valid_tool()]}


# ══════════════════════════════════════════════════════════════════════
# The real committed pack fixture -- valid registry becomes typed objects.
# ══════════════════════════════════════════════════════════════════════


class TestPackFixtureParses:
    def test_valid_registry_becomes_typed_objects(self) -> None:
        raw = _load_pack_fixture_dict()
        document = ToolRegistryDocument.model_validate(raw)

        assert document.registry_version == "1.0"
        assert len(document.tools) == 2

        lookup = document.tools[0]
        assert isinstance(lookup, ToolDefinition)
        assert lookup.tool_id == "supplier_status_lookup"
        assert lookup.tool_version == "1.0.0"
        assert lookup.status is ToolStatus.ACTIVE
        assert lookup.transport is ToolTransportKind.MCP
        assert lookup.risk_level is RiskLevel.LOW
        assert lookup.side_effecting is False
        assert lookup.idempotent is True
        assert lookup.retry_policy.max_attempts == 2
        assert RetryableFailureCategory.TIMEOUT in lookup.retry_policy.retryable_categories
        assert lookup.required_permissions == ["read_structured_supplier"]
        assert lookup.key == ("supplier_status_lookup", "1.0.0")

        update = document.tools[1]
        assert update.tool_id == "supplier_record_update"
        assert update.status is ToolStatus.DISABLED
        assert update.side_effecting is True
        assert update.idempotent is False
        assert update.risk_level is RiskLevel.HIGH

    def test_registry_validation_cases_fixture_is_documented(self) -> None:
        """Proves the pack's own named cases (REG13-001..005) are each
        covered by a test below, rather than silently drifting from the
        fixture the assignment actually supplies."""
        cases = json.loads(VALIDATION_CASES_PATH.read_text(encoding="utf-8"))["cases"]
        case_names = {c["name"] for c in cases}
        assert case_names == {
            "valid_registry",
            "duplicate_tool_version",
            "invalid_semver",
            "missing_owner",
            "unsafe_retry_metadata",
        }


# ══════════════════════════════════════════════════════════════════════
# REG13-001 -- a minimal valid registry/tool is accepted.
# ══════════════════════════════════════════════════════════════════════


def test_minimal_valid_registry_is_accepted() -> None:
    document = ToolRegistryDocument.model_validate(_minimal_valid_registry())
    assert document.tools[0].tool_id == "supplier_status_lookup"


# ══════════════════════════════════════════════════════════════════════
# REG13-002 -- duplicate (tool_id, tool_version) rejected.
# ══════════════════════════════════════════════════════════════════════


def test_duplicate_tool_version_rejected() -> None:
    registry = _minimal_valid_registry()
    registry["tools"].append(copy.deepcopy(registry["tools"][0]))

    with pytest.raises(ValidationError, match="duplicate tool/version"):
        ToolRegistryDocument.model_validate(registry)


# ══════════════════════════════════════════════════════════════════════
# REG13-003 -- invalid semantic version rejected.
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("bad_version", ["one", "1.0", "1.0.0.0", "v1.0.0", ""])
def test_invalid_semver_rejected(bad_version: str) -> None:
    tool = _minimal_valid_tool(tool_version=bad_version)
    with pytest.raises(ValidationError):
        ToolDefinition.model_validate(tool)


def test_valid_semver_accepted() -> None:
    tool = _minimal_valid_tool(tool_version="12.34.5")
    assert ToolDefinition.model_validate(tool).tool_version == "12.34.5"


# ══════════════════════════════════════════════════════════════════════
# REG13-004 -- missing owner rejected.
# ══════════════════════════════════════════════════════════════════════


def test_missing_owner_rejected() -> None:
    tool = _minimal_valid_tool()
    del tool["owner"]
    with pytest.raises(ValidationError, match="owner"):
        ToolDefinition.model_validate(tool)


def test_blank_owner_rejected() -> None:
    tool = _minimal_valid_tool(owner="")
    with pytest.raises(ValidationError):
        ToolDefinition.model_validate(tool)


# ══════════════════════════════════════════════════════════════════════
# REG13-005 -- unsafe retry configuration for a side-effecting/
# non-idempotent tool is rejected at the registry boundary.
# ══════════════════════════════════════════════════════════════════════


def test_unsafe_retry_for_non_idempotent_side_effecting_tool_rejected() -> None:
    tool = _minimal_valid_tool(
        side_effecting=True,
        idempotent=False,
        retry_policy={"max_attempts": 3, "retryable_categories": ["transport_unavailable"]},
    )
    with pytest.raises(ValidationError, match="side_effecting/non-idempotent"):
        ToolDefinition.model_validate(tool)


def test_side_effecting_tool_with_no_retry_is_accepted() -> None:
    tool = _minimal_valid_tool(
        side_effecting=True,
        idempotent=False,
        retry_policy={"max_attempts": 1, "retryable_categories": []},
    )
    document = ToolDefinition.model_validate(tool)
    assert document.retry_policy.max_attempts == 1


def test_retry_policy_max_attempts_is_bounded() -> None:
    with pytest.raises(ValidationError):
        RetryPolicy.model_validate({"max_attempts": 6, "retryable_categories": []})
    with pytest.raises(ValidationError):
        RetryPolicy.model_validate({"max_attempts": 0, "retryable_categories": []})


def test_retry_policy_rejects_non_transient_category() -> None:
    with pytest.raises(ValidationError):
        RetryPolicy.model_validate({"max_attempts": 2, "retryable_categories": ["policy_denied"]})


# ══════════════════════════════════════════════════════════════════════
# unknown status / transport rejected -- closed enums cannot even parse.
# ══════════════════════════════════════════════════════════════════════


def test_unknown_status_rejected() -> None:
    tool = _minimal_valid_tool(status="retired")
    with pytest.raises(ValidationError):
        ToolDefinition.model_validate(tool)


def test_unknown_transport_rejected() -> None:
    tool = _minimal_valid_tool(transport="http")
    with pytest.raises(ValidationError):
        ToolDefinition.model_validate(tool)


# ══════════════════════════════════════════════════════════════════════
# registry version required.
# ══════════════════════════════════════════════════════════════════════


def test_missing_registry_version_rejected() -> None:
    registry = _minimal_valid_registry()
    del registry["registry_version"]
    with pytest.raises(ValidationError):
        ToolRegistryDocument.model_validate(registry)


def test_blank_registry_version_rejected() -> None:
    registry = _minimal_valid_registry()
    registry["registry_version"] = ""
    with pytest.raises(ValidationError):
        ToolRegistryDocument.model_validate(registry)


# ══════════════════════════════════════════════════════════════════════
# timeout metadata valid.
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("bad_timeout", [0, -100])
def test_non_positive_timeout_rejected(bad_timeout: int) -> None:
    tool = _minimal_valid_tool(timeout_ms=bad_timeout)
    with pytest.raises(ValidationError):
        ToolDefinition.model_validate(tool)


# ══════════════════════════════════════════════════════════════════════
# input/output schemas present and structurally valid.
# ══════════════════════════════════════════════════════════════════════


def test_missing_input_schema_rejected() -> None:
    tool = _minimal_valid_tool()
    del tool["input_schema"]
    with pytest.raises(ValidationError):
        ToolDefinition.model_validate(tool)


def test_input_schema_must_declare_object_type() -> None:
    tool = _minimal_valid_tool(input_schema={"type": "string"})
    with pytest.raises(ValidationError, match="type 'object'"):
        ToolDefinition.model_validate(tool)


def test_output_schema_required_field_must_exist_in_properties() -> None:
    tool = _minimal_valid_tool(
        output_schema={
            "type": "object",
            "required": ["not_a_declared_property"],
            "properties": {"status": {"type": "string"}},
        }
    )
    with pytest.raises(ValidationError, match="unknown properties"):
        ToolDefinition.model_validate(tool)


def test_schema_additional_properties_must_be_boolean() -> None:
    tool = _minimal_valid_tool(input_schema={"type": "object", "additionalProperties": "false"})
    with pytest.raises(ValidationError):
        ToolDefinition.model_validate(tool)


# ══════════════════════════════════════════════════════════════════════
# unknown fields rejected at every level (extra="forbid").
# ══════════════════════════════════════════════════════════════════════


def test_unknown_top_level_field_rejected() -> None:
    registry = _minimal_valid_registry()
    registry["unexpected_field"] = "nope"
    with pytest.raises(ValidationError):
        ToolRegistryDocument.model_validate(registry)


def test_unknown_tool_field_rejected() -> None:
    tool = _minimal_valid_tool(unexpected_field="nope")
    with pytest.raises(ValidationError):
        ToolDefinition.model_validate(tool)


# ══════════════════════════════════════════════════════════════════════
# Day 13 Task 2 -- the `ToolRegistry` service.
# ══════════════════════════════════════════════════════════════════════


class TestToolRegistryLoad:
    def test_loads_the_committed_registry(self) -> None:
        registry = ToolRegistry.load(COMMITTED_REGISTRY_PATH)
        assert registry.registry_version == "1.0"
        assert len(registry.tools) == 2

    def test_default_path_loads_from_repository_root(self) -> None:
        """`uv run` always runs from the repository root, so the default
        `DEFAULT_REGISTRY_PATH` (`tools/registry.v1.json`, relative) must
        resolve without an explicit path when invoked from there."""
        original_cwd = os.getcwd()
        os.chdir(REPO_ROOT)
        try:
            registry = ToolRegistry.load()
        finally:
            os.chdir(original_cwd)
        assert registry.registry_version == "1.0"

    def test_missing_file_raises_load_error(self, tmp_path: Path) -> None:
        with pytest.raises(ToolRegistryLoadError, match="not found"):
            ToolRegistry.load(tmp_path / "does_not_exist.json")

    def test_invalid_json_raises_load_error(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "registry.json"
        bad_file.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ToolRegistryLoadError, match="not valid JSON"):
            ToolRegistry.load(bad_file)

    def test_schema_invalid_content_raises_load_error(self, tmp_path: Path) -> None:
        """A structurally-invalid registry (Task 1's own validation, e.g.
        a duplicate tool/version) fails to *load* as a `ToolRegistry`,
        never as a silently-degraded partial registry."""
        registry = _minimal_valid_registry()
        registry["tools"].append(copy.deepcopy(registry["tools"][0]))
        bad_file = tmp_path / "registry.json"
        bad_file.write_text(json.dumps(registry), encoding="utf-8")
        with pytest.raises(ToolRegistryLoadError, match="failed validation"):
            ToolRegistry.load(bad_file)


class TestToolRegistryLookup:
    @pytest.fixture()
    def registry(self) -> ToolRegistry:
        return ToolRegistry.load(COMMITTED_REGISTRY_PATH)

    def test_has_tool_and_has_tool_version(self, registry: ToolRegistry) -> None:
        assert registry.has_tool("supplier_status_lookup") is True
        assert registry.has_tool("model_invented_tool") is False
        assert registry.has_tool_version("supplier_status_lookup", "1.0.0") is True
        assert registry.has_tool_version("supplier_status_lookup", "9.9.9") is False
        assert registry.has_tool_version("model_invented_tool", "1.0.0") is False

    def test_versions_for_known_tool(self, registry: ToolRegistry) -> None:
        assert registry.versions_for("supplier_status_lookup") == ("1.0.0",)

    def test_versions_for_unknown_tool_raises_tool_not_found(self, registry: ToolRegistry) -> None:
        with pytest.raises(ToolNotFoundError):
            registry.versions_for("model_invented_tool")

    def test_get_tool_exact_version_returns_typed_definition(self, registry: ToolRegistry) -> None:
        tool = registry.get_tool("supplier_status_lookup", "1.0.0")
        assert isinstance(tool, ToolDefinition)
        assert tool.owner == "AICO Synthetic Procurement Platform"
        assert tool.required_permissions == ["read_structured_supplier"]

    def test_get_tool_unknown_tool_id_raises_tool_not_found(self, registry: ToolRegistry) -> None:
        with pytest.raises(ToolNotFoundError) as exc_info:
            registry.get_tool("model_invented_tool", "9.9.9")
        assert exc_info.value.tool_id == "model_invented_tool"

    def test_get_tool_unknown_version_raises_version_not_found(self, registry: ToolRegistry) -> None:
        """A registered tool_id at an unregistered version is a distinct
        failure from an entirely unknown tool_id (Day 13's own
        `tool_not_found` vs `version_not_found` normalized error
        categories, Task 10)."""
        with pytest.raises(ToolVersionNotFoundError) as exc_info:
            registry.get_tool("supplier_status_lookup", "9.9.9")
        assert exc_info.value.tool_id == "supplier_status_lookup"
        assert exc_info.value.tool_version == "9.9.9"

    def test_get_tool_unknown_version_does_not_raise_tool_not_found(self, registry: ToolRegistry) -> None:
        with pytest.raises(ToolVersionNotFoundError):
            try:
                registry.get_tool("supplier_status_lookup", "9.9.9")
            except ToolNotFoundError:
                pytest.fail("expected ToolVersionNotFoundError, not ToolNotFoundError")


class TestToolRegistryActiveDisabledState:
    @pytest.fixture()
    def registry(self) -> ToolRegistry:
        return ToolRegistry.load(COMMITTED_REGISTRY_PATH)

    def test_active_tool_is_active(self, registry: ToolRegistry) -> None:
        assert registry.is_active("supplier_status_lookup", "1.0.0") is True

    def test_disabled_tool_is_not_active(self, registry: ToolRegistry) -> None:
        assert registry.is_active("supplier_record_update", "1.0.0") is False

    def test_is_active_still_fails_closed_for_unknown_tool(self, registry: ToolRegistry) -> None:
        with pytest.raises(ToolNotFoundError):
            registry.is_active("model_invented_tool", "1.0.0")

    def test_is_active_still_fails_closed_for_unknown_version(self, registry: ToolRegistry) -> None:
        with pytest.raises(ToolVersionNotFoundError):
            registry.is_active("supplier_status_lookup", "9.9.9")


class TestToolRegistryReadOnly:
    def test_tools_property_returns_independent_tuple(self) -> None:
        """`tools` must not expose a mutable reference into the loaded
        document -- mutating the returned collection must not affect the
        registry's own state (Task 2: "remain read-only at runtime")."""
        registry = ToolRegistry.load(COMMITTED_REGISTRY_PATH)
        tools = registry.tools
        assert isinstance(tools, tuple)

        mutable_copy = list(tools)
        mutable_copy.clear()

        assert len(registry.tools) == 2

    def test_no_mutator_method_exists(self) -> None:
        """Request/model output cannot dynamically register a tool: there
        is no `register_tool()`/`add_tool()`/`set_status()` (or similarly
        named) method on `ToolRegistry` at all."""
        registry = ToolRegistry.load(COMMITTED_REGISTRY_PATH)
        forbidden_method_names = ("register_tool", "add_tool", "set_status", "update_tool", "remove_tool")
        for name in forbidden_method_names:
            assert not hasattr(registry, name)
