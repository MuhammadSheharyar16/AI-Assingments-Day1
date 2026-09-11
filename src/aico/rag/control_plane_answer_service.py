"""
Day 9 Task 9/11 -- integrate Gate-A (Task 3) and the lane selector
(Task 5) in front of the Day 5 answer pipeline
(`answer_service.GroundedAnswerService`), with decision provenance /
observability (Task 11) on both stages.
Day 10 Task 13 -- integrate Gate-B (Day 10 Task 3-12) immediately after
the lane selector, before any protected lane behavior.

Required order (Day 9 assignment, extended by Day 10 Task 13):

    trusted identity -> session resolution -> Day 5 input policy
    -> Gate-A -> lane selector -> Gate-B -> selected/authorized lane behavior

`ControlPlaneAnswerService.answer()` implements everything from "Day 5
input policy" onward -- "session resolution" is, exactly as for
`GroundedAnswerService` today, `api/app.py`'s job (`MemorySessionService`),
not this module's. "Trusted identity" is different from Day 9: Gate-B
needs it directly (`identity: TrustedIdentity | None = None`, Task 13's
one new parameter on `answer()`) -- see "Gate-B integration is opt-out, not opt-in"
below for why it is optional here rather than required.

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
    5. Gate-B (Day 10 Task 3-12), run only when this service was built
       with a `policy_registry` AND only for the two lanes that could
       ever reach protected evidence, `rag`/`mode_b` -- `clarify`/`block`
       already terminated at step 4 (nothing governed to authorize yet),
       and `safe_fast_path` never touches protected data at all (its own
       branch below, deterministic and model-free), so Gate-B is not
       invoked for either. `GateB.authorize()` runs against `identity`
       (this method's own new parameter), `gate_decision`, `lane_decision`
       and an optional `requested` (`GateBRequest`, Task 5's caller-
       declared scope-narrowing input). `deny` -> `GateBDenied`; `clarify`
       -> `GateBAuthorizationClarify` (Task 10 -- never a role/tenant/permission/
       clearance question, only a safe, policy-scoped one); `allow` ->
       step 6 proceeds, carrying `gate_b_decision`'s sanitized provenance
       forward.
    6. Selected/authorized lane behavior:
         rag            -> delegates to the wrapped `GroundedAnswerService`
                            for retrieval -> Model Gateway -> typed
                            contract/semantic validation -> citation
                            validation -- Day 5's pipeline, unmodified;
                            reached only after Gate-B `allow` (or when
                            Gate-B is not active for this service at all --
                            see "Gate-B integration is opt-out, not opt-in" below).
         mode_b         -> `ModeBSelected`: the governed, now Gate-B-
                            authorized selection is returned; nothing
                            executes it (Day 9/10 working rule: "Do not
                            implement an uncontrolled Mode-B query
                            executor" -- there is no database import
                            anywhere in this module, Day 10 unchanged).
         clarify        -> `GateClarify`, carrying Gate-A's own
                            deterministically generated
                            `clarification_question` (Task 6).
         block          -> `GateBlocked` (an unsupported OR a Gate-A-level
                            blocked request -- `lane_policy.md`:
                            "unsupported or blocked request -> block").
         safe_fast_path -> `SafeFastPathAnswer`: a deterministic, governed
                            response built only from the matched intent's
                            own `Intent.description` plus every active
                            intent's description (never model-generated;
                            never Gate-B-gated -- see step 5).
       Only the `rag` branch ever calls the wrapped `GroundedAnswerService`
       (i.e. only it can ever reach retrieval or the Model Gateway) -- the
       other branches return a typed value directly. `test_day09_gate_a.py`'s
       Task 7 static-import check already proves `aico.control` itself
       cannot reach retrieval/Mode-B; this module is the one place that
       *could* (it legitimately holds a `GroundedAnswerService`), and it
       only ever does so from the single `rag` branch, now additionally
       gated behind Gate-B `allow` (or an inactive Gate-B) for that branch.

## Gate-B integration is opt-out, not opt-in (Task 13)

`policy_registry: PolicyRegistry | None = None` (a new, optional
constructor field) is what activates Gate-B on a given
`ControlPlaneAnswerService` instance -- `None` preserves Day 9's exact
behavior: no Gate-B span, no `GateBDenied`/`GateBAuthorizationClarify`
outcome ever produced, `identity`/`requested` accepted by `.answer()` but
unread. Wiring this to `None` is a real, supported mode (unit tests build
one this way to exercise Day 9 behavior directly), but it is not what a
deployment gets by default: `config/control-plane.yaml`'s `gate_b.enabled`
is `true` unless a deployment explicitly opts out -- shipping ungoverned
until an operator remembers to flip a flag is exactly the fail-open shape
Day 10 exists to close.

The one committed opt-out is Day 9's own synthetic identity space:
`ontology/registry.v1.json`'s three synthetic intents each map to exactly
one/two-or-more governed data classifications in
`policy/gate_b_policy.v1.json`'s own committed rules, and this module's
`.answer()` takes a bare free-text `question` with no per-request
classification hint -- meaning a caller that does not supply
`requested.data_class` will genuinely hit Task 10's `clarify` path for
every `rag`/`mode_b` request against a rule that allows more than one
classification (`GB-R001`/`GB-R003`/`GB-R004`, all real, all in the
committed policy). That is correct Gate-B behavior (Task 10: "policy needs
one selected" is a real, safe ambiguity here), not a bug -- but it is also
a materially different outcome shape than Day 9's own already-passing
regression suite (`test_day09_api_integration.py` and friends) exercises
against an identity with no governed role at all. Rather than relying on
the shipped default to stay ungoverned on its behalf, that file explicitly
overrides `config/control-plane.yaml`'s `gate_b.enabled` to `false` for
its own requests (see its own module docstring) -- an explicit,
documented exception, not the default anyone else inherits silently.

`/ask/governed` (`api/control_plane.py`) forwards `identity`
unconditionally and resolves `policy_registry` from
`api/dependencies.py`'s `get_policy_registry`, so Gate-B *is* reachable
through a real request today -- `get_control_plane_answer_service` passes
that resolved `policy_registry` into this class (rather than leaving it
`None`) whenever `control_plane_config.gate_b.enabled` is true, which is
the committed default. `test_day10_control_plane_integration.py` proves
the full required order end to end at this class's own level
(constructing the service *with* a real `policy_registry` directly), and
`test_day10_api_integration.py` proves the identical order through a real
HTTP request to `/ask/governed` with the committed `gate_b.enabled: true`
default left untouched and a real Day 10 governed identity.

## Gate-C integration (Day 11 Task 13)

Required order for the `rag` lane (`Day 11 Task.pdf`, Task 13):

    trusted identity -> session -> input policy -> Gate-A -> lane selector
    -> Gate-B -> retrieval -> Gate-C -> Model Gateway -> typed contract
    validation -> semantic validation -> citation validation -> safe
    disclosure -> response

Everything through Gate-B is exactly the pipeline already described above
(steps 1-5); Gate-C (Task 9) slots in between retrieval and the Model
Gateway, inside the `rag` branch of step 6, via a new private method,
`_answer_rag_with_gate_c()`. Reached only when this service was built with
`source_registry`/`gate_c_policy_registry`/`evidence_adapter` (see
`ControlPlaneAnswerService`'s own docstring) -- `None` for all three (still
the constructor default, and still what a direct `ControlPlaneAnswerService
(registry, rag_service)` call gets) preserves the exact Day 9/10 `rag`
behavior: retrieval flows straight into `GroundedAnswerService.answer()`,
unmodified.

`source_registry`/`gate_c_policy_registry` govern only Day 11's own pinned
synthetic fixtures (`evidence/source_registry.v1.json`/`policy/
gate_c_policy.v1.json`, unmodified -- used exactly as the graded Day 11
pack shipped them). A real `EvidenceChunk` (`aico.rag.citation_validator`,
`chunk_id`/`source_file`/`text`) carries none of the governed provenance
metadata Gate-C's `EvidenceItem` requires on its own, so wiring Gate-C
against the real corpus needed its own real, non-fabricated governed data
rather than reusing (or editing) the pinned synthetic fixtures:
`RealCorpusEvidenceAdapter` (`aico.rag.real_corpus_evidence_adapter`) maps
real retrieved chunks against `evidence/real_corpus_source_registry.v1.json`/
`evidence/real_corpus_manifest.v1.json`/`policy/real_corpus_gate_c_
policy.v1.json` -- a second, additional, real-corpus-specific set of
governed data (`scripts/day11_generate_real_corpus_registry.py`, built
from each document's own front-matter and real commit history, never
invented values), separate from and never touching the pinned Day 11 pack
files. `get_control_plane_answer_service` (`api/dependencies.py`) wires
this real adapter in by default (`config/control-plane.yaml`'s `gate_c.
enabled: true`) whenever Gate-B is also active, the same "governs by
default, an explicit opt-out only where a committed synthetic identity
space would otherwise get a materially different outcome" posture
`gate_b.enabled` already established (see "Gate-B integration is opt-out,
not opt-in" above) -- see `real_corpus_evidence_adapter.py`'s own module
docstring for exactly what is/is not asserted about the real corpus
(coarse, source/freshness/tenant/classification trust only; no per-
supplier facet/claim model, since this corpus is governance policy text,
not supplier records -- conflict detection is consequently always
`NO_CONFLICT` here, honestly, not a gap papered over).

`_answer_rag_with_gate_c()`'s own required order, once reached:

    1. Retrieval (`self.rag_service.retriever(resolved_question)`) -- its
       own `"retrieval"` span, identical shape to `GroundedAnswerService.
       answer()`'s own (this method is what *replaces* that call for the
       Gate-C-active `rag` lane, not something layered on top of it).
    2. `self.evidence_adapter(resolved_question, lane_decision, retrieved)`
       builds the candidate `EvidencePackage` plus the governed
       `request_kind` Gate-C needs (`GateCRequest`) -- the one place this
       pipeline's real chunks become Day 11's governed shape.
    3. `GateC.evaluate()` (Task 9) -- opens its own `"gate_c"` span (plus
       Task 14's nested `"provenance_validation"`/`"freshness_validation"`/
       `"completeness_validation"` child spans) and records its own
       sanitized decision-provenance attributes there (policy/source-
       registry versions, the decision itself, reason codes, evidence/
       missing-facet/conflict *counts*, a freshness summary string) --
       never raw evidence content, never `question`/`resolved_question`;
       see `gate_c.py`'s own module docstring, "Day 11 Task 14" section.
       This call becomes a child of whatever span is already current here
       (correlation context preserved the same way every span in this
       pipeline already preserves it).
    4. `reject`/`insufficient_evidence`/`clarify` each return their own
       typed result (`GateCRejected`/`GateCInsufficientEvidence`/
       `GateCClarify`, all carrying only Gate-C's own sanitized
       provenance) -- zero Model Gateway calls (Task 11's no-fall-through
       guarantee, proven again here at the service-integration level, the
       same way `test_day10_control_plane_integration.py` re-proves
       Gate-B's identical guarantee beyond `test_day10_no_fallthrough.py`'s
       own `GateB`-direct proof).
    5. `allow` -- "the prompt builder must receive only Gate-C-validated
       evidence" (Task 13's own rule): `validated_evidence_ids` is matched
       back to the *original* `EvidenceChunk` objects retrieval actually
       returned (never a chunk reconstructed from `EvidenceItem` fields),
       then handed to `GroundedAnswerService._answer_from_evidence()`
       (Task 13's own refactor of `answer_service.py`) -- Day 5's real,
       already-tested prompt building, Model Gateway call, typed contract/
       semantic validation, citation validation and support validation run
       completely unmodified from here, over exactly this narrowed list.
       "Do not let Gate-C replace post-generation citation validation;
       they solve different problems" is true structurally: citation
       validation still runs, as Day 5 left it, checking membership
       against `filtered_chunks` -- a citation naming a chunk Gate-C
       rejected fails exactly as a citation naming a chunk retrieval never
       returned at all always has.

Task 11 -- decision provenance / observability: steps 3, 4 and (when
active) 5 above each run inside their own OTel span (`"gate_a"`,
`"lane_selection"`, `"gate_b"`), carrying exactly the sanitized fields the
assignment names -- `ontology_version`/`policy_version`, `domain`,
`intent_id`, `gate_a.status`/`lane`/`gate_b.decision`, `reason_code`, and a
directly measured `latency_ms` -- and nothing else. No span, nor anything
else in this module, ever receives `question` (the user's raw text),
`resolved_question`, or `clarification_question` as an attribute -- there
is no call site here that could leak them (Task 11 rule: "Do not log full
prompt/session/evidence content, authorization claims or secrets"); the
`gate_b` span in particular never receives `identity` itself (no
tenant_id/user_id/roles attribute) -- only `gate_b_decision`'s own already-
sanitized fields.

`request_id`/`correlation_id` are deliberately NOT explicit parameters or
span attributes here. `answer_service.py`'s own module docstring documents
why: that module intentionally never imports `aico.api`/`aico.observability`,
and this module inherited the same discipline for Day 9's own additions
(Gate-A/the lane selector needed neither). Day 10 Task 13 imports exactly
one thing from `aico.api` -- `TrustedIdentity` (`aico.api.identity`), a
plain, dependency-free type (it imports only `jwt`/`fastapi`/its own
`errors.py`, nothing back into `aico.rag`, so this is not a circular
import) -- because Gate-B (Task 3) genuinely needs identity earlier in the
pipeline than anything Day 9 ever did; `request_id`/`correlation_id`
themselves are still never read or set here. Day 6 Task 9 already solved
"preserve correlation context" for exactly this shape of problem: a
caller (`api/app.py`'s `api.ask` root span, were this wired in) sets those
two IDs as attributes once, on the span it opens *around* this call, and
every span created here becomes a *child* of that span automatically
(Python's `start_as_current_span` uses the ambient current span as
parent) -- so `gate_a`/`lane_selection`/`gate_b` share that request's one
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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from opentelemetry import trace

from aico.api.identity import TrustedIdentity
from aico.control.config import ControlPlaneConfig
from aico.control.disclosure import ProtectedField, SafeDisclosureView, apply_disclosure
from aico.control.gate_a import GateA
from aico.control.gate_b import GateB, GateBRequest
from aico.control.gate_c import GateC, GateCRequest, GateCStatus
from aico.control.lane_selector import LaneSelector
from aico.control.models import GateADecision, GateBDecision, GateBStatus, LaneDecision
from aico.control.ontology import LaneId, LifecycleStatus
from aico.control.ontology_registry import OntologyRegistry
from aico.control.policy_models import DisclosureAction, DisclosureProfile
from aico.control.policy_registry import PolicyRegistry
from aico.evidence.models import EvidencePackage
from aico.evidence.policy import GateCPolicyRegistry
from aico.evidence.provenance import GovernedProvenanceIndex
from aico.evidence.source_registry import SourceRegistry
from aico.memory.context_builder import MemoryContext, SessionReferenceContext, resolve_reference
from aico.platform.model_gateway import CancellationToken
from aico.rag.answer_service import AnswerResult, Blocked, Clarify, GroundedAnswerService
from aico.rag.citation_validator import EvidenceChunk
from aico.security.input_policy import PolicyOutcome, evaluate_policy
from aico.security.normalization import normalize_input

# Day 11 Task 13 -- given the resolved question, the current LaneDecision
# (carries `intent_id`/`domain`) and what retrieval actually returned,
# produce Gate-C's own candidate `EvidencePackage` plus the governed
# `request_kind` (`GateCRequest`, `aico.control.gate_c`) this request
# should be evaluated under. A real `EvidenceChunk` (`chunk_id`/
# `source_file`/`text`) carries none of the governed provenance metadata
# (`source_id`/`content_hash`/`tenant_id`/`data_classification`/
# `evidence_facets`/`claims`) Gate-C's `EvidenceItem` requires on its own,
# so this mapping is fundamentally caller/domain-specific -- the same
# reason `Retriever`/`PolicyEvaluator` (`answer_service.py`) are
# injectable seams rather than one hardcoded implementation.
# `RealCorpusEvidenceAdapter` (`aico.rag.real_corpus_evidence_adapter`) is
# the real, production implementation of this seam for the real
# `data/documents/` corpus (see "Gate-C integration" below); tests build
# their own throwaway ones against Day 11's pinned synthetic fixtures.
EvidenceAdapter = Callable[[str, LaneDecision, list[EvidenceChunk]], tuple[EvidencePackage, str | None]]


class GateCIntegrationError(Exception):
    """Raised by `ControlPlaneAnswerService.__post_init__` when Gate-C
    integration is only *partially* configured -- `source_registry`,
    `gate_c_policy_registry` and `evidence_adapter` must all be supplied
    together (Gate-C cannot evaluate without a source registry, a policy,
    or a way to build the candidate package at all), and `policy_registry`
    (Gate-B) must also be active (Gate-C's own required input list starts
    with "Gate-B allowed decision/effective scope" -- there is no
    `GateBDecision` to hand it otherwise). Never raised for a normal,
    fully-configured-or-fully-inactive construction; exists to fail loudly
    at construction time rather than crash confusingly inside the first
    `.answer()` call that reaches the `rag` lane."""

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


@dataclass(frozen=True)
class GateBDenied:
    """Day 10 Task 13 -- lane selection resolved a governed `rag`/`mode_b`
    intent+lane, but Gate-B (Task 3-12) denied authorization for this
    trusted caller. Distinct from `GateBlocked` (Gate-A's own block: no
    governed intent matched at all, a different stage/cause entirely) --
    this fires only once a specific governed intent+lane is already known,
    for a caller this policy does not authorize for it. Carries only
    Gate-B's own sanitized provenance (Day 10 working rule: "Policy
    decisions produce sanitized provenance") -- never raw policy
    internals, and never any retrieved/generated content: Gate-B runs
    strictly before retrieval (Task 11's no-fall-through guarantee), so
    there is nothing here that could leak protected data."""

    question: str
    reason_code: str
    policy_version: str
    rule_id: str | None = None


@dataclass(frozen=True)
class GateBAuthorizationClarify:
    """Day 10 Task 13 -- a governed `rag`/`mode_b` rule matched and would
    have allowed the request, but Gate-B needs one safe, policy-scoped
    piece of information selected before it can authorize (Task 10) -- in
    the committed policy, which governed data classification the caller
    is asking about. Distinct from Gate-A's own `GateClarify` (ambiguous
    *intent*, an earlier stage) -- this never asks for a role/tenant/
    permission/clearance (`gate_b.py`'s own documented boundary, Task 10)."""

    question: str
    reason_code: str
    policy_version: str
    rule_id: str | None = None


@dataclass(frozen=True)
class GateCRejected:
    """Day 11 Task 13 -- the `rag` lane reached Gate-C (Task 9), which
    returned `reject`: an untrusted source, broken provenance, a Gate-B
    scope violation, or an unresolved conflict (working rule: "unresolved
    conflict cannot silently proceed to the model"). Carries only Gate-C's
    own sanitized decision provenance -- never raw evidence content, and
    never any generated content: Gate-C runs strictly before the Model
    Gateway (Task 11's no-fall-through guarantee), so there is nothing
    here that could leak it."""

    question: str
    reason_codes: tuple[str, ...]
    rejected_evidence_ids: tuple[str, ...]
    policy_version: str
    source_registry_version: str


@dataclass(frozen=True)
class GateCInsufficientEvidence:
    """Day 11 Task 13 -- Gate-C returned `insufficient_evidence`: valid
    evidence exists but does not cover what this governed request
    requires (missing required facets, or fewer validated items than the
    governed rule's own `minimum_valid_items`). Distinct from Day 5's own
    `InsufficientEvidence` (the *model* explicitly declining to answer
    from evidence it was actually shown) -- this fires before generation
    ever runs at all."""

    question: str
    reason_codes: tuple[str, ...]
    missing_facets: tuple[str, ...]
    policy_version: str
    source_registry_version: str


@dataclass(frozen=True)
class GateCClarify:
    """Day 11 Task 13 -- Gate-C returned `clarify`: the request itself is
    safely ambiguous about which governed evidence-quality rule applies
    (`GateCRequest.request_kind` could not be resolved by the
    `EvidenceAdapter`). Distinct from Gate-A's own `GateClarify` (ambiguous
    *intent*, an earlier stage) and Gate-B's `GateBAuthorizationClarify`
    (ambiguous *data classification*) -- this is Gate-C's own, narrower
    "which evidence-quality requirement" ambiguity."""

    question: str
    reason_codes: tuple[str, ...]
    policy_version: str
    source_registry_version: str


# The full set of results `ControlPlaneAnswerService.answer()` can return:
# Day 5's own five (via the early policy short-circuit, or via delegating
# to `GroundedAnswerService` for the `rag` lane) plus the four Day 9 lane
# outcomes, the two Day 10 Gate-B outcomes, and the three Day 11 Gate-C
# outcomes above, none of which has an earlier-day equivalent.
ControlPlaneAnswerResult = (
    AnswerResult
    | GateBlocked
    | GateClarify
    | ModeBSelected
    | SafeFastPathAnswer
    | GateBDenied
    | GateBAuthorizationClarify
    | GateCRejected
    | GateCInsufficientEvidence
    | GateCClarify
)


@dataclass
class ControlPlaneAnswerService:
    """Construct with an `OntologyRegistry` (Task 2) and a
    `GroundedAnswerService` (Day 5) -- `gate_a`/`lane_selector` are built
    from the registry automatically unless supplied (tests may inject
    their own, e.g. built from a throwaway registry). See the module
    docstring for the full pipeline and why this is not yet the service
    `api/app.py` calls.

    `control_plane_config` (Task 12, optional) is `config/control-plane.yaml`,
    already loaded (`aico.control.config.load_control_plane_config`) -- when
    given, its `enabled_lanes` is passed straight to the `LaneSelector` this
    class builds, so a deployment can turn a lane off without touching the
    ontology (`lane_selector.py`'s own module docstring on what
    `enabled_lanes` can and cannot do). `None` (the default) applies no
    deployment-level restriction beyond what the registry already governs --
    every existing caller of `ControlPlaneAnswerService(registry, rag_service)`
    is unaffected.

    `policy_registry` (Day 10 Task 13, optional) is `policy/gate_b_policy.v1.json`,
    already loaded (`aico.control.policy_registry.PolicyRegistry.load()`) --
    when given, `gate_b` (`GateB`, built from it plus `registry`) is
    activated for every `.answer()` call. `None` (the default) leaves
    `gate_b` unset and Gate-B entirely out of the pipeline -- see the
    module docstring's "Gate-B integration is opt-out, not opt-in" section for why.

    `source_registry`/`gate_c_policy_registry`/`evidence_adapter` (Day 11
    Task 13, optional, and only together -- see "Gate-C real-corpus
    wiring" below) activate Gate-C (`GateC`, built from the first two) for
    the `rag` lane only, once Gate-B has already granted `allow`. All
    three `None` (the default) preserves Day 9/10 behavior for the `rag`
    lane exactly: retrieval flows straight into `GroundedAnswerService.
    answer()`, no Gate-C span, no `GateCRejected`/
    `GateCInsufficientEvidence`/`GateCClarify` outcome ever produced.
    `provenance_index` (Task 5, optional) is forwarded to every
    `GateC.evaluate()` call unmodified when given -- `None` runs Task 4's
    self-consistency check only, exactly `GateC.evaluate()`'s own default
    behavior when no integrity index is supplied."""

    registry: OntologyRegistry
    rag_service: GroundedAnswerService
    control_plane_config: ControlPlaneConfig | None = None
    policy_registry: PolicyRegistry | None = None
    source_registry: SourceRegistry | None = None
    gate_c_policy_registry: GateCPolicyRegistry | None = None
    evidence_adapter: EvidenceAdapter | None = None
    provenance_index: GovernedProvenanceIndex | None = None
    gate_a: GateA = field(init=False)
    lane_selector: LaneSelector = field(init=False)
    gate_b: GateB | None = field(init=False)
    gate_c: GateC | None = field(init=False)

    def __post_init__(self) -> None:
        self.gate_a = GateA(self.registry)
        enabled_lanes = self.control_plane_config.enabled_lanes if self.control_plane_config is not None else None
        self.lane_selector = LaneSelector(self.registry, enabled_lanes=enabled_lanes)
        self.gate_b = (
            GateB(self.policy_registry, ontology_registry=self.registry) if self.policy_registry is not None else None
        )

        # Day 11 Task 13 -- Gate-C is configured only when all three of its
        # own fields are supplied together (never a partial configuration
        # silently treated as "inactive" or "active with a missing piece"),
        # and only when Gate-B is also active (Gate-C's own first required
        # input is a real `GateBDecision`; see `GateCIntegrationError`'s
        # own docstring).
        gate_c_fields = (self.source_registry, self.gate_c_policy_registry, self.evidence_adapter)
        gate_c_configured = any(f is not None for f in gate_c_fields)
        if gate_c_configured:
            if not all(f is not None for f in gate_c_fields):
                raise GateCIntegrationError(
                    "Gate-C integration requires source_registry, gate_c_policy_registry and "
                    "evidence_adapter to all be supplied together"
                )
            if self.gate_b is None:
                raise GateCIntegrationError(
                    "Gate-C integration requires policy_registry (Gate-B) to also be active -- "
                    "Gate-C's first required input is a real GateBDecision"
                )
            self.gate_c = GateC(source_registry=self.source_registry, policy_registry=self.gate_c_policy_registry)
        else:
            self.gate_c = None

    def answer(
        self,
        question: str,
        cancellation: CancellationToken | None = None,
        *,
        memory_context: MemoryContext | None = None,
        reference_context: SessionReferenceContext | None = None,
        identity: TrustedIdentity | None = None,
        requested: GateBRequest | None = None,
    ) -> ControlPlaneAnswerResult:
        """See the module docstring for the full required pipeline order.
        `memory_context` is forwarded, unread by anything before it,
        straight to `GroundedAnswerService.answer()` for the `rag` lane
        only (Day 8's existing rule: memory never affects policy,
        retrieval, or citation validation -- see `answer_service.py`).
        `reference_context` (Task 8), when given, may resolve a dangling
        reference in `question` before Gate-A ever classifies it.
        `identity`/`requested` (Day 10 Task 13) are read only when this
        service was built with `policy_registry` -- see `gate_b`'s own
        field docstring above -- and only ever reach `GateB.authorize()`,
        never anything upstream of it (Gate-A/the lane selector remain
        exactly the Day 9 identity-blind classification/routing they
        always were)."""

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

        # 5. Gate-B (Day 10 Task 3-12), active only when this service was
        # built with `policy_registry`, and only for the two lanes that
        # could ever reach protected evidence -- `clarify`/`block` are
        # already terminal by this point (nothing governed to authorize),
        # and `safe_fast_path` never touches protected data at all (see
        # its own branch below), so Gate-B is not invoked for either.
        # `identity` is never logged here -- only `gate_b_decision`'s own
        # already-sanitized fields become span attributes (Task 14: "do
        # not log ... authorization tokens, full claims").
        gate_b_decision = None
        if self.gate_b is not None and lane_decision.lane in (LaneId.RAG, LaneId.MODE_B):
            with _tracer.start_as_current_span("gate_b") as span:
                start = time.monotonic()
                gate_b_decision = self.gate_b.authorize(identity, gate_decision, lane_decision, requested)
                latency_ms = (time.monotonic() - start) * 1000
                span.set_attribute("gate_b.ontology_version", gate_decision.ontology_version)
                span.set_attribute("gate_b.policy_version", gate_b_decision.policy_version)
                span.set_attribute("gate_b.decision", gate_b_decision.decision.value)
                span.set_attribute("gate_b.rule_id", gate_b_decision.rule_id or "")
                span.set_attribute("gate_b.intent_id", gate_b_decision.intent_id or "")
                span.set_attribute("gate_b.lane", gate_b_decision.lane.value if gate_b_decision.lane else "")
                span.set_attribute("gate_b.reason_code", gate_b_decision.reason_code)
                span.set_attribute("gate_b.effective_scope_summary", gate_b_decision.effective_scope_summary)
                span.set_attribute("gate_b.disclosure_profile", gate_b_decision.disclosure_profile or "")
                span.set_attribute("gate_b.latency_ms", latency_ms)

            if gate_b_decision.decision is GateBStatus.DENY:
                return GateBDenied(
                    question=question,
                    reason_code=gate_b_decision.reason_code,
                    policy_version=gate_b_decision.policy_version,
                    rule_id=gate_b_decision.rule_id,
                )
            if gate_b_decision.decision is GateBStatus.CLARIFY:
                return GateBAuthorizationClarify(
                    question=question,
                    reason_code=gate_b_decision.reason_code,
                    policy_version=gate_b_decision.policy_version,
                    rule_id=gate_b_decision.rule_id,
                )
            # GateBStatus.ALLOW falls through to step 6 below.

        # 6. Selected/authorized lane behavior.
        if lane_decision.lane is LaneId.RAG:
            if self.gate_c is not None:
                # `gate_b_decision` is guaranteed a real, `ALLOW` decision
                # here: Gate-C is only ever configured together with
                # Gate-B (`__post_init__`), and `deny`/`clarify` already
                # returned above in step 5.
                assert gate_b_decision is not None and gate_b_decision.decision is GateBStatus.ALLOW
                return self._answer_rag_with_gate_c(
                    question, resolved_question, cancellation, memory_context, gate_b_decision, lane_decision
                )
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

    def _answer_rag_with_gate_c(
        self,
        question: str,
        resolved_question: str,
        cancellation: CancellationToken | None,
        memory_context: MemoryContext | None,
        gate_b_decision: GateBDecision,
        lane_decision: LaneDecision,
    ) -> ControlPlaneAnswerResult:
        """Day 11 Task 13's required order for the `rag` lane, picking up
        exactly where step 6's `rag` branch (in `.answer()`, above) leaves
        off: retrieval -> Gate-C -> (only on `allow`) Model Gateway -> typed
        contract validation -> semantic validation -> citation validation
        (via `GroundedAnswerService._answer_from_evidence()`, reused rather
        than reimplemented -- "do not let Gate-C replace post-generation
        citation validation; they solve different problems"). Only ever
        called once `self.gate_c`/`self.evidence_adapter` are known non-None
        (`__post_init__`) and `gate_b_decision.decision` is known `ALLOW`
        (the caller's own assertion, immediately above the one call site)."""
        assert self.gate_c is not None
        assert self.evidence_adapter is not None

        # Retrieval -- the identical span/call `GroundedAnswerService.
        # answer()` would otherwise run internally; done here instead so
        # Gate-C can see what was actually retrieved before any of it
        # reaches a prompt.
        with _tracer.start_as_current_span("retrieval") as span:
            start = time.monotonic()
            retrieved = self.rag_service.retriever(resolved_question)
            latency_ms = (time.monotonic() - start) * 1000
            span.set_attribute("retrieval.retrieved_count", len(retrieved))
            span.set_attribute("retrieval.latency_ms", latency_ms)

        package, request_kind = self.evidence_adapter(resolved_question, lane_decision, retrieved)

        # Gate-C (Task 9). Its own `"gate_c"` span (plus the nested
        # `"provenance_validation"`/`"freshness_validation"`/
        # `"completeness_validation"` child spans, Task 14) is opened by
        # `evaluate()` itself, not here -- `GateC` is itself a multi-stage
        # orchestrator over four other Day 11 validator modules, the same
        # reason `GroundedAnswerService.answer()` traces its own pipeline
        # rather than leaving it to this class (see `gate_c.py`'s own
        # module docstring, "Day 11 Task 14" section, for why this is a
        # deliberate exception to `gate_a`/`gate_b`/`disclosure` staying
        # trace-free). This call becomes a child of whatever span is
        # already current (this method's own caller's span, if any) --
        # correlation context is preserved the same way every span in
        # this pipeline already preserves it (Day 6 Task 9's mechanism).
        gate_c_decision = self.gate_c.evaluate(
            gate_b_decision=gate_b_decision,
            package=package,
            request=GateCRequest(request_kind=request_kind),
            provenance_index=self.provenance_index,
        )

        if gate_c_decision.decision is GateCStatus.REJECT:
            return GateCRejected(
                question=question,
                reason_codes=gate_c_decision.reason_codes,
                rejected_evidence_ids=gate_c_decision.rejected_evidence_ids,
                policy_version=gate_c_decision.policy_version,
                source_registry_version=gate_c_decision.source_registry_version,
            )
        if gate_c_decision.decision is GateCStatus.INSUFFICIENT_EVIDENCE:
            return GateCInsufficientEvidence(
                question=question,
                reason_codes=gate_c_decision.reason_codes,
                missing_facets=gate_c_decision.missing_facets,
                policy_version=gate_c_decision.policy_version,
                source_registry_version=gate_c_decision.source_registry_version,
            )
        if gate_c_decision.decision is GateCStatus.CLARIFY:
            return GateCClarify(
                question=question,
                reason_codes=gate_c_decision.reason_codes,
                policy_version=gate_c_decision.policy_version,
                source_registry_version=gate_c_decision.source_registry_version,
            )

        # GateCStatus.ALLOW -- "the prompt builder must receive only
        # Gate-C-validated evidence" (Task 13's own rule): match
        # `validated_evidence_ids` back to the *original* `EvidenceChunk`
        # objects retrieval actually returned (never reconstructed from
        # `EvidenceItem` fields), so citation validation below still checks
        # membership against the real retrieved chunk_ids, and a rejected
        # chunk's text is never read again.
        validated_ids = set(gate_c_decision.validated_evidence_ids)
        validated_chunk_ids = {item.chunk_id for item in package.items if item.evidence_id in validated_ids}
        filtered_chunks = [chunk for chunk in retrieved if chunk.chunk_id in validated_chunk_ids]

        return self.rag_service._answer_from_evidence(resolved_question, filtered_chunks, cancellation, memory_context)

    def _safe_fast_path_answer(self, intent_id: str) -> str:
        """Deterministic, governed response for a `safe_fast_path` intent
        -- built only from committed `Intent.description` text (Task 1),
        never the Model Gateway. Lists every active governed intent's own
        description, so what this lane reports as "supported" can never
        drift from what Gate-A actually governs."""
        matched_intent = self.registry.get_intent(intent_id)
        supported = [i.description for i in self.registry.intents if i.status is LifecycleStatus.ACTIVE]
        return matched_intent.description + " Supported requests: " + "; ".join(supported) + "."

    def disclose(
        self, gate_b_decision: GateBDecision, profile: DisclosureProfile | None, candidate_fields: Sequence[ProtectedField]
    ) -> SafeDisclosureView:
        """Day 10 Task 14's `"safe_disclosure"` span, wrapping Task 9's
        pure `apply_disclosure()` -- the same "orchestration layer adds
        tracing, decision logic itself stays pure/span-free" split this
        class already applies to Gate-A/the lane selector/Gate-B (Task 11)
        and `disclosure.py`/`redaction.py` deliberately keep (see their
        own module docstrings: no I/O, no tracing import, of their own).

        Not called from `.answer()` itself: this pipeline's `rag`/`mode_b`
        lanes have no structured, per-field-classified protected record to
        disclose yet (see the module docstring's "Gate-B integration is
        opt-out, not opt-in" section on the same underlying reason `answer()` cannot
        supply a `requested.data_class` on a caller's behalf either) -- a
        free-text RAG answer and a not-yet-executed Mode-B selection are
        not `ProtectedField` sequences. This method is the concrete,
        traced, directly-tested orchestration point a future caller uses
        once one exists; `test_day10_observability.py` proves the span
        end to end today with a hand-built `candidate_fields` sequence.

        Span attributes are counts and governed labels only -- never a raw
        field name, value, or masked value (Task 14: "do not log ... raw
        protected records")."""
        with _tracer.start_as_current_span("safe_disclosure") as span:
            start = time.monotonic()
            view = apply_disclosure(gate_b_decision, profile, candidate_fields)
            latency_ms = (time.monotonic() - start) * 1000
            allowed = sum(1 for f in view.fields if f.action is DisclosureAction.ALLOW)
            redacted = sum(1 for f in view.fields if f.action is DisclosureAction.REDACT)
            denied = sum(1 for f in view.fields if f.action is DisclosureAction.DENY)
            span.set_attribute("safe_disclosure.policy_version", view.policy_version)
            span.set_attribute("safe_disclosure.disclosure_profile", view.disclosure_profile or "")
            span.set_attribute("safe_disclosure.field_count", len(view.fields))
            span.set_attribute("safe_disclosure.allowed_count", allowed)
            span.set_attribute("safe_disclosure.redacted_count", redacted)
            span.set_attribute("safe_disclosure.denied_count", denied)
            span.set_attribute("safe_disclosure.latency_ms", latency_ms)
        return view
