"""
Day 9 Task 3 -- shared control-plane decision models.

`OntologyDocument`/`OntologyRegistry` (Tasks 1/2) define what is governed;
this module defines what a *decision* about a request looks like once it
has been checked against that governance: `GateADecision`, Gate-A's typed
intent/domain classification, made before lane selection ever runs
(`gate_a.py`). Kept separate from both `ontology.py` (governed *data*) and
`gate_a.py` (the classification *logic* that produces this decision), so a
later stage (the lane selector consuming a `GateADecision`, Task 11's
observability layer logging one) can import the decision shape without
importing the classification logic that builds it. Task 5's lane selector
adds its own `LaneDecision` here, alongside this one, when it lands.

`GateADecision` sets `extra="forbid"` and carries `ontology_version` +
`reason_code`, matching the Day 9 working rule: "Route decisions include
ontology version and reason." It is never constructed with a
`domain`/`intent_id` value that did not come from a governed
`OntologyRegistry` lookup -- this type describes the *shape* of a
decision; `gate_a.py` owns making that guarantee true.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


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

    `matched_concepts` names the governed concepts that drove this
    decision -- for `MATCHED`, the concept(s) behind the winning governed
    phrase; for `AMBIGUOUS`, the concept(s) shared by the tied candidate
    intents (i.e. *why* the request is ambiguous); empty for
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
