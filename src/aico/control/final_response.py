"""
Day 12 Task 1 -- the typed final-response envelope.

Gate-D's whole premise (`Day 12 Task.pdf`, "Build outcome": "A valid model
response is still untrusted until the final deterministic controls approve
it") only holds if what Gate-D actually evaluates is a typed,
self-validating shape -- never a raw dict the Model Gateway/contract layer/
semantic validator happened to hand back (working rule: "Do not pass raw
unchecked dictionaries into Gate-D"). This module is that shape, built from
`data/day12_pack/gate_d_policy_requirements.md` and
`final_response_rules.md`, the same "typed envelope before the decision
logic" split `evidence/models.py` already draws for Gate-C (Day 11 Task 1).

Two models:

- `FinalCitation` -- one citation on the candidate answer, carrying enough
  provenance to be reconciled against Gate-C's own `validated_evidence_ids`
  (Task 3) and, independently, against the full `evidence_id`/`chunk_id`/
  `source_id`/`source_version` identity Task 4 requires. `citation_id` is
  kept optional: `final_citation_cases.json`'s own fixture citations never
  carry one (only the four provenance-identity fields), and Task 8's
  working rule is "do not edit failing fixtures to make the implementation
  pass" -- so a citation without one must still be a well-formed
  `FinalCitation`, not a shape rejection. When a caller does supply one
  (e.g. a real deployment's own per-citation id, distinct from the
  evidence it cites), it is carried through unchanged for later
  observability/reporting use, never required.
- `FinalResponseCandidate` -- the envelope itself: Task 1's own minimum
  field list (`request_id` / `correlation_id` / `candidate_status` /
  `candidate_answer` / `candidate_citations` /
  `gate_c_validated_evidence_ids` / `gate_b_disclosure_profile` /
  `started_at` / `elapsed_ms` / `model_latency_ms` /
  `contract_validation_status` / `semantic_validation_status`), nothing
  more, nothing less.

`candidate_status` reuses Day 4's own governed `AnswerStatus` enum
(`contracts/models.py`) rather than a second, competing vocabulary -- the
two values it already carries (`answered` / `insufficient_evidence`) are
exactly `gate_d_policy_v1.json`'s own `allowed_response_statuses` universe,
and a final candidate's status is, by construction, the same typed status
the model's own cited-answer contract produced. Task 1's "unknown final
status rejected" case is therefore not a separate rule this module has to
enforce -- Pydantic's own enum validation already rejects anything outside
`AnswerStatus`'s two closed members before this envelope can ever be
constructed; a *policy* additionally narrowing which of those two values it
is willing to allow (Task 2's own `invalid response status rejected` case)
is a distinct, later concern this module does not duplicate.

`contract_validation_status`/`semantic_validation_status` are each a
`ValidationStatus` (`passed`/`failed`) recording, verbatim, what Day 4's
contract validator and Day 4/5's semantic validator already decided
upstream -- Gate-D never re-derives or re-runs either check itself (working
rule: "Gate-D does not repair ... citations[or]disclosure ... with another
model call"; more fundamentally, Gate-D is the *release* boundary, not a
second contract/semantic validator). Both are required, non-defaulted
fields: an envelope that omits either is missing control metadata Gate-D
cannot make a safe decision without (Task 1's "missing control metadata
rejected" case) -- there is no default that could stand in for "did the
typed contract actually pass."

`candidate_answer` deliberately has no `min_length` constraint: an empty
string is a well-formed (if unhelpful) candidate answer at the *shape*
level -- `final_quality_cases.json`'s own QUAL12-002 (`"candidate_answer":
""`) must still parse into a valid envelope and only fail later, at Task
5's deterministic quality gate (`no empty "answered" result`), the
identical shape/semantic split `contracts/models.py`'s own docstring draws
for Day 4's `CitedAnswer.citations`.

`gate_b_disclosure_profile` is optional (`None` default): a well-formed
candidate can legitimately carry no disclosure profile at all --
`disclosure_leak_cases.json`'s own DISC12-005/DISC12-006 fixtures (a raw
secret-token leak, a raw hidden-prompt-marker leak) name no
`disclosure_profile` whatsoever, because those checks apply universally
regardless of which profile was in effect. A profile is therefore present
when Gate-B actually resolved one for this request, never synthesized here.

`gate_c_validated_evidence_ids` defaults to an empty tuple, not because it
is optional control metadata, but because *empty* is itself a valid,
common state (an `insufficient_evidence` candidate legitimately cites
nothing) -- the missing-vs-empty distinction this module draws everywhere
else (`gate_b_disclosure_profile`, `candidate_citations`).

Every model here sets `extra="forbid"` (an unknown field is a malformed
envelope, not something silently dropped -- the same contract-boundary
convention `contracts/models.py`, `control/models.py` and
`evidence/models.py` already use) and uses Pydantic's `AwareDatetime` for
`started_at`, so a naive datetime (no tzinfo) or an unparsable timestamp
string is rejected by Pydantic itself, before any downstream latency-budget
logic (Task 8) ever runs. `elapsed_ms`/`model_latency_ms` are each
constrained `>= 0` at the field level -- Task 1's "invalid latency/timing
values rejected" case (a negative timing value, `latency_budget_cases.json`
LAT12-005) is therefore a shape failure caught right here, not something a
later latency-budget check has to separately guard against; Gate-D's own
decision contract (Task 10) is expected to treat a malformed envelope this
way (a negative-timing candidate can never even become a `FinalResponse
Candidate`) as its documented `reject` outcome -- "invalid internal
candidate ... that should not be exposed as normal answer" -- never as a
normal `allow`/`safe_failure` release decision.

What this module deliberately does NOT do: it proves an envelope is
well-formed, not that it is releasable. Whether `candidate_citations`
actually reconciles against `gate_c_validated_evidence_ids` (Task 3),
whether the disclosure profile was actually honored (Task 6), whether the
latency values are within a governed budget (Task 8), and whether the
overall candidate meets deterministic quality bars (Task 5) are all later,
separate boundaries `gate_d.py` (Task 10) orchestrates over this typed
shape -- deliberately not duplicated here, the same shape/semantic split
`evidence/models.py` already draws for Gate-C's own envelope."""
from __future__ import annotations

from enum import Enum

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from aico.contracts.models import AnswerStatus
from aico.control.errors import FinalResponseEnvelopeError


class ValidationStatus(str, Enum):
    """Whether an upstream Day 4 check (contract/semantic validation)
    passed or failed, as already decided by that stage -- Gate-D records
    the verdict, it never re-derives it (see module docstring)."""

    PASSED = "passed"
    FAILED = "failed"


def _non_blank(value: str, *, field_name: str) -> str:
    """`min_length=1` alone accepts a whitespace-only string (`"   "`) as
    non-empty -- every identifier field below also rejects a value that is
    blank once stripped, the identical helper `evidence/models.py` already
    uses for its own identifier fields."""
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


def _no_blank_entries(value: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    for entry in value:
        if not entry.strip():
            raise ValueError(f"{field_name} entries must be non-empty")
    return value


class FinalCitation(BaseModel):
    """One citation on the candidate final answer -- see module docstring
    for the field-by-field rationale, especially why `citation_id` is
    optional while the other four are not."""

    model_config = ConfigDict(extra="forbid")

    citation_id: str | None = Field(
        default=None, description="Optional public citation id, distinct from the evidence it cites."
    )
    evidence_id: str = Field(min_length=1, description="Gate-C evidence_id this citation claims to be backed by.")
    chunk_id: str = Field(min_length=1, description="Stable chunk/record identifier of the cited evidence.")
    source_id: str = Field(min_length=1, description="Governed source_id the cited evidence claims to come from.")
    source_version: str = Field(min_length=1, description="Version of the source the cited evidence was retrieved at.")

    @field_validator("evidence_id", "chunk_id", "source_id", "source_version")
    @classmethod
    def _validate_non_blank_identifiers(cls, value: str, info: ValidationInfo) -> str:
        return _non_blank(value, field_name=info.field_name)

    @field_validator("citation_id")
    @classmethod
    def _validate_citation_id(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("citation_id must not be blank when supplied")
        return value


class FinalResponseCandidate(BaseModel):
    """The typed final-response envelope Gate-D consumes (Task 1's required
    field list). Nothing downstream is permitted to hand Gate-D a raw dict
    or a hand-picked subset of these fields instead -- see
    `parse_final_response_candidate()`, the one boundary parser that turns
    an untrusted payload into one of these, or a sanitized
    `FinalResponseEnvelopeError`."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, description="Non-empty caller-supplied request id.")
    correlation_id: str = Field(min_length=1, description="Non-empty trace correlation id.")
    candidate_status: AnswerStatus = Field(description="The typed contract's own answer status for this candidate.")
    candidate_answer: str = Field(description="The candidate answer text. May legitimately be empty at this shape layer.")
    candidate_citations: tuple[FinalCitation, ...] = Field(
        default_factory=tuple, description="Citations the candidate answer actually carries."
    )
    gate_c_validated_evidence_ids: tuple[str, ...] = Field(
        default_factory=tuple, description="The evidence_ids Gate-C actually validated for this request (Task 11)."
    )
    gate_b_disclosure_profile: str | None = Field(
        default=None, description="Gate-B's resolved disclosure profile_id for this request, when one was resolved."
    )
    started_at: AwareDatetime = Field(description="When this request began, for elapsed_ms to be measured against.")
    elapsed_ms: int = Field(ge=0, description="Total end-to-end latency so far, checked against the total latency budget.")
    model_latency_ms: int = Field(ge=0, description="Model-stage latency, checked against the model latency budget.")
    contract_validation_status: ValidationStatus = Field(description="Day 4 contract-validation verdict for this candidate.")
    semantic_validation_status: ValidationStatus = Field(description="Day 4/5 semantic-validation verdict for this candidate.")

    @field_validator("request_id", "correlation_id")
    @classmethod
    def _validate_non_blank_identifiers(cls, value: str, info: ValidationInfo) -> str:
        return _non_blank(value, field_name=info.field_name)

    @field_validator("gate_b_disclosure_profile")
    @classmethod
    def _validate_disclosure_profile(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("gate_b_disclosure_profile must not be blank when supplied")
        return value

    @field_validator("gate_c_validated_evidence_ids")
    @classmethod
    def _validate_gate_c_validated_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _no_blank_entries(value, field_name="gate_c_validated_evidence_ids")


def _field_path(exc: ValidationError) -> str | None:
    loc = exc.errors(include_url=False)[0]["loc"]
    return ".".join(str(part) for part in loc) if loc else None


def _first_error_message(exc: ValidationError) -> str:
    first = exc.errors(include_url=False)[0]
    field_path = _field_path(exc)
    prefix = f"{field_path}: " if field_path else ""
    return f"{prefix}{first['msg']}"


def parse_final_response_candidate(data: dict) -> FinalResponseCandidate:
    """Boundary parser: a raw dict (as actually produced by the Model
    Gateway/contract/semantic layers) -> a typed `FinalResponseCandidate`,
    or a sanitized `FinalResponseEnvelopeError` -- never a raw
    `pydantic.ValidationError` escaping to a caller, and never an unchecked
    dict passed further into Gate-D (working rule: "Do not pass raw
    unchecked dictionaries into Gate-D"). Mirrors
    `evidence/models.py`'s `parse_evidence_item()`/`parse_evidence_package()`
    exactly, the identical pattern one layer over for the final-response
    boundary."""
    try:
        return FinalResponseCandidate.model_validate(data)
    except ValidationError as exc:
        raise FinalResponseEnvelopeError(_first_error_message(exc), field_path=_field_path(exc)) from exc
