"""
Day 13 Task 5 -- input schema validation (`validate_tool_input()`,
`src/aico/tools/schema_validator.py`).

Proves, against the real committed `supplier_status_lookup@1.0.0` and
`supplier_record_update@1.0.0` input schemas (`tools/registry.v1.json`):

  - valid input passes through unchanged;
  - each of the five required cases (`Day 13 Task.pdf`, TASK 5) is
    correctly categorized: missing required field, wrong type, extra
    property, invalid enum, invalid constraint (pattern/length);
  - `execution_cases.json`'s own EXEC13-002/EXEC13-003 shapes categorize as
    expected (`missing_field`/`extra_field`, both mapping onto Task 10's
    future `input_invalid` normalized category);
  - no submitted argument *value* ever appears in a returned
    `ToolSchemaValidationFailure` (only schema-declared property names/
    fixed category text) -- the working rule against dumping protected
    tool-argument content into default logs;
  - determinism;
  - the structural half of "transport is not the first validator": this
    module imports no transport/gateway concept at all.

Task 7 (the controlled executor that is expected to call this before the
MCP Gateway and stop on a `ToolSchemaValidationFailure`) is out of scope
here -- this file only proves the validator itself.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from aico.tools.errors import SCHEMA_VALIDATION_CATEGORIES, ToolSchemaValidationFailure
from aico.tools.models import ToolDefinition
from aico.tools.registry import ToolRegistry
from aico.tools.schema_validator import validate_tool_input

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_REGISTRY_PATH = REPO_ROOT / "tools" / "registry.v1.json"
EXECUTION_CASES_PATH = REPO_ROOT / "data" / "day13_pack" / "fixtures" / "execution_cases.json"


def _lookup_tool() -> ToolDefinition:
    return ToolRegistry.load(COMMITTED_REGISTRY_PATH).get_tool("supplier_status_lookup", "1.0.0")


def _update_tool() -> ToolDefinition:
    """The disabled side-effecting lab tool -- schema validation itself is
    status-agnostic (Task 4's policy is what turns `disabled` into a
    deny), so its schema is still exercised here on its own terms."""
    return ToolRegistry.load(COMMITTED_REGISTRY_PATH).get_tool("supplier_record_update", "1.0.0")


def _load_execution_cases() -> dict:
    return json.loads(EXECUTION_CASES_PATH.read_text(encoding="utf-8"))["cases"]


# ══════════════════════════════════════════════════════════════════════
# Valid input.
# ══════════════════════════════════════════════════════════════════════


class TestValidInput:
    def test_valid_arguments_pass_through_unchanged(self) -> None:
        tool = _lookup_tool()
        arguments = {"supplier_id": "SUP-ALPHA"}
        result = validate_tool_input(tool, arguments)
        assert result == {"supplier_id": "SUP-ALPHA"}
        assert not isinstance(result, ToolSchemaValidationFailure)

    def test_valid_update_arguments_pass_for_disabled_tool_schema(self) -> None:
        """Schema validation does not itself know or care that this tool
        is disabled -- Task 4's policy layer is the one that denies it."""
        tool = _update_tool()
        result = validate_tool_input(tool, {"supplier_id": "SUP-ALPHA", "status": "inactive"})
        assert result == {"supplier_id": "SUP-ALPHA", "status": "inactive"}


# ══════════════════════════════════════════════════════════════════════
# Task 5's five required cases.
# ══════════════════════════════════════════════════════════════════════


class TestRequiredCases:
    def test_missing_required_field(self) -> None:
        tool = _lookup_tool()
        result = validate_tool_input(tool, {})
        assert isinstance(result, ToolSchemaValidationFailure)
        assert result.category == "missing_field"
        assert result.field_path == "supplier_id"

    def test_wrong_type(self) -> None:
        tool = _lookup_tool()
        result = validate_tool_input(tool, {"supplier_id": 12345})
        assert isinstance(result, ToolSchemaValidationFailure)
        assert result.category == "wrong_type"
        assert result.field_path == "supplier_id"

    def test_extra_property(self) -> None:
        """`execution_cases.json` EXEC13-003's own shape: the schema's own
        `additionalProperties: false` is what rejects the injected `role`
        -- not a special case in the validator."""
        tool = _lookup_tool()
        result = validate_tool_input(tool, {"supplier_id": "SUP-ALPHA", "role": "admin"})
        assert isinstance(result, ToolSchemaValidationFailure)
        assert result.category == "extra_field"
        assert result.field_path == "role"

    def test_invalid_enum(self) -> None:
        tool = _update_tool()
        result = validate_tool_input(tool, {"supplier_id": "SUP-ALPHA", "status": "deleted"})
        assert isinstance(result, ToolSchemaValidationFailure)
        assert result.category == "invalid_enum"
        assert result.field_path == "status"

    def test_invalid_constraint_pattern(self) -> None:
        tool = _lookup_tool()
        result = validate_tool_input(tool, {"supplier_id": "not-a-valid-id"})
        assert isinstance(result, ToolSchemaValidationFailure)
        assert result.category == "invalid_constraint"
        assert result.field_path == "supplier_id"

    def test_invalid_constraint_min_length(self) -> None:
        tool = _lookup_tool()
        result = validate_tool_input(tool, {"supplier_id": "SU"})
        assert isinstance(result, ToolSchemaValidationFailure)
        assert result.category == "invalid_constraint"

    @pytest.mark.parametrize("category", SCHEMA_VALIDATION_CATEGORIES)
    def test_every_declared_category_is_a_real_string(self, category: str) -> None:
        assert isinstance(category, str) and category


# ══════════════════════════════════════════════════════════════════════
# `execution_cases.json` -- the real supplied fixture cases.
# ══════════════════════════════════════════════════════════════════════


class TestExecutionCasesFixture:
    def test_missing_required_input_case_is_input_invalid(self) -> None:
        case = next(c for c in _load_execution_cases() if c["id"] == "EXEC13-002")
        assert case["expected"] == "input_invalid"
        tool = _lookup_tool()
        result = validate_tool_input(tool, case["request"]["arguments"])
        assert isinstance(result, ToolSchemaValidationFailure)
        assert result.category == "missing_field"

    def test_extra_privilege_argument_case_is_input_invalid(self) -> None:
        case = next(c for c in _load_execution_cases() if c["id"] == "EXEC13-003")
        assert case["expected"] == "input_invalid"
        tool = _lookup_tool()
        result = validate_tool_input(tool, case["request"]["arguments"])
        assert isinstance(result, ToolSchemaValidationFailure)
        assert result.category == "extra_field"

    def test_valid_lookup_case_passes(self) -> None:
        case = next(c for c in _load_execution_cases() if c["id"] == "EXEC13-001")
        tool = _lookup_tool()
        result = validate_tool_input(tool, case["request"]["arguments"])
        assert result == case["request"]["arguments"]


# ══════════════════════════════════════════════════════════════════════
# No raw argument value ever leaks into a failure.
# ══════════════════════════════════════════════════════════════════════


class TestNoRawValueLeak:
    def test_wrong_type_failure_does_not_echo_submitted_value(self) -> None:
        tool = _lookup_tool()
        secret_looking_value = "SECRET-TOKEN-DO-NOT-LOG-98765"
        result = validate_tool_input(tool, {"supplier_id": ["not", "a", "string", secret_looking_value]})
        assert isinstance(result, ToolSchemaValidationFailure)
        assert secret_looking_value not in str(result)
        assert secret_looking_value not in result.message

    def test_invalid_enum_failure_does_not_echo_submitted_value(self) -> None:
        tool = _update_tool()
        secret_looking_value = "TOTALLY-CONFIDENTIAL-STATUS-VALUE"
        result = validate_tool_input(tool, {"supplier_id": "SUP-ALPHA", "status": secret_looking_value})
        assert isinstance(result, ToolSchemaValidationFailure)
        assert secret_looking_value not in str(result)
        assert secret_looking_value not in result.message

    def test_pattern_failure_does_not_echo_submitted_value(self) -> None:
        tool = _lookup_tool()
        secret_looking_value = "SUPER-SECRET-RAW-ARGUMENT-VALUE"
        result = validate_tool_input(tool, {"supplier_id": secret_looking_value})
        assert isinstance(result, ToolSchemaValidationFailure)
        assert secret_looking_value not in str(result)
        assert secret_looking_value not in result.message

    def test_extra_field_failure_only_names_the_key_not_a_value(self) -> None:
        tool = _lookup_tool()
        secret_looking_value = "ADMIN-BACKDOOR-TOKEN-VALUE"
        result = validate_tool_input(
            tool, {"supplier_id": "SUP-ALPHA", "role": secret_looking_value}
        )
        assert isinstance(result, ToolSchemaValidationFailure)
        assert secret_looking_value not in str(result)
        assert result.field_path == "role"  # the key name is fine to surface


# ══════════════════════════════════════════════════════════════════════
# Determinism.
# ══════════════════════════════════════════════════════════════════════


def test_validation_is_deterministic() -> None:
    tool = _lookup_tool()
    first = validate_tool_input(tool, {})
    second = validate_tool_input(tool, {})
    assert first == second


# ══════════════════════════════════════════════════════════════════════
# "Transport is not the first validator" -- structural proof.
# ══════════════════════════════════════════════════════════════════════


def test_schema_validator_module_imports_no_transport_or_gateway_concept() -> None:
    """This module must be independently callable, with no dependency on
    the MCP Gateway or transport at all -- a static proof (source-level,
    not a runtime mock check) that nothing here could ever call transport,
    which is what makes "invalid input -> transport calls = 0" a property
    of the pipeline's own dependency structure, not just of today's test
    coverage."""
    source_path = REPO_ROOT / "src" / "aico" / "tools" / "schema_validator.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    forbidden_substrings = ("transport", "gateway", "mcp_gateway")
    for module_name in imported_modules:
        lowered = module_name.lower()
        assert not any(forbidden in lowered for forbidden in forbidden_substrings), module_name
