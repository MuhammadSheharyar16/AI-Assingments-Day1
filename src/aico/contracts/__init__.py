"""
Day 4 contract boundary. `src/aico/contracts/` is the only place model
output is deserialized into application data (see the package's own
modules for the pipeline: parse -> contract/schema validation -> semantic
validation -> typed result, with one bounded repair attempt on failure -
`contract_requirements.md` / `semantic_rules.md` in `data/day04_pack/`).

Task 1 exports the versioned Pydantic contracts. Task 2 adds the typed
failure value and the parse/contract validator. Task 3 adds semantic
validation. Task 4 adds the bounded repair path (and the full
parse->contract->semantic pipeline it revalidates against,
`validate_full`). Later tasks add the service entry point in a sibling
module.
"""
from aico.contracts.errors import CONTRACT_CATEGORIES, ValidationFailure
from aico.contracts.models import (
    CITED_ANSWER_SCHEMA_VERSION,
    RESPONSE_ENVELOPE_SCHEMA_VERSION,
    AnswerStatus,
    Citation,
    CitedAnswer,
    ConfidenceLabel,
    ResponseEnvelope,
)
from aico.contracts.repair import attempt_repair, build_repair_request, is_repairable, resolve, validate_full
from aico.contracts.semantic import validate_semantic
from aico.contracts.validator import parse_and_validate, parse_json, validate_contract

__all__ = [
    "CITED_ANSWER_SCHEMA_VERSION",
    "RESPONSE_ENVELOPE_SCHEMA_VERSION",
    "AnswerStatus",
    "Citation",
    "CitedAnswer",
    "ConfidenceLabel",
    "ResponseEnvelope",
    "CONTRACT_CATEGORIES",
    "ValidationFailure",
    "parse_and_validate",
    "parse_json",
    "validate_contract",
    "validate_semantic",
    "attempt_repair",
    "build_repair_request",
    "is_repairable",
    "resolve",
    "validate_full",
]
