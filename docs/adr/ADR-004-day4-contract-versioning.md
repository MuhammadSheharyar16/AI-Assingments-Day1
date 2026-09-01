# ADR-004 — Day 4 Contract Versioning and Backward Compatibility

## Status

Accepted

## Context

Day 4's contracts (`src/aico/contracts/models.py`) carry an explicit
`schema_version: Literal["1.0"]` on both `CitedAnswer` and
`ResponseEnvelope`. That version string is only useful if there is a clear,
consistently-applied rule for when it must change - otherwise "schema
version" is decoration, not a contract. This ADR records that rule: which
changes are safe to make under the *same* version, which ones are not, and
where the line runs. Every claim below is backed by an executable test in
`tests/test_day04_compatibility.py` - this is documentation with proof,
not just prose asserting a policy nobody checks.

## Decision

**A change is backward compatible - no version bump - only when every
existing, already-deployed caller keeps validating exactly as before,
with no behavior change for the fields it already reads.** In practice for
a Pydantic contract with `extra="forbid"`, that means: adding a new
*optional* field, and nothing else. Everything that changes what a
required field means, what shape a field has, or what values are legal
needs an explicit version decision instead.

### Backward-compatible: adding an optional field

`ResponseEnvelope.warning: Optional[str] = None` was added after v1
shipped. `data/day04_pack/fixtures/existing_caller_v1.json` is a frozen
snapshot of what an already-deployed v1 caller sends/expects - it has no
`warning` key at all. `test_existing_caller_v1_fixture_is_still_valid_
after_optional_warning_field_was_added` proves that fixture still
validates against the *current* `ResponseEnvelope`, with `warning`
defaulting to `None` - the existing caller is unaffected, because nothing
it already depended on changed. The same test file also proves the
reverse direction: a server that *does* start sending `warning` doesn't
break a new caller that reads it either
(`test_existing_caller_v1_still_valid_even_if_a_new_optional_field_is_sent_too`).

No version bump was needed for this change; `schema_version` stayed
`"1.0"`.

### Breaking: requires an explicit version decision

Each example below is proven by mutating the *real* fixture data (or, where
the real model can't demonstrate the "before" state because it's already
correct, a small model declared only inside the test file and clearly
labeled hypothetical - never applied to `src/aico/contracts/models.py`)
and showing it fails validation. That failure is exactly what an
already-deployed v1 caller would hit if a server made this change while
keeping `schema_version="1.0"` - which is precisely why it must not:

1. **Removing a required field.** Dropping `model_alias` (or any other
   required field) from the response breaks every caller that reads it -
   proven directly against the real `ResponseEnvelope` model
   (`test_removing_a_required_field_breaks_existing_validation`).
2. **Changing a field type incompatibly.** Changing `confidence_label`
   from the `low`/`medium`/`high` enum to a numeric score breaks every
   caller expecting a string label - proven directly against the real
   `CitedAnswer` model (`test_changing_a_field_type_incompatibly_breaks_
   existing_validation`).
3. **Making an optional field required.** Promoting `warning` from
   optional to required breaks every existing caller that has never sent
   it (including the `existing_caller_v1.json` fixture itself) - proven
   against a hypothetical local model with `warning` made required
   (`test_making_an_optional_field_required_would_break_existing_callers`).
4. **An incompatible enum change.** Renaming `AnswerStatus.ANSWERED`'s
   value from `"answered"` to `"responded"` (or removing a value outright)
   breaks every caller holding the old string - proven against a
   hypothetical local model with the enum value renamed
   (`test_incompatible_enum_change_would_break_existing_callers`).

Any of these, if actually needed, gets handled the same way: bump
`schema_version` to a new literal (e.g. `"2.0"`), add a new model
(`CitedAnswerV2`/`ResponseEnvelopeV2` or similar) rather than editing the
v1 model shape in place, generate and commit a new schema file
alongside the old one (`cited_answer.v2.schema.json` next to
`cited_answer.v1.schema.json` - the existing `.v1.` naming already leaves
room for this), and give existing v1 callers an explicit migration
window rather than an unannounced breaking change under the version
string they already trust.

### Schema version in output metadata

`schema_version` is a required field directly on both `CitedAnswer` and
`ResponseEnvelope` - not a side channel, not something a caller has to
infer - so it is always present on every typed result a caller receives.
`test_schema_version_appears_on_both_typed_contracts` proves this using
the real `existing_caller_v1.json` fixture data.

## Consequences

- A reviewer (or a future contributor) can classify any proposed contract
  change against this ADR's rule before writing it, instead of guessing
  whether it needs a version bump.
- Adding new optional fields stays cheap and doesn't require coordinating
  a version migration with every caller.
- Any of the four breaking-change categories above is caught by
  `tests/test_day04_compatibility.py` staying red if someone tries to make
  that change to the real v1 models without also making the version
  decision this ADR requires.
