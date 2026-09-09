"""
Day 9 Task 9 -- integrate Gate-A (Task 3) and the lane selector (Task 5)
in front of the Day 5 answer pipeline (`answer_service.GroundedAnswerService`).

Required order (Day 9 assignment):

    trusted identity -> session resolution -> Day 5 input policy
    -> Gate-A -> lane selector -> selected lane behavior

`ControlPlaneAnswerService.answer()` implements everything from "Day 5
input policy" onward -- "trusted identity" and "session resolution" are,
exactly as for `GroundedAnswerService` today, `api/app.py`'s job
(`TrustedIdentity`, `MemorySessionService`), not this module's; this class
is a drop-in peer of `GroundedAnswerService`, not a replacement for the
API layer that calls it.

    1. Day 5 input policy (`evaluate_policy`, unchanged) -- a `block` or
       `clarify` outcome here returns the exact same `Blocked`/`Clarify`
       result `GroundedAnswerService.answer()` already returns for it,
       and Gate-A never runs at all. Only an `allow` outcome proceeds.
    2. Session memory MAY resolve a dangling reference in the question
       (Task 8's `resolve_reference`, given an optional
       `reference_context`) -- plain text substitution only, run before
       Gate-A, never after (Day 9 pipeline: "Session Context" precedes
       "Gate-A").
    3. Gate-A (Task 3) classifies the (possibly reference-resolved)
       question into a typed `GateADecision`.
    4. The lane selector (Task 5) routes that decision to a typed
       `LaneDecision`.
    5. Selected lane behavior:
         rag            -> delegates to the wrapped `GroundedAnswerService`
                            for retrieval -> Model Gateway -> typed
                            contract/semantic validation -> citation
                            validation -- Day 5's pipeline, unmodified.
         mode_b         -> `ModeBSelected`: the governed selection is
                            returned; nothing executes it (Day 9 working
                            rule: "Day 9 selects Mode B but does not
                            implement uncontrolled Mode-B execution" --
                            there is no database import anywhere in this
                            module).
         clarify        -> `GateClarify`, carrying Gate-A's own
                            deterministically generated
                            `clarification_question` (Task 6).
         block          -> `GateBlocked` (an unsupported OR a Gate-A-level
                            blocked request -- `lane_policy.md`:
                            "unsupported or blocked request -> block").
         safe_fast_path -> `SafeFastPathAnswer`: a deterministic, governed
                            response built only from the matched intent's
                            own `Intent.description` plus every active
                            intent's description (never model-generated).
       Only the `rag` branch ever calls the wrapped `GroundedAnswerService`
       (i.e. only it can ever reach retrieval or the Model Gateway) -- the
       other four branches return a typed value directly. `test_day09_gate_a.py`'s
       Task 7 static-import check already proves `aico.control` itself
       cannot reach retrieval/Mode-B; this module is the one place that
       *could* (it legitimately holds a `GroundedAnswerService`), and it
       only ever does so from the single `rag` branch.

WHY THIS IS NOT WIRED INTO `api/app.py`'s `/ask` TODAY: Day 9's committed
ontology (`ontology/registry.v1.json`) is deliberately a small, SYNTHETIC
Mode-A registry (`ontology_requirements.md`: "The supplied registry is a
synthetic Mode-A registry... It does not contain supplier facts") covering
exactly three intents. The real RAG corpus `/ask` answers over today
(`data/documents/`) and Day 7's permanent golden-eval questions
(`evals/golden_v1.json`) are far broader real supplier-governance
questions that this narrow synthetic registry was never built to
recognize -- routing them all through Gate-A first would classify nearly
all of them `unsupported` and block them from ever reaching retrieval,
which would not be a Gate-A bug, but it would silently break Day 7's
permanent regression gate (`hit_at_1`/`hit_at_k`/`mrr`/`groundedness_rate`),
a working rule this build must never violate ("Day 7 evaluation and Day 8
isolation must remain green"). `GroundedAnswerService` therefore keeps
answering `/ask` exactly as it does today; `ControlPlaneAnswerService` is
this Task's complete, independently testable integration, ready for a
future day to route real traffic through once the governed ontology
actually covers the corpus it gates.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from aico.control.gate_a import GateA
from aico.control.lane_selector import LaneSelector
from aico.control.models import GateADecision, LaneDecision
from aico.control.ontology import LaneId, LifecycleStatus
from aico.control.ontology_registry import OntologyRegistry
from aico.memory.context_builder import MemoryContext, SessionReferenceContext, resolve_reference
from aico.platform.model_gateway import CancellationToken
from aico.rag.answer_service import AnswerResult, Blocked, Clarify, GroundedAnswerService
from aico.security.input_policy import PolicyOutcome, evaluate_policy
from aico.security.normalization import normalize_input

# ── New typed result paths (lane outcomes with no Day 5 equivalent) ─────

@dataclass(frozen=True)
class GateBlocked:
    """Selected lane was `block`, driven by Gate-A -- either an
    unsupported request (nothing governed matched) or a request Gate-A's
    own policy check rejected. Distinct from Day 5's `Blocked` (which
    fires earlier, before Gate-A ever runs, for the exact same underlying
    reason -- Day 5's own block check) purely so a caller can tell which
    stage produced the block; the outward meaning ("this did not proceed
    to retrieval or generation") is identical."""

    question: str
    reason_code: str
    ontology_version: str


@dataclass(frozen=True)
class GateClarify:
    """Selected lane was `clarify` -- Gate-A's `AMBIGUOUS` status (Task 6).
    `clarification_question` is Gate-A's own deterministically generated
    question; `candidate_intents` is Gate-A's own list, when there was
    something specific to disambiguate among (may be empty -- an
    under-specified request with no competing candidates is still
    ambiguous, see `gate_a.py`)."""

    question: str
    clarification_question: str
    candidate_intents: tuple[str, ...]
    reason_code: str
    ontology_version: str


@dataclass(frozen=True)
class ModeBSelected:
    """Selected lane was `mode_b` -- the governed intent this request
    resolved to is one a later Mode-B component is meant to execute
    structured-data lookups for. Selected, never executed (Day 9 working
    rule): this dataclass carries no query, no connection, nothing
    callable -- there is nothing here *to* execute."""

    question: str
    intent_id: str
    domain: str
    reason_code: str
    ontology_version: str
    message: str = "Mode-B structured-data execution is not implemented yet; this request was routed, not executed."


@dataclass(frozen=True)
class SafeFastPathAnswer:
    """Selected lane was `safe_fast_path` -- a governed utility/help
    intent (e.g. `INT-HELP`). `answer` is deterministic, built only from
    governed `Intent.description` text (see
    `ControlPlaneAnswerService._safe_fast_path_answer`) -- never a Model
    Gateway call, matching the whole point of this lane: a fixed, safe,
    non-generative response."""

    question: str
    intent_id: str
    answer: str
    reason_code: str
    ontology_version: str


# The full set of results `ControlPlaneAnswerService.answer()` can return:
# Day 5's own five (via the early policy short-circuit, or via delegating
# to `GroundedAnswerService` for the `rag` lane) plus the four lane
# outcomes above that have no Day 5 equivalent.
ControlPlaneAnswerResult = AnswerResult | GateBlocked | GateClarify | ModeBSelected | SafeFastPathAnswer


@dataclass
class ControlPlaneAnswerService:
    """Construct with an `OntologyRegistry` (Task 2) and a
    `GroundedAnswerService` (Day 5) -- `gate_a`/`lane_selector` are built
    from the registry automatically unless supplied (tests may inject
    their own, e.g. built from a throwaway registry). See the module
    docstring for the full pipeline and why this is not yet the service
    `api/app.py` calls."""

    registry: OntologyRegistry
    rag_service: GroundedAnswerService
    gate_a: GateA = field(init=False)
    lane_selector: LaneSelector = field(init=False)

    def __post_init__(self) -> None:
        self.gate_a = GateA(self.registry)
        self.lane_selector = LaneSelector(self.registry)

    def answer(
        self,
        question: str,
        cancellation: CancellationToken | None = None,
        *,
        memory_context: MemoryContext | None = None,
        reference_context: SessionReferenceContext | None = None,
    ) -> ControlPlaneAnswerResult:
        """See the module docstring for the full required pipeline order.
        `memory_context` is forwarded, unread by anything before it,
        straight to `GroundedAnswerService.answer()` for the `rag` lane
        only (Day 8's existing rule: memory never affects policy,
        retrieval, or citation validation -- see `answer_service.py`).
        `reference_context` (Task 8), when given, may resolve a dangling
        reference in `question` before Gate-A ever classifies it."""

        # 1. Day 5 input policy -- explicit, first, exactly as
        # `GroundedAnswerService.answer()` runs it (required pipeline
        # order: this happens before Gate-A, not inside it).
        normalized = normalize_input(question)
        policy = evaluate_policy(normalized.normalized)
        if policy.outcome is PolicyOutcome.BLOCK:
            return Blocked(question=question, reason=policy.reason, category=policy.category)
        if policy.outcome is PolicyOutcome.CLARIFY:
            return Clarify(question=question, reason=policy.reason, category=policy.category)

        # 2. Session memory may resolve a dangling reference (Task 8) --
        # plain text, never policy. Only affects what Gate-A sees below;
        # the *original* `question` is still what every returned result
        # carries and what the `rag` lane retrieves/answers over.
        resolved_question = question
        if reference_context is not None:
            resolved_question = resolve_reference(question, reference_context)

        # 3. Gate-A (Task 3).
        gate_decision: GateADecision = self.gate_a.classify(resolved_question)

        # 4. Lane selector (Task 5).
        lane_decision: LaneDecision = self.lane_selector.select(gate_decision)

        # 5. Selected lane behavior.
        if lane_decision.lane is LaneId.RAG:
            return self.rag_service.answer(resolved_question, cancellation, memory_context=memory_context)

        if lane_decision.lane is LaneId.MODE_B:
            return ModeBSelected(
                question=question,
                intent_id=lane_decision.intent_id,
                domain=lane_decision.domain,
                reason_code=lane_decision.reason_code,
                ontology_version=lane_decision.ontology_version,
            )

        if lane_decision.lane is LaneId.CLARIFY:
            return GateClarify(
                question=question,
                clarification_question=gate_decision.clarification_question or "",
                candidate_intents=tuple(gate_decision.candidate_intents),
                reason_code=lane_decision.reason_code,
                ontology_version=lane_decision.ontology_version,
            )

        if lane_decision.lane is LaneId.SAFE_FAST_PATH:
            return SafeFastPathAnswer(
                question=question,
                intent_id=lane_decision.intent_id,
                answer=self._safe_fast_path_answer(lane_decision.intent_id),
                reason_code=lane_decision.reason_code,
                ontology_version=lane_decision.ontology_version,
            )

        # LaneId.BLOCK (unsupported or Gate-A-level blocked).
        return GateBlocked(
            question=question,
            reason_code=lane_decision.reason_code,
            ontology_version=lane_decision.ontology_version,
        )

    def _safe_fast_path_answer(self, intent_id: str) -> str:
        """Deterministic, governed response for a `safe_fast_path` intent
        -- built only from committed `Intent.description` text (Task 1),
        never the Model Gateway. Lists every active governed intent's own
        description, so what this lane reports as "supported" can never
        drift from what Gate-A actually governs."""
        matched_intent = self.registry.get_intent(intent_id)
        supported = [i.description for i in self.registry.intents if i.status is LifecycleStatus.ACTIVE]
        return matched_intent.description + " Supported requests: " + "; ".join(supported) + "."
