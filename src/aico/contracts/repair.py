"""
Day 4 Task 4 — one bounded repair attempt.

When a raw model response fails validation, this module decides whether
that failure is worth one repair call, builds the repair request from the
validation error, makes exactly one call through the Day 3 Model Gateway
(`aico.platform.model_gateway.ModelGateway` - the only model-call
boundary; this module never imports a provider SDK or calls a transport
directly), and revalidates the repaired response through the *complete*
pipeline again (Task 2's contract/schema validation, then Task 3's
semantic validation - both stages, not just the one that failed the first
time, since a "fix" could plausibly introduce a new problem at the other
stage).

The assignment's own diagram:

    Invalid response -> Validation error -> Repair allowed?
        no  -> typed error
        yes -> one repair call -> validate again -> typed result OR typed error

Bounded by construction, not by a counter: `attempt_repair` calls
`gateway.chat()` exactly once - there is no loop anywhere in this module,
so "repair never exceeds one attempt" holds even if every future change
to this file forgets to check a counter. `resolve()` calls
`attempt_repair` at most once per original response, and never calls it
again on the outcome of a repair - a repaired-but-still-invalid response
comes back as a typed failure, it is never fed back in for a second
repair. This is Day 4 output repair; it has nothing to do with Day 3's
provider-level retry (`ModelGateway`'s own bounded exponential backoff
for transport failures) - the two are orthogonal and never share a loop.

Repair policy (`is_repairable`): only `stage in {"contract", "semantic"}`
failures are attempted. Both carry a precise, structured diagnosis
(`category` + `field_path`) that a repair prompt can point the model at.
A `stage="parse"` failure (malformed JSON) has no such structure - the
model didn't produce a JSON object at all, so there is nothing concrete
for a single repair prompt to reference - and is therefore never
repaired; it returns as a typed failure immediately, with zero Model
Gateway calls. This is a lab-scoped, documented policy decision, not a
hard requirement from the brief.
"""
from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from aico.contracts.errors import ValidationFailure
from aico.contracts.models import CitedAnswer
from aico.contracts.semantic import validate_semantic
from aico.contracts.validator import parse_and_validate
from aico.platform.errors import ModelGatewayError
from aico.platform.model_gateway import ChatMessage, ChatRequest, ModelGateway

T = TypeVar("T", bound=BaseModel)

_REPAIRABLE_STAGES = {"contract", "semantic"}

_REPAIR_SYSTEM_PROMPT = (
    "You produce structured JSON that must validate against a fixed contract. "
    "When told your previous response was invalid, return ONLY the corrected "
    "JSON object - no prose, no markdown code fence, no explanation."
)


def is_repairable(failure: ValidationFailure) -> bool:
    """Whether Task 4's bounded repair should be attempted for this
    failure - see the module docstring's repair-policy section."""
    return failure.stage in _REPAIRABLE_STAGES


def validate_full(raw: str, model: type[T]) -> T | ValidationFailure:
    """The complete Day 4 validation pipeline for one raw response:
    Task 2's parse/contract validation, then - only for `CitedAnswer`,
    the one contract `data/day04_pack/semantic_rules.md` defines rules
    for - Task 3's semantic validation. Composes the two stages'
    functions without merging their implementations, so
    contract/schema and semantic validation stay the separate, separately
    tested functions the working rules require; this is only the call
    site that chains them for a single response."""
    contract_result = parse_and_validate(raw, model)
    if isinstance(contract_result, ValidationFailure):
        return contract_result
    if isinstance(contract_result, CitedAnswer):
        semantic_result = validate_semantic(contract_result)
        if isinstance(semantic_result, ValidationFailure):
            return semantic_result
    return contract_result


def build_repair_request(
    original_raw: str,
    failure: ValidationFailure,
    model: type[T],
    *,
    model_alias: str | None = None,
) -> ChatRequest:
    """Build the one repair call's request from the validation error -
    category, field path and safe message all come from `failure`, never
    from re-deriving the problem some other way. Includes the original
    raw response so the model has something concrete to correct; that is
    a request payload, not a log line, so the "never log full invalid
    model responses" rule (see `errors.py`) does not apply here."""
    field_note = f"\nField: {failure.field_path}" if failure.field_path else ""
    user_content = (
        f"Your previous response failed {failure.stage} validation.\n"
        f"Category: {failure.category}{field_note}\n"
        f"Detail: {failure.message}\n\n"
        f"Previous response:\n{original_raw}\n\n"
        f"Return corrected JSON for the {model.__name__} contract (schema_version "
        f'"1.0") that fixes this problem while still satisfying every other '
        f"required field and rule. Respond with the JSON object only."
    )
    return ChatRequest(
        messages=[
            ChatMessage(role="system", content=_REPAIR_SYSTEM_PROMPT),
            ChatMessage(role="user", content=user_content),
        ],
        model_alias=model_alias,
    )


def attempt_repair(
    original_raw: str,
    failure: ValidationFailure,
    model: type[T],
    gateway: ModelGateway,
    *,
    model_alias: str | None = None,
) -> T | ValidationFailure:
    """The one bounded repair attempt: build the request from `failure`,
    call `gateway.chat()` exactly once, then revalidate the reply through
    the complete pipeline (`validate_full`) again. Returns the typed
    contract on success. On failure - the repaired response still doesn't
    validate, or the Model Gateway call itself raised - returns a typed
    `ValidationFailure` and does not retry; the caller (`resolve()`) must
    not call this a second time for the same original response."""
    request = build_repair_request(original_raw, failure, model, model_alias=model_alias)
    try:
        chat_result = gateway.chat(request)
    except ModelGatewayError as exc:
        # A normalized, typed gateway failure (e.g. timeout, rate limit) -
        # never the raw exception, and never its free-text message, which
        # could echo request/response content. `exc.category` is the same
        # sanitized field ModelGateway's own logging uses.
        return ValidationFailure(
            stage="repair",
            category="repair_call_failed",
            message=f"repair call failed: {exc.category}",
        )
    return validate_full(chat_result.content, model)


def resolve(
    raw: str,
    model: type[T],
    gateway: ModelGateway,
    *,
    model_alias: str | None = None,
) -> T | ValidationFailure:
    """The full Day 4 entry point for one raw model response: validate,
    and only on a repairable failure, attempt exactly one repair. Returns
    a typed contract or a typed `ValidationFailure` either way - never an
    unchecked dict, never a raised exception."""
    result = validate_full(raw, model)
    if not isinstance(result, ValidationFailure):
        return result
    if not is_repairable(result):
        return result
    return attempt_repair(raw, result, model, gateway, model_alias=model_alias)
