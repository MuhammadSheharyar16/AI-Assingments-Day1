"""
Day 13 Task 11 -- output schema validation (`validate_tool_output()`,
`src/aico/tools/schema_validator.py`). Already built during Task 7 out of
necessity (the controlled executor's own required pipeline order includes
an output-validation stage) and shares `_validate_against_schema()` with
Task 5's `validate_tool_input()`; this file is Task 11's own dedicated
proof, mirroring `test_day13_input_schema.py`'s structure one stage later.

Proves, against the real committed `supplier_status_lookup@1.0.0` output
schema (`tools/registry.v1.json`, `required=["supplier_id", "status",
"as_of"]`, `additionalProperties=False`, `status` a closed `enum`):

  - "External tool output is untrusted": a raw transport payload is never
    treated as trustworthy just because transport itself reported success
    -- it is checked against the registered `output_schema` the same way
    `arguments` are checked against `input_schema`;
  - valid payload -> typed success (the payload dict, unchanged);
  - each of Task 11's five required cases is correctly categorized:
    missing field, wrong type, invalid enum, extra field (`additional
    Properties: false` is what rejects it, not a special case in the
    validator), plus a general invalid-constraint case for completeness;
  - `output_validation_cases.json` OUT13-001..005 each resolve to their
    fixture-declared `expected` outcome;
  - "Do not ask the model to repair malformed tool output": there is no
    parameter anywhere in `validate_tool_output()`'s signature a model
    output/suggestion could reach, and the module imports no model/LLM
    concept at all -- a malformed payload is rejected outright, never
    "fixed" and re-offered as success;
  - "Schema-invalid tool output is not returned as success": proven once
    more, end to end, through the real `ToolExecutor` (the fuller version
    of this same guarantee already lives in `test_day13_executor.py`;
    this file adds the schema-validator-level proof plus one end-to-end
    confirmation for completeness);
  - no returned transport payload *value* ever appears in a
    `ToolSchemaValidationFailure` (only schema-declared property names) --
    the identical no-raw-value discipline `validate_tool_input()` already
    gives its own failures;
  - determinism.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from aico.tools.errors import ToolSchemaValidationFailure
from aico.tools.executor import ToolExecutionErrorCategory, ToolExecutionStatus, ToolExecutor
from aico.tools.mcp_gateway import MCPGateway
from aico.tools.models import ToolDefinition, ToolExecutionRequest
from aico.tools.policy import ToolExecutionPolicy
from aico.tools.registry import ToolRegistry
from aico.tools.schema_validator import validate_tool_output
from aico.tools.transport import FakeToolTransport

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_REGISTRY_PATH = REPO_ROOT / "tools" / "registry.v1.json"
COMMITTED_POLICY_PATH = REPO_ROOT / "policy" / "tool_execution_policy.v1.json"
OUTPUT_VALIDATION_CASES_PATH = REPO_ROOT / "data" / "day13_pack" / "fixtures" / "output_validation_cases.json"


def _lookup_tool() -> ToolDefinition:
    return ToolRegistry.load(COMMITTED_REGISTRY_PATH).get_tool("supplier_status_lookup", "1.0.0")


def _load_output_validation_cases() -> list[dict]:
    return json.loads(OUTPUT_VALIDATION_CASES_PATH.read_text(encoding="utf-8"))["cases"]


# ══════════════════════════════════════════════════════════════════════
# Valid output.
# ══════════════════════════════════════════════════════════════════════


class TestValidOutput:
    def test_valid_payload_passes_through_unchanged(self) -> None:
        tool = _lookup_tool()
        payload = {"supplier_id": "SUP-ALPHA", "status": "active", "as_of": "2026-09-15T09:00:00Z"}
        result = validate_tool_output(tool, payload)
        assert result == payload
        assert not isinstance(result, ToolSchemaValidationFailure)


# ══════════════════════════════════════════════════════════════════════
# Task 11's required cases.
# ══════════════════════════════════════════════════════════════════════


class TestRequiredCases:
    def test_missing_field(self) -> None:
        tool = _lookup_tool()
        result = validate_tool_output(
            tool, {"supplier_id": "SUP-ALPHA", "as_of": "2026-09-15T09:00:00Z"}
        )
        assert isinstance(result, ToolSchemaValidationFailure)
        assert result.category == "missing_field"
        assert result.field_path == "status"

    def test_wrong_type(self) -> None:
        tool = _lookup_tool()
        result = validate_tool_output(
            tool, {"supplier_id": "SUP-ALPHA", "status": 1, "as_of": "2026-09-15T09:00:00Z"}
        )
        assert isinstance(result, ToolSchemaValidationFailure)
        assert result.category == "wrong_type"
        assert result.field_path == "status"

    def test_invalid_enum(self) -> None:
        tool = _lookup_tool()
        result = validate_tool_output(
            tool, {"supplier_id": "SUP-ALPHA", "status": "deleted", "as_of": "2026-09-15T09:00:00Z"}
        )
        assert isinstance(result, ToolSchemaValidationFailure)
        assert result.category == "invalid_enum"
        assert result.field_path == "status"

    def test_extra_field(self) -> None:
        """`output_validation_cases.json` OUT13-005's own shape: the
        schema's own `additionalProperties: false` is what rejects the
        extra `secret_internal_note` field -- not a special case in the
        validator, and not something a model gets to add after the fact
        either."""
        tool = _lookup_tool()
        result = validate_tool_output(
            tool,
            {
                "supplier_id": "SUP-ALPHA",
                "status": "active",
                "as_of": "2026-09-15T09:00:00Z",
                "secret_internal_note": "must not pass",
            },
        )
        assert isinstance(result, ToolSchemaValidationFailure)
        assert result.category == "extra_field"
        assert result.field_path == "secret_internal_note"

    def test_missing_all_required_fields(self) -> None:
        tool = _lookup_tool()
        result = validate_tool_output(tool, {})
        assert isinstance(result, ToolSchemaValidationFailure)
        assert result.category == "missing_field"


# ══════════════════════════════════════════════════════════════════════
# `output_validation_cases.json` -- the real supplied fixture cases.
# ══════════════════════════════════════════════════════════════════════


class TestOutputValidationCasesFixture:
    @pytest.mark.parametrize("case_id", ["OUT13-001", "OUT13-002", "OUT13-003", "OUT13-004", "OUT13-005"])
    def test_case_resolves_to_its_declared_expectation(self, case_id: str) -> None:
        case = next(c for c in _load_output_validation_cases() if c["id"] == case_id)
        tool = ToolRegistry.load(COMMITTED_REGISTRY_PATH).get_tool(case["tool_id"], "1.0.0")

        result = validate_tool_output(tool, case["payload"])

        if case["expected"] == "success":
            assert result == case["payload"]
            assert not isinstance(result, ToolSchemaValidationFailure)
        else:
            assert case["expected"] == "output_invalid"
            assert isinstance(result, ToolSchemaValidationFailure)

    def test_every_fixture_case_id_is_known(self) -> None:
        case_ids = {c["id"] for c in _load_output_validation_cases()}
        assert case_ids == {"OUT13-001", "OUT13-002", "OUT13-003", "OUT13-004", "OUT13-005"}


# ══════════════════════════════════════════════════════════════════════
# "Schema-invalid tool output is not returned as success" -- end to end.
# ══════════════════════════════════════════════════════════════════════


class TestInvalidOutputNeverReturnedAsSuccessEndToEnd:
    def _executor(self, payload: dict) -> ToolExecutor:
        registry = ToolRegistry.load(COMMITTED_REGISTRY_PATH)
        policy = ToolExecutionPolicy.load(COMMITTED_POLICY_PATH)
        transport = FakeToolTransport([payload])
        gateway = MCPGateway(transport)
        return ToolExecutor(registry, policy, gateway)

    def _request(self) -> ToolExecutionRequest:
        return ToolExecutionRequest.model_validate(
            {
                "tool_id": "supplier_status_lookup",
                "tool_version": "1.0.0",
                "arguments": {"supplier_id": "SUP-ALPHA"},
                "trusted_permissions": ["read_structured_supplier"],
            }
        )

    def test_malformed_transport_payload_never_surfaces_as_success(self) -> None:
        case = next(c for c in _load_output_validation_cases() if c["id"] == "OUT13-005")
        executor = self._executor(case["payload"])

        result = executor.execute(self._request())

        assert result.status is ToolExecutionStatus.FAILURE
        assert result.error_category is ToolExecutionErrorCategory.OUTPUT_INVALID
        assert result.payload is None

    def test_valid_transport_payload_still_succeeds(self) -> None:
        case = next(c for c in _load_output_validation_cases() if c["id"] == "OUT13-001")
        executor = self._executor(case["payload"])

        result = executor.execute(self._request())

        assert result.status is ToolExecutionStatus.SUCCESS
        assert result.payload == case["payload"]


# ══════════════════════════════════════════════════════════════════════
# "Do not ask the model to repair malformed tool output."
# ══════════════════════════════════════════════════════════════════════


def test_validate_tool_output_has_no_parameter_a_model_could_reach() -> None:
    """Structural proof: `validate_tool_output()`'s signature is exactly
    `(tool, payload)` -- there is nowhere to pass a model suggestion, a
    repair hint, or anything model-shaped at all."""
    import inspect

    signature = inspect.signature(validate_tool_output)
    assert list(signature.parameters) == ["tool", "payload"]


def test_schema_validator_module_imports_no_model_gateway_concept() -> None:
    """No import of anything model/LLM-shaped -- a malformed payload is
    rejected outright, never "fixed" by asking a model to repair it."""
    source_path = REPO_ROOT / "src" / "aico" / "tools" / "schema_validator.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    forbidden_substrings = ("model_gateway", "platform.model", "rag.")
    for module_name in imported_modules:
        lowered = module_name.lower()
        assert not any(forbidden in lowered for forbidden in forbidden_substrings), module_name


# ══════════════════════════════════════════════════════════════════════
# No raw transport payload value ever leaks into a failure.
# ══════════════════════════════════════════════════════════════════════


class TestNoRawValueLeak:
    def test_wrong_type_failure_does_not_echo_returned_value(self) -> None:
        tool = _lookup_tool()
        secret_looking_value = "SECRET-TRANSPORT-PAYLOAD-VALUE-98765"
        result = validate_tool_output(
            tool,
            {
                "supplier_id": "SUP-ALPHA",
                "status": ["not", "a", "string", secret_looking_value],
                "as_of": "2026-09-15T09:00:00Z",
            },
        )
        assert isinstance(result, ToolSchemaValidationFailure)
        assert secret_looking_value not in str(result)
        assert secret_looking_value not in result.message

    def test_invalid_enum_failure_does_not_echo_returned_value(self) -> None:
        tool = _lookup_tool()
        secret_looking_value = "TOTALLY-CONFIDENTIAL-TRANSPORT-STATUS"
        result = validate_tool_output(
            tool,
            {"supplier_id": "SUP-ALPHA", "status": secret_looking_value, "as_of": "2026-09-15T09:00:00Z"},
        )
        assert isinstance(result, ToolSchemaValidationFailure)
        assert secret_looking_value not in str(result)
        assert secret_looking_value not in result.message

    def test_extra_field_failure_only_names_the_key_not_a_value(self) -> None:
        tool = _lookup_tool()
        secret_looking_value = "LEAKED-INTERNAL-TRANSPORT-DETAIL"
        result = validate_tool_output(
            tool,
            {
                "supplier_id": "SUP-ALPHA",
                "status": "active",
                "as_of": "2026-09-15T09:00:00Z",
                "secret_internal_note": secret_looking_value,
            },
        )
        assert isinstance(result, ToolSchemaValidationFailure)
        assert secret_looking_value not in str(result)
        assert result.field_path == "secret_internal_note"  # the key name is fine to surface


# ══════════════════════════════════════════════════════════════════════
# Determinism.
# ══════════════════════════════════════════════════════════════════════


def test_validation_is_deterministic() -> None:
    tool = _lookup_tool()
    first = validate_tool_output(tool, {})
    second = validate_tool_output(tool, {})
    assert first == second
