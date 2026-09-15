"""
Day 13 Task 5 -- input schema validation.
Day 13 Task 7 (necessarily, to complete the executor's own required
pipeline order) -- output schema validation, `validate_tool_output()`,
formalized in full by Task 11.

`validate_tool_input()` is the one place a `ToolExecutionRequest.arguments`
value is ever checked against its resolved tool's own registered
`input_schema` (Task 1's `ToolDefinition.input_schema`) -- turning it into
either the validated arguments dict or a typed, sanitized
`ToolSchemaValidationFailure` (`errors.py`), never a bare `jsonschema`
exception escaping to a caller and never an unchecked dict passed through
untouched. Mirrors `aico.contracts.validator.validate_contract()`'s own
`Model | ValidationFailure` shape exactly -- this is Day 13's analog one
boundary over, for tool arguments instead of a model's JSON response.
`validate_tool_output()` is the identical shape, one stage later, against
`tool.output_schema` -- both share `_validate_against_schema()` below, the
one place a payload is ever checked against a JSON Schema object at all.

Critical (`Day 13 Task.pdf`, TASK 5): "invalid input -> transport calls =
0". This module only validates; it has no reference to a transport or the
MCP Gateway at all, and calls neither -- Task 7's controlled executor is
the one place that reads this function's result and is expected to stop
*before* the gateway whenever it is a `ToolSchemaValidationFailure` rather
than a dict, which is what actually makes "transport is not the first
validator" true end to end. `validate_tool_output()`'s own critical
property (Task 11): a `ToolSchemaValidationFailure` is never mistaken for
or silently repaired into a successful payload -- "invalid output is not
returned as success" reduces directly to a caller pattern-matching this
function's return type, exactly the way "invalid input -> transport calls
= 0" reduces to a caller checking `validate_tool_input()`'s return type
before ever reaching the gateway.

Required cases (`tool_registry_v1.json`'s own `supplier_status_lookup.
input_schema`, `required=["supplier_id"]`, `additionalProperties=False`,
`supplier_id` a `pattern`/`minLength`/`maxLength`-constrained string):

    - valid input                    -> the arguments dict, unchanged.
    - missing required field         -> category `missing_field`.
    - wrong type                     -> category `wrong_type`.
    - extra property                 -> category `extra_field`
                                         (`execution_cases.json` EXEC13-003:
                                         `arguments={"supplier_id": ...,
                                         "role": "admin"}` -- the schema's
                                         own `additionalProperties: false`
                                         is what actually rejects the
                                         injected `role`, not a special
                                         case in this module).
    - invalid enum/constraint        -> category `invalid_enum` (an
                                         `enum` mismatch) or
                                         `invalid_constraint`
                                         (`pattern`/`minLength`/`maxLength`/
                                         `minimum`/... and everything else
                                         `jsonschema` can report).

Only the single most relevant failure is ever returned
(`jsonschema.exceptions.best_match`), the identical "one concrete,
actionable error, not an exhaustive list" choice
`aico.contracts.validator.validate_contract()` already makes for the
identical reason: a caller (Task 7's executor, Task 10's normalized
`input_invalid` error) needs one field/reason to report, not a batch.

## Why this module never uses `jsonschema`'s own generated `.message`
verbatim

`jsonschema` embeds the actual offending value in its own messages for
`type`/`enum`/most constraint keywords (e.g. `"123 is not of type
'string'"`, `"'deleted' is not one of [...]"`) -- exactly the raw
tool-argument content the working rule against dumping protected data into
default logs is about. `required`/`additionalProperties` messages only
ever name schema-declared *property keys* (never a submitted value), so
those two are safe to read from `jsonschema`'s message as-is; every other
category gets a fixed, value-free template message instead (`_describe_error`
below) -- a `ToolSchemaValidationFailure` is safe to log/return by
construction, not by a redaction step applied after the fact."""
from __future__ import annotations

import re
from typing import Any

import jsonschema
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from aico.tools.errors import ToolSchemaValidationFailure
from aico.tools.models import ToolDefinition

# Every `jsonschema` validator keyword this module treats as a bounded
# value/shape constraint distinct from `type`/`enum` -- reported as
# category `invalid_constraint`, never with the offending value echoed.
_CONSTRAINT_VALIDATORS = frozenset(
    {
        "pattern",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minItems",
        "maxItems",
        "uniqueItems",
        "multipleOf",
        "minProperties",
        "maxProperties",
        "format",
    }
)

_QUOTED_NAME_RE = re.compile(r"'([^']*)'")


def _quoted_names(text: str) -> list[str]:
    """Extract single-quoted names from a `jsonschema` message -- safe to
    do for `required`/`additionalProperties` specifically, since both only
    ever quote schema-declared property *keys*, never a submitted value."""
    return _QUOTED_NAME_RE.findall(text)


def _describe_error(error: JsonSchemaValidationError) -> ToolSchemaValidationFailure:
    """The one place a `jsonschema.ValidationError` is ever translated into
    a `ToolSchemaValidationFailure` -- see module docstring for why most
    categories get a fixed template message rather than `error.message`
    verbatim."""
    path = ".".join(str(part) for part in error.absolute_path)
    validator = error.validator

    if validator == "required":
        names = _quoted_names(error.message)
        missing = names[0] if names else None
        return ToolSchemaValidationFailure(
            category="missing_field",
            message="required property is missing",
            field_path=missing or (path or None),
        )

    if validator == "additionalProperties":
        names = _quoted_names(error.message)
        plural = "properties" if len(names) != 1 else "property"
        message = f"unexpected additional {plural}: {', '.join(names)}" if names else "unexpected additional property"
        return ToolSchemaValidationFailure(
            category="extra_field",
            message=message,
            field_path=", ".join(names) if names else (path or None),
        )

    if validator == "type":
        return ToolSchemaValidationFailure(
            category="wrong_type", message="value does not match the required type", field_path=path or None
        )

    if validator in ("enum", "const"):
        return ToolSchemaValidationFailure(
            category="invalid_enum", message="value is not one of the allowed values", field_path=path or None
        )

    if validator in _CONSTRAINT_VALIDATORS:
        return ToolSchemaValidationFailure(
            category="invalid_constraint",
            message=f"value does not satisfy the {validator!r} constraint",
            field_path=path or None,
        )

    return ToolSchemaValidationFailure(
        category="other",
        message=f"value does not satisfy schema constraint {validator!r}",
        field_path=path or None,
    )


def _validate_against_schema(
    schema: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any] | ToolSchemaValidationFailure:
    """The one place any payload is ever checked against a JSON Schema
    object in this module -- shared by `validate_tool_input()` (Task 5)
    and `validate_tool_output()` (Task 7/11). Returns `payload` unchanged
    on success, or the single most relevant `ToolSchemaValidationFailure`
    on failure (`jsonschema.exceptions.best_match`)."""
    validator = jsonschema.Draft7Validator(schema)
    error = jsonschema.exceptions.best_match(validator.iter_errors(payload))
    if error is not None:
        return _describe_error(error)
    return payload


def validate_tool_input(
    tool: ToolDefinition, arguments: dict[str, Any]
) -> dict[str, Any] | ToolSchemaValidationFailure:
    """Validate `arguments` against `tool.input_schema` (already
    structurally validated at registration time, Task 1 --
    `_validate_object_schema_shape`). Returns `arguments` unchanged on
    success, or the single most relevant `ToolSchemaValidationFailure` on
    failure. Never raises for an ordinary invalid-input case, and never
    calls anything outside this module -- see module docstring's
    "Critical" paragraph for why that is what makes "transport is not the
    first validator" true."""
    return _validate_against_schema(tool.input_schema, arguments)


def validate_tool_output(
    tool: ToolDefinition, payload: dict[str, Any]
) -> dict[str, Any] | ToolSchemaValidationFailure:
    """Validate a transport call's raw success `payload` against
    `tool.output_schema` -- external, untrusted/unvalidated data (Day 13
    working rule: "Tool output is external/untrusted until validated")
    until this function says otherwise. Returns `payload` unchanged on
    success, or the single most relevant `ToolSchemaValidationFailure` on
    failure. Never raises, and never repairs or drops an offending field
    to make a malformed payload superficially valid -- Day 13 working
    rule: "Schema-invalid tool output is not returned as success" / "Do
    not ask the model to repair malformed tool output" (there is nothing
    in this function's signature a model could even reach)."""
    return _validate_against_schema(tool.output_schema, payload)
