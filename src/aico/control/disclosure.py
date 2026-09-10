"""
Day 10 Task 9 -- safe disclosure output: given a `GateBDecision` (Task 3)
and typed candidate fields, produce only the policy-approved view.

"Given: Gate-B decision, typed candidate data/fields -> produce only the
policy-approved view" (Day 10 assignment). `apply_disclosure()` is the one
public entry point:

    - `decision` must already be `ALLOW` with a `disclosure_profile` set --
      any other decision (`DENY`, `CLARIFY`, or a hand-built `ALLOW` that
      somehow lacks a profile) produces an empty view with zero fields
      disclosed, never a partial one. This is the disclosure layer's own
      "no fall-through" guarantee (Task 11): a caller cannot get a
      non-empty view out of anything but a real `ALLOW` decision.
    - Every candidate `ProtectedField` is resolved independently, in two
      steps, both already built and proven in Tasks 7/8
      (`policy_models.py`) -- this module reuses them, it does not
      re-implement either:

        1. `is_pii_category_permitted(field.pii_category,
           decision.effective_pii_policy)` -- a defense-in-depth safety
           net. Safe to apply per field (unlike a data-classification
           re-check -- see `policy_models.py`'s "Task 7/8" docstring
           section for why that one is deliberately *not* done here):
           `effective_pii_policy` is always the matched rule's *complete*
           `allowed_pii_categories`, never narrowed the way
           `effective_data_classes` sometimes is, so a category genuinely
           absent from it is always safe to deny outright, never a false
           positive against a field the matched rule does authorize.
        2. `resolve_disclosure_action(profile, field.name)` -- the
           authoritative, policy-authored decision for a field the
           matched `disclosure_profile` actually declares (or the
           fail-closed `DENY` default for one it does not).

    - `DisclosureAction.ALLOW` -> the field's value passes through
      unchanged. `REDACT` -> `redaction.mask_value()` transforms it,
      deterministically (Task 9: "redactable fields are transformed
      deterministically"; never a generative model -- see `redaction.py`'s
      own docstring). `DENY` (from either step) -> the field is omitted
      from the disclosed value (`DisclosedField.value is None`), while
      still appearing in `SafeDisclosureView.fields` with its own
      `action` recorded, so a caller can distinguish "this field was
      considered and denied" from "this field was never offered as a
      candidate at all" without the *original* value ever having existed
      in the output.

    - The original `candidate_fields` are never mutated: `ProtectedField`/
      `DisclosedField`/`SafeDisclosureView` are all frozen dataclasses,
      `apply_disclosure()` only reads its inputs and returns new objects
      (Task 9: "original protected object is not mutated unexpectedly").

    - `SafeDisclosureView` carries `disclosure_profile` (a `profile_id`)
      and `policy_version` -- enough for a caller/telemetry layer to
      identify which governed profile/policy version produced this view
      (Task 9: "response metadata identifies the disclosure profile/rule
      version") -- and nothing else: no `PermissionRule`, no raw
      `GateBPolicyDocument` internals, no `PolicyRegistry` reference (Day
      10 working rule: "Policy decisions produce sanitized provenance").

What this module deliberately does NOT do: it does not call the Model
Gateway, read session memory, or accept any parameter through which a
model's output or a caller's request could influence which action applies
to a field (Day 10 working rule: "A model is not the authority for
permission or disclosure decisions") -- the only external input besides
`decision` is `candidate_fields`, which supplies each field's own
governed `data_class`/`pii_category` metadata, never a suggested action.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from aico.control.models import GateBDecision, GateBStatus
from aico.control.policy_models import (
    DataClassification,
    DisclosureAction,
    DisclosureProfile,
    PiiCategory,
    is_pii_category_permitted,
    resolve_disclosure_action,
)
from aico.control.redaction import mask_value


@dataclass(frozen=True)
class ProtectedField:
    """One field of a protected candidate record -- the typed shape
    `apply_disclosure()` consumes, matching
    `pii_disclosure_cases.json`'s own `field_metadata` shape
    (`data_class`/`pii_category` per field name). Always supplied by the
    caller, never inferred/guessed by this module: `data_class`/
    `pii_category` are the record's own governed tagging, not something
    `disclosure.py` derives from the field's name or value."""

    name: str
    value: str
    data_class: DataClassification
    pii_category: PiiCategory


@dataclass(frozen=True)
class DisclosedField:
    """One field of the safe, policy-approved disclosed view.
    `value is None` for `DENY` (omitted from disclosure); for `ALLOW` it
    is `ProtectedField.value` unchanged, for `REDACT` it is
    `redaction.mask_value(ProtectedField.value)`. `action` is always
    recorded, even for a denied field -- provenance, not raw content."""

    name: str
    value: str | None
    action: DisclosureAction


@dataclass(frozen=True)
class SafeDisclosureView:
    """The full policy-approved view produced by `apply_disclosure()`.
    `fields` never includes anything beyond what `candidate_fields` named
    (this module cannot invent a field), and never carries a raw value for
    a field whose resolved `action` is `DENY`. `disclosure_profile`/
    `policy_version` are the only provenance carried -- see the module
    docstring's "sanitized provenance" note."""

    fields: tuple[DisclosedField, ...]
    disclosure_profile: str | None
    policy_version: str

    def disclosed_values(self) -> dict[str, str]:
        """The convenience "final answer" shape -- field name -> disclosed
        value, for every field whose resolved action was not `DENY`.
        Denied fields are simply absent, not present with a `None`/
        placeholder value -- a caller iterating this mapping alone can
        never accidentally leak "this field exists but was denied"
        information beyond what `SafeDisclosureView.fields` already
        exposes explicitly via `action`."""
        return {field.name: field.value for field in self.fields if field.value is not None}


def apply_disclosure(decision: GateBDecision, profile: DisclosureProfile | None, candidate_fields: Sequence[ProtectedField]) -> SafeDisclosureView:
    """Produce the policy-approved view for one `GateBDecision` and its
    candidate fields. `profile` is the `DisclosureProfile` named by
    `decision.disclosure_profile` -- callers resolve it once via
    `PolicyRegistry.get_disclosure_profile(decision.disclosure_profile)`
    and pass it in, so this module never needs its own `PolicyRegistry`
    reference (kept a pure function of its typed inputs, no I/O, matching
    `redaction.py`'s own purity).

    Returns an empty view (`fields=()`, `disclosure_profile=None`) whenever
    `decision.decision` is not `ALLOW`, or `profile` is `None` -- Task 9's
    own "no fall-through" guarantee: there is no code path here that
    discloses anything from a `DENY`/`CLARIFY` decision, or without a
    profile actually resolved."""
    if decision.decision is not GateBStatus.ALLOW or profile is None:
        return SafeDisclosureView(fields=(), disclosure_profile=None, policy_version=decision.policy_version)

    disclosed = tuple(_resolve_field(field, profile, decision) for field in candidate_fields)
    return SafeDisclosureView(
        fields=disclosed, disclosure_profile=decision.disclosure_profile, policy_version=decision.policy_version
    )


def _resolve_field(field: ProtectedField, profile: DisclosureProfile, decision: GateBDecision) -> DisclosedField:
    """One field's decision -- see the module docstring's two-step
    description. `is_pii_category_permitted` can only ever downgrade
    toward `DENY`; it never upgrades an action `resolve_disclosure_action`
    would otherwise have denied."""
    if not is_pii_category_permitted(field.pii_category, decision.effective_pii_policy):
        return DisclosedField(name=field.name, value=None, action=DisclosureAction.DENY)

    action = resolve_disclosure_action(profile, field.name)
    if action is DisclosureAction.ALLOW:
        return DisclosedField(name=field.name, value=field.value, action=action)
    if action is DisclosureAction.REDACT:
        return DisclosedField(name=field.name, value=mask_value(field.value), action=action)
    return DisclosedField(name=field.name, value=None, action=DisclosureAction.DENY)
