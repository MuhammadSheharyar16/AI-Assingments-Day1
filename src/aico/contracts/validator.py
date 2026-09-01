"""
Day 4 Task 2 — contract / schema validation.

Turns a raw model response string into either a typed Pydantic contract or
a typed `ValidationFailure` (`stage` in {"parse", "contract"}) - never an
unchecked dict, and never a bare exception escaping to a caller. This is
the only place in the contract layer that calls `json.loads` /
`Model.model_validate` on model output - nothing else should deserialize
model JSON ad hoc (working rule: "Do not deserialize model JSON ad hoc
outside the contract layer").

Two stages, matching the assignment's own pipeline diagram:

    raw text -> Parse -> Contract/schema validation -> typed contract

`parse_json` handles stage 1: malformed JSON, and the one documented
bounded markdown-fence unwrap (see its docstring - Task 5's
`markdown_wrapped_json` case). `validate_contract` handles stage 2 by
delegating entirely to Pydantic (missing/extra field, wrong type, invalid
enum, out-of-range constraint all come from the models in `models.py`,
never duplicated here) and translating Pydantic's `ValidationError` into
`ValidationFailure` values so a caller never needs to know Pydantic's own
exception shape. `parse_and_validate` composes both stages - the entry
point the rest of the contract layer (Task 4's repair, the eventual
service.py) should call.

Parsing JSON successfully is not enough on its own - a syntactically valid
JSON object that is the wrong shape must still be rejected, which is why
`parse_and_validate` never returns early just because `parse_json`
succeeded.
"""
from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from aico.contracts.errors import ValidationFailure

T = TypeVar("T", bound=BaseModel)

# One clearly documented bounded unwrap (Task 5's markdown_wrapped_json
# case): a ```json ... ``` or ``` ... ``` fence wrapping exactly one JSON
# value, with nothing else outside the fence but surrounding whitespace.
# Anything looser than that - prose before/after the fence, several
# fences, no fence at all around non-JSON text - is rejected rather than
# guessed at, per the working rule "do not silently accept arbitrary prose
# around JSON."
_MARKDOWN_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*\n(?P<body>.*?)\n```\s*$", re.DOTALL | re.IGNORECASE
)


def _strip_markdown_fence(raw: str) -> str | None:
    match = _MARKDOWN_FENCE_RE.match(raw)
    return match.group("body") if match else None


def parse_json(raw: str) -> dict | ValidationFailure:
    """Parse `raw` into a JSON object (a dict - a top-level JSON array,
    string, number, etc. is rejected too, since no Day 4 contract is
    ever anything but an object). Tries the raw text first; only on
    failure tries exactly one bounded markdown-fence unwrap of the same
    text before giving up - see `_MARKDOWN_FENCE_RE`."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        unwrapped = _strip_markdown_fence(raw)
        if unwrapped is None:
            return ValidationFailure(
                stage="parse",
                category="malformed_json",
                message="response body is not valid JSON",
            )
        try:
            data = json.loads(unwrapped)
        except json.JSONDecodeError:
            return ValidationFailure(
                stage="parse",
                category="malformed_json",
                message="response body is not valid JSON (including inside its markdown fence)",
            )

    if not isinstance(data, dict):
        return ValidationFailure(
            stage="parse",
            category="malformed_json",
            message=f"parsed JSON is a {type(data).__name__}, expected an object",
        )
    return data


# Maps Pydantic v2's ValidationError "type" strings onto Day 4's six named
# contract-failure categories. Anything not listed here still gets a
# typed failure - just category "other_contract" - so an error type this
# repository doesn't yet name explicitly is never silently dropped, only
# less specifically categorized than the six the assignment names.
_PYDANTIC_TYPE_TO_CATEGORY: dict[str, str] = {
    "missing": "missing_field",
    "extra_forbidden": "extra_field",
    "enum": "invalid_enum",
    "literal_error": "invalid_enum",
    "string_type": "wrong_type",
    "int_type": "wrong_type",
    "float_type": "wrong_type",
    "bool_type": "wrong_type",
    "list_type": "wrong_type",
    "dict_type": "wrong_type",
    "model_type": "wrong_type",
    "string_too_short": "out_of_range",
    "string_too_long": "out_of_range",
    "too_short": "out_of_range",
    "too_long": "out_of_range",
    "greater_than": "out_of_range",
    "greater_than_equal": "out_of_range",
    "less_than": "out_of_range",
    "less_than_equal": "out_of_range",
}


def _category_for(error_type: str) -> str:
    return _PYDANTIC_TYPE_TO_CATEGORY.get(error_type, "other_contract")


def _field_path(loc: tuple) -> str | None:
    return ".".join(str(part) for part in loc) if loc else None


def validate_contract(data: dict, model: type[T]) -> T | ValidationFailure:
    """Validate an already-parsed JSON object against `model`
    (`extra="forbid"` on every Day 4 model, so unknown fields are
    rejected here too - see `models.py`). Returns the typed contract on
    success, or the *first* Pydantic error translated into a
    `ValidationFailure` on failure - first, not all: Task 4's repair path
    needs one concrete, actionable error to build its repair request
    from, and every broken-output fixture breaks exactly one thing at a
    time."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        first = exc.errors(include_url=False)[0]
        return ValidationFailure(
            stage="contract",
            category=_category_for(first["type"]),
            message=first["msg"],
            field_path=_field_path(first["loc"]),
        )


def parse_and_validate(raw: str, model: type[T]) -> T | ValidationFailure:
    """The full Task 2 pipeline for one raw model response: parse ->
    contract/schema validate. Callers in the contract layer (Task 4's
    repair, the eventual service.py) should call this instead of
    composing `parse_json`/`validate_contract` themselves."""
    parsed = parse_json(raw)
    if isinstance(parsed, ValidationFailure):
        return parsed
    return validate_contract(parsed, model)
