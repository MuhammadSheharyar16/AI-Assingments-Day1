"""
Day 9 Task 5 -- the lane selector: turns a Gate-A decision (Task 3) into a
typed routing decision, from the closed, governed lane set
(`ontology.py`'s `LaneId`: `rag` / `mode_b` / `clarify` / `block` /
`safe_fast_path`).

Required policy (`lane_policy.md`):
    governed document/policy question -> rag
    governed structured-data intent    -> mode_b
    ambiguous intent                   -> clarify
    unsupported/blocked                -> block
    explicitly governed utility/help   -> safe_fast_path

`LaneSelector.select()` never invents a lane: for a `MATCHED`
`GateADecision`, the lane comes from the governed intent's own
`Intent.allowed_lanes` (Task 1) -- the selector reads which lane(s) the
intent is governed to allow and picks the one it declares, it never
hardcodes "this intent_id routes to that lane" itself. Every governed
intent in the committed v1 registry declares exactly one allowed lane
(`INT-POLICY-QUESTION` -> `rag`, `INT-STRUCTURED-LOOKUP` -> `mode_b`,
`INT-HELP` -> `safe_fast_path`), which is what actually realizes the
policy table above -- a future registry version could add an intent with
more than one allowed lane, in which case `_choose_lane()` documents
exactly how that tie is broken (first-declared order), rather than this
module silently doing something undocumented.

`AMBIGUOUS` and the two fail-closed statuses (`UNSUPPORTED`, `BLOCKED`)
route by status alone, per `lane_policy.md`'s explicit table -- "unknown
intent does not default to RAG", and unsupported/blocked share one lane
(`block`) precisely so neither reaches retrieval or model generation
(Day 9 working rule: "Blocked requests do not reach retrieval/model
generation"; Task 10's "no fall-through" proof is that this module, and
whatever calls it, never even attempts to for those two statuses).

`reason_code`/`intent_id`/`domain` mirror the `GateADecision` this
decision was made from for every status except `MATCHED`, where the lane
choice itself is the new information (`reason_code="intent_allowed_lane"`)
-- see `select()`.

This module selects `mode_b` as a lane; it never executes it. No database
call, no Mode-B service, is reachable from anywhere in this file (Day 9
working rule: "Day 9 selects Mode B but does not implement uncontrolled
Mode-B execution").
"""
from __future__ import annotations

from dataclasses import dataclass

from aico.control.errors import LaneSelectionError
from aico.control.models import GateADecision, GateAStatus, LaneDecision
from aico.control.ontology import LaneId
from aico.control.ontology_registry import OntologyRegistry


@dataclass
class LaneSelector:
    """The lane selector: built once against a loaded `OntologyRegistry`
    (Task 2, the same registry `GateA` was built against) and reused for
    every `GateADecision`. `select()` is the only public entry point."""

    registry: OntologyRegistry

    def select(self, gate_decision: GateADecision) -> LaneDecision:
        """Route one `GateADecision` to a typed `LaneDecision`. Never
        raises for a decision `GateA.classify()` could actually produce --
        only for one that violates an invariant lane selection depends on
        (see `LaneSelectionError`)."""
        version = gate_decision.ontology_version

        if gate_decision.status is GateAStatus.BLOCKED:
            return LaneDecision(
                lane=LaneId.BLOCK,
                reason_code=gate_decision.reason_code,
                ontology_version=version,
            )

        if gate_decision.status is GateAStatus.UNSUPPORTED:
            return LaneDecision(
                lane=LaneId.BLOCK,
                reason_code=gate_decision.reason_code,
                ontology_version=version,
            )

        if gate_decision.status is GateAStatus.AMBIGUOUS:
            return LaneDecision(
                lane=LaneId.CLARIFY,
                reason_code=gate_decision.reason_code,
                ontology_version=version,
            )

        # GateAStatus.MATCHED
        if gate_decision.intent_id is None:
            raise LaneSelectionError("MATCHED GateADecision has no intent_id")

        intent = self.registry.get_intent(gate_decision.intent_id)
        lane = self._choose_lane(intent.allowed_lanes)

        return LaneDecision(
            lane=lane,
            intent_id=intent.intent_id,
            domain=intent.domain,
            reason_code="intent_allowed_lane",
            ontology_version=version,
        )

    @staticmethod
    def _choose_lane(allowed_lanes: list[LaneId]) -> LaneId:
        """`allowed_lanes` is never empty (`Intent.allowed_lanes` requires
        at least one, Task 1). Every intent in the committed v1 registry
        declares exactly one, so this is a plain lookup in practice; for a
        future intent that ever declared more than one, the first
        (registry-declaration order, not sorted/arbitrary) is the
        deterministic, documented tie-break -- never a random or
        request-dependent choice."""
        return allowed_lanes[0]
