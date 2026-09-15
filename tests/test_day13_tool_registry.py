"""
Day 13 Task 1 -- the typed Tool Registry model (`ToolDefinition` /
`ToolRegistryDocument`, `src/aico/tools/models.py`).

Mirrors `test_day12_gate_d_policy.py`'s style: the real committed pack
fixture (`data/day13_pack/fixtures/tool_registry_v1.json`) proves a valid
registry becomes typed objects end to end, and
`registry_validation_cases.json`'s own named mutations
(REG13-001..REG13-005) drive the negative-path assertions -- each case is
replayed against a hand-built minimal valid registry rather than the
fixture file itself, so every test only ever changes the one thing it is
proving is rejected. Task 2's `registry.py` (load-from-file, exact-version
lookup, active/disabled state) is out of scope here -- this file only
proves the typed model and its own validation.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aico.tools.models import (
    RetryableFailureCategory,
    RetryPolicy,
    RiskLevel,
    ToolDefinition,
    ToolRegistryDocument,
    ToolStatus,
    ToolTransportKind,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
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
