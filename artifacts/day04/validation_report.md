# Day 4 Validation Report

Generated 2026-09-01 by `scripts/day04_generate_validation_report.py` against `data/day04_pack/fixtures/`. Every result below comes from running the real `aico.contracts` pipeline against these fixtures and a fake Model Gateway - no real network call is made generating this report.

## Contract/schema version

- `CitedAnswer.schema_version`: `1.0`
- `ResponseEnvelope.schema_version`: `1.0`

## Generated schema paths

Generated from the source Pydantic models by `scripts/day04_generate_schemas.py` and committed (never hand-maintained):

- `contracts/schema/cited_answer.v1.schema.json`
- `contracts/schema/response_envelope.v1.schema.json`

## Fixture summary

12 cases from `structured_output_cases.json`, each run through the real pipeline (`validate_full()`, or `resolve()` for the two repair fixtures):

| ID | Name | Expected stage | Outcome | Stage | Category | Repair calls |
|---|---|---|---|---|---|---|
| D04-01 | valid_first_pass | valid | valid | - | - | 0 |
| D04-02 | malformed_json | parse | failure | parse | malformed_json | 0 |
| D04-03 | markdown_wrapped_json | parse_or_documented_unwrap | valid | - | - | 0 |
| D04-04 | missing_required_field | contract | failure | contract | missing_field | 0 |
| D04-05 | extra_field | contract | failure | contract | extra_field | 0 |
| D04-06 | wrong_type | contract | failure | contract | wrong_type | 0 |
| D04-07 | invalid_enum | contract | failure | contract | invalid_enum | 0 |
| D04-08 | out_of_range_value | contract | failure | contract | out_of_range | 0 |
| D04-09 | semantic_answered_without_citation | semantic | failure | semantic | s1_answered_without_citation | 0 |
| D04-10 | semantic_insufficient_with_high_confidence | semantic | failure | semantic | s2_insufficient_evidence_high_confidence | 0 |
| D04-11 | repairable_invalid_response | repair_then_valid | valid | - | - | 1 |
| D04-12 | repair_still_invalid | repair_then_failure | failure | contract | wrong_type | 1 |

## Valid first-pass cases

Passed the complete pipeline (parse -> contract/schema -> semantic) with zero repair calls:

- `D04-01` (valid_first_pass)
- `D04-03` (markdown_wrapped_json)

## Contract/schema failures

Rejected at the parse or contract/schema stage (Task 2), before semantic validation ever runs:

| ID | Name | Stage | Category |
|---|---|---|---|
| D04-02 | malformed_json | parse | malformed_json |
| D04-04 | missing_required_field | contract | missing_field |
| D04-05 | extra_field | contract | extra_field |
| D04-06 | wrong_type | contract | wrong_type |
| D04-07 | invalid_enum | contract | invalid_enum |
| D04-08 | out_of_range_value | contract | out_of_range |
| D04-12 | repair_still_invalid | contract | wrong_type |

## Semantic failures

Passed contract/schema validation as a well-typed `CitedAnswer`, then rejected by a semantic rule (Task 3):

| ID | Name | Stage | Category |
|---|---|---|---|
| D04-09 | semantic_answered_without_citation | semantic | s1_answered_without_citation |
| D04-10 | semantic_insufficient_with_high_confidence | semantic | s2_insufficient_evidence_high_confidence |

## Repair attempts

2 fixture(s) triggered a bounded repair call (exactly one Model Gateway call each, never more): `D04-11`, `D04-12`.

The other contract/schema and semantic failures above (`D04-04`-`D04-10`) are repair-*eligible* under `repair.is_repairable` (any `contract`/`semantic` stage failure is), but this run exercises them through `validate_full()` only, matching `tests/test_day04_broken_output_suite.py`: those fixtures carry no `fake_repair_response` in `structured_output_cases.json`, so they exist to prove correct rejection at their stage, not to exercise repair - the repair path itself is proven exhaustively by `D04-11`/`D04-12` below. A `parse`-stage failure (`D04-02`) is never repair-eligible at all - see `repair.py`'s module docstring.

## Repair successes

- `D04-11` (repairable_invalid_response): invalid first response -> one repair call -> revalidated successfully as a typed `CitedAnswer`.

## Final failures

9 of 12 fixtures end as a typed failure after this run:

- Non-repairable by policy (`stage="parse"`, zero Model Gateway calls): `D04-02`.
- Repair-eligible but not exercised for repair in this fixture run (no `fake_repair_response` supplied): `D04-04`, `D04-05`, `D04-06`, `D04-07`, `D04-08`, `D04-09`, `D04-10`.
- Repair attempted and still failed (repair capped at one call, never retried): `D04-12`.

## Compatibility test result

**Passed.** `data/day04_pack/fixtures/existing_caller_v1.json` (a v1 caller snapshot that never sends `warning`) still validates against the current `ResponseEnvelope` (`schema_version="1.0"`), with `warning` defaulting to `None`. See `docs/adr/ADR-004-day4-contract-versioning.md` for the full backward-compatibility policy and the breaking-change examples it documents, each proven in `tests/test_day04_compatibility.py`.

## Schema-valid but semantically invalid example

Fixture `D04-09` (`semantic_answered_without_citation`): a response with `status="answered"`, `0` citation(s), and `confidence_label="medium"`. It is well-typed - every required field present, every type and enum correct - so it **passes contract/schema validation** and becomes a typed `CitedAnswer`. It still **fails semantic validation** under rule S1 (`data/day04_pack/semantic_rules.md`): an `answered` response must carry at least one citation, and this one carries none.

