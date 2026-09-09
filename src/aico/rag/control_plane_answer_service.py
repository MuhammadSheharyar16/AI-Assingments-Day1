"""
Day 9 Task 9/11 -- integrate Gate-A (Task 3) and the lane selector
(Task 5) in front of the Day 5 answer pipeline
(`answer_service.GroundedAnswerService`), with decision provenance /
observability (Task 11) on both stages.

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

Task 11 -- decision provenance / observability: steps 3 and 4 above each
run inside their own OTel span (`"gate_a"`, `"lane_selection"`), carrying
exactly the sanitized fields the assignment names -- `ontology_version`,
`domain`, `intent_id`, `gate_a.status`/`lane`, `reason_code`, and a
directly measured `latency_ms` -- and nothing else. Neither span, nor
anything else in this module, ever receives `question` (the user's raw
text), `resolved_question`, or `clarification_question` as an attribute --
there is no call site here that could leak them (Task 11 rule: "Do not log
full prompt/session/evidence content, authorization claims or secrets").

`request_id`/`correlation_id` are deliberately NOT explicit parameters or
span attributes here. `answer_service.py`'s own module docstring documents
why: this module intentionally never imports `aico.api`/`aico.observability`
(same layering `answer_service.py` already keeps -- see its docstring),
so it has no way to read them directly. Day 6 Task 9 already solved
"preserve correlation context" for exactly this shape of problem: a
caller (`api/app.py`'s `api.ask` root span, were this wired in) sets those
two IDs as attributes once, on the span it opens *around* this call, and
every span created here becomes a *child* of that span automatically
(Python's `start_as_current_span` uses the ambient current span as
parent) -- so `gate_a`/`lane_selection` share that request's one
`trace_id` without this module needing to know either ID exists. This is
the identical mechanism `answer_service.py`'s "policy"/"retrieval"/
"model_gateway" spans already rely on (see its own module docstring and
`app.py`'s Task 9 section) -- Task 11 does not introduce a new
correlation mechanism, it reuses the one already proven in
`tests/test_day06_observability.py`.

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

import time
from dataclasses import dataclass, field

from opentelemetry import trace

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

# Task 11 -- same pattern `answer_service.py` already uses and documents:
# `opentelemetry.trace.get_tracer(__name__)` directly, never importing
# `aico.observability` here. The tracer works against whatever provider
# `aico.observability.telemetry.configure_tracing()` installs (or a
# harmless no-op default when nothing has configured one yet, e.g. a test
# that imports this module directly without importing `api/app.py`).
_tracer = trace.get_tracer(__name__)

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

        # 3. Gate-A (Task 3). Task 11 -- "gate_a" span, sanitized attributes
        # only (ontology_version/domain/intent_id/status/reason_code/
        # latency_ms) -- never the question text. `request_id`/
        # `correlation_id` are deliberately NOT set here: this module does
        # not import `aico.api` (same boundary `answer_service.py` already
        # keeps), so those IDs are carried the same way every other span
        # in this codebase already carries them -- as attributes on
        # whatever root span a caller (a future `app.py` integration) has
        # open around this call, with every span below it sharing that
        # root's trace_id automatically (Day 6 Task 9's own established
        # mechanism, not a new one).
        with _tracer.start_as_current_span("gate_a") as span:
            start = time.monotonic()
            gate_decision: GateADecision = self.gate_a.classify(resolved_question)
            latency_ms = (time.monotonic() - start) * 1000
            span.set_attribute("gate_a.ontology_version", gate_decision.ontology_version)
            span.set_attribute("gate_a.status", gate_decision.status.value)
            span.set_attribute("gate_a.domain", gate_decision.domain or "")
            span.set_attribute("gate_a.intent_id", gate_decision.intent_id or "")
            span.set_attribute("gate_a.reason_code", gate_decision.reason_code)
            span.set_attribute("gate_a.latency_ms", latency_ms)

        # 4. Lane selector (Task 5). Task 11 -- "lane_selection" span, same
        # sanitized-attribute rule as above.
        with _tracer.start_as_current_span("lane_selection") as span:
            start = time.monotonic()
            lane_decision: LaneDecision = self.lane_selector.select(gate_decision)
            latency_ms = (time.monotonic() - start) * 1000
            span.set_attribute("lane_selection.ontology_version", lane_decision.ontology_version)
            span.set_attribute("lane_selection.lane", lane_decision.lane.value)
            span.set_attribute("lane_selection.domain", lane_decision.domain or "")
            span.set_attribute("lane_selection.intent_id", lane_decision.intent_id or "")
            span.set_attribute("lane_selection.reason_code", lane_decision.reason_code)
            span.set_attribute("lane_selection.latency_ms", latency_ms)

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
