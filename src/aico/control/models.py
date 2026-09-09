"""
Day 9 Task 3/5 -- shared control-plane decision models.

`OntologyDocument`/`OntologyRegistry` (Tasks 1/2) define what is governed;
this module defines what a *decision* about a request looks like once it
has been checked against that governance. Two decisions live here:

- `GateADecision` (Task 3, `gate_a.py`) -- Gate-A's typed intent/domain
  classification, made before lane selection ever runs.
- `LaneDecision` (Task 5, `lane_selector.py`) -- the lane selector's typed
  routing decision, made *from* a `GateADecision`.

Kept separate from both `ontology.py` (governed *data*) and
`gate_a.py`/`lane_selector.py` (the *logic* that produces these decisions),
so a later stage (Task 9's API integration, Task 11's observability layer)
can import the decision shapes without importing the classification/
routing logic that builds them.

Both models set `extra="forbid"` and carry `ontology_version` +
`reason_code`, matching the Day 9 working rule: "Route decisions include
ontology version and reason." Neither is ever constructed with a
`domain`/`intent_id`/`lane` value that did not come from a governed
`OntologyRegistry` lookup -- these types describe the *shape* of a
decision; `gate_a.py`/`lane_selector.py` own making that guarantee true.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from aico.control.ontology import LaneId


class GateAStatus(str, Enum):
    """The four required Gate-A outcomes (Day 9 Task 3). Deliberately not
    extensible at the type level -- a fifth status would need a registry
    change to `ontology.py`'s `LaneId`-style enums plus a matching
    `lane_selector.py` policy update, never an ad hoc string."""

    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"
    BLOCKED = "blocked"


class GateADecision(BaseModel):
    """Gate-A's typed classification of one request (Task 3's required
    field list, `status`/`domain`/`intent_id`/`matched_concepts`/
    `reason_code`/`ontology_version`, plus `candidate_intents` -- see
    below). Gate-A itself (`gate_a.py`) is the only thing that constructs
    one; nothing downstream is permitted to synthesize a `GateADecision`
    from a raw dict or hand-picked field values (Day 9 working rule:
    "Model output cannot create a new ontology entry, intent, lane or
    policy").

    `intent_id`/`domain` are populated only for `MATCHED` (exactly one
    governed intent, in exactly one governed domain) -- `None` for every
    other status, including `AMBIGUOUS`, where by definition no single
    intent/domain has been chosen yet.

    `candidate_intents` is an extension beyond the assignment's minimum
    field list, populated only for `AMBIGUOUS`: the governed intent ids a
    request plausibly matches (`ambiguity_cases.json`'s own
    `possible_governed_intents`) -- what Task 6's clarification question
    is built from. Empty for every other status.

    `matched_concepts` names every active governed concept the request
    text itself referenced (`gate_a.py`'s "multiple known concepts"
    behavior, Task 4 -- a request can reference more than one governed
    concept while still resolving, or failing to resolve, to a single
    intent). Populated for `MATCHED`/`AMBIGUOUS`; empty for
    `UNSUPPORTED`/`BLOCKED`, where by definition nothing governed was
    recognized (or the request never reached classification at all)."""

    model_config = ConfigDict(extra="forbid")

    status: GateAStatus
    domain: str | None = Field(default=None, description="domain_id, populated only when status is MATCHED.")
    intent_id: str | None = Field(default=None, description="intent_id, populated only when status is MATCHED.")
    matched_concepts: list[str] = Field(
        default_factory=list, description="concept_ids that drove this decision (see class docstring)."
    )
    candidate_intents: list[str] = Field(
        default_factory=list,
        description="intent_ids this request plausibly matches, populated only when status is AMBIGUOUS.",
    )
    reason_code: str = Field(min_length=1, description="Short, sanitized, machine-checkable reason for this decision.")
    ontology_version: str = Field(min_length=1, description="The governed ontology version this decision was made against.")


class LaneDecision(BaseModel):
    """The lane selector's typed routing decision (Task 5's required field
    list, `lane`/`intent_id`/`domain`/`reason_code`/`ontology_version`).
    `LaneSelector` (`lane_selector.py`) is the only thing that constructs
    one, always *from* a `GateADecision` -- never from a raw dict or an
    arbitrary lane string (`lane_policy.md`: "no arbitrary lane strings").

    `lane` is always one of the five governed `LaneId` values, and for a
    request that reached a specific governed intent, always one of that
    intent's own `Intent.allowed_lanes` (Task 1) -- the lane selector
    picks among what the intent itself is governed to allow, it never
    invents a route. `intent_id`/`domain` mirror the `GateADecision` this
    decision was made from: both `None` unless the underlying Gate-A
    status was `MATCHED`."""

    model_config = ConfigDict(extra="forbid")

    lane: LaneId
    intent_id: str | None = Field(default=None, description="intent_id this lane was selected for, when one exists.")
    domain: str | None = Field(default=None, description="domain_id this lane was selected for, when one exists.")
    reason_code: str = Field(min_length=1, description="Short, sanitized, machine-checkable reason for this decision.")
    ontology_version: str = Field(min_length=1, description="The governed ontology version this decision was made against.")
