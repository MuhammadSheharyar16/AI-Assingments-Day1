"""
Day 4 contract boundary. `src/aico/contracts/` is the only place model
output is deserialized into application data (see the package's own
modules for the pipeline: parse -> contract/schema validation -> semantic
validation -> typed result, with one bounded repair attempt on failure -
`contract_requirements.md` / `semantic_rules.md` in `data/day04_pack/`).

Task 1 exports the versioned Pydantic contracts; later tasks add the
validator, semantic rules, repair path and service entry point in
sibling modules.
"""
from aico.contracts.models import (
    CITED_ANSWER_SCHEMA_VERSION,
    RESPONSE_ENVELOPE_SCHEMA_VERSION,
    AnswerStatus,
    Citation,
    CitedAnswer,
    ConfidenceLabel,
    ResponseEnvelope,
)

__all__ = [
    "CITED_ANSWER_SCHEMA_VERSION",
    "RESPONSE_ENVELOPE_SCHEMA_VERSION",
    "AnswerStatus",
    "Citation",
    "CitedAnswer",
    "ConfidenceLabel",
    "ResponseEnvelope",
]
