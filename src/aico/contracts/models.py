"""
Day 4 — versioned typed contracts (Task 1).

These Pydantic models are the single source of truth for what the Day 4
contract layer is willing to accept as application data, built from
`data/day04_pack/contract_requirements.md`. Nothing else hand-maintains a
competing shape: JSON Schema is generated FROM these models (see
`scripts/day04_generate_schemas.py`) and committed under
`contracts/schema/` - never edited by hand, so it cannot drift from the
source model.

Two contracts:
- `Citation` / `CitedAnswer` (v1) - the answer a model is asked to
  produce, with the citations backing it.
- `ResponseEnvelope` (v1) - the shared AICO envelope wrapping a
  `CitedAnswer`, needed by later days.

Every model here sets `extra="forbid"`: an unknown/extra field is a
contract violation, not something silently dropped. `schema_version` is a
required `Literal` field (not defaulted) on both top-level contracts, so a
model response that omits it is rejected the same way a response missing
any other required field is - and it doubles as the version tag Task 6
requires in output metadata.

What this module deliberately does NOT do: cross-field business rules
(e.g. "an `answered` response needs at least one citation") are semantic
rules, not contract/schema rules - see `data/day04_pack/semantic_rules.md` and
Task 3's `semantic.py`. Enforcing them here would collapse the
schema-valid-but-semantically-invalid distinction Task 3 depends on
(fixture D04-09 in `structured_output_cases.json` relies on exactly that
separation: an explicitly-sent empty `citations: []` with `status="answered"`
must pass this module's validation and only fail later, at the semantic
stage). Note the distinction this draws: `citations` itself is a required
field (a response that omits the key is a contract failure, per
`contract_requirements.md`'s required-fields table) - only the
*permissibility of an empty list for a given status* is semantic.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

CITED_ANSWER_SCHEMA_VERSION = "1.0"
RESPONSE_ENVELOPE_SCHEMA_VERSION = "1.0"


class AnswerStatus(str, Enum):
    """Whether a cited answer actually answers the question or explicitly
    declines for lack of evidence. Day 5 owns *deciding* insufficiency;
    Day 4 only owns the typed shape of the outcome."""

    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ConfidenceLabel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Citation(BaseModel):
    """One cited source chunk backing an answer.

    Day 4 validates only the shape of a citation (non-empty id/source).
    It does not prove the chunk was actually returned by retrieval for
    this query - that grounding check against retrieved chunks is Day 5,
    per `data/day04_pack/contract_requirements.md`."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1, description="Non-empty chunk identifier.")
    source_file: str = Field(min_length=1, description="Non-empty source document name.")


class CitedAnswer(BaseModel):
    """Versioned cited-answer contract (`contract_requirements.md` #1).

    `citations` is a required field - `contract_requirements.md` lists it
    in the same "Required fields" table as `schema_version`/`status`/
    `answer`/`confidence_label`, so a response that omits the key entirely
    is a contract/shape failure (`missing_field`), not something silently
    defaulted. Whether an explicitly-supplied empty list (`"citations": []`)
    is *allowed* for a given `status` is a separate, semantic rule (S1/S4
    in `semantic_rules.md`), enforced in Task 3's `semantic.py`, not here -
    that is the distinction fixture D04-09 relies on: it sends `citations`
    explicitly as `[]`, so it still passes this module's validation and
    only fails later, at the semantic stage."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = Field(description="Cited-answer contract version.")
    status: AnswerStatus
    answer: str = Field(min_length=1, description="Non-empty answer text.")
    citations: list[Citation]
    confidence_label: ConfidenceLabel


class ResponseEnvelope(BaseModel):
    """Shared AICO response envelope (`contract_requirements.md` #2)
    wrapping a `CitedAnswer` result - the shape later days' callers
    depend on.

    `trace_id` and `warning` are optional: `warning` was added after v1
    shipped specifically to prove backward compatibility (Task 6) - the
    supplied `existing_caller_v1.json` fixture never sends it and must
    keep validating without it."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = Field(description="Response-envelope contract version.")
    request_id: str = Field(min_length=1, description="Non-empty caller-supplied request id.")
    result: CitedAnswer
    model_alias: str = Field(min_length=1, description="Non-empty model alias that produced the result.")
    trace_id: Optional[str] = Field(default=None, min_length=1)
    warning: Optional[str] = Field(default=None, min_length=1)
