"""
Day 5 Task 1 — grounded answer service.

The orchestration that wires every earlier piece into the one path
described in the assignment brief (build_outcome diagram):

    user question -> normalize -> policy -> retrieve -> build prompt
                   -> Model Gateway -> Day 4 typed contract validation
                   -> Day 4 semantic validation -> citation validation
                   -> final result

`GroundedAnswerService.answer()` is the single entry point. It never
bypasses retrieval, the Model Gateway, Day 4 typed contract validation,
Day 4 semantic validation (`aico.contracts.semantic.validate_semantic` -
the complete S1-S5 path, not a partial reimplementation of it), citation
validation, or answer-support validation (`aico.rag.support_validator` -
post-review hardening: citation-ID membership alone does not prove the
answer's claim is actually supported by its cited chunk's content) -
each stage below returns early only with one of the five typed result
values, and every one of those returns happens *after* the stage
responsible for it has actually run.

Result paths (grounding_rules.md, Task 1):
    GroundedAnswer      - a typed, cited, evidence-supported answer
    InsufficientEvidence- evidence does not support the question
    Clarify             - input policy needs the caller to disambiguate
    Blocked             - input policy rejected the question outright
    TypedFailure        - the gateway, Day 4 contract stage, or citation
                           validation stage failed closed

Day 6 Task 9 — OpenTelemetry spans: `answer()` wraps each stage of the
brief's traced flow ("API -> Policy -> Retrieval -> Model Gateway ->
Contract/Semantic Validation -> Citation Validation -> Response
Composition") in its own span - "policy", "retrieval", "model_gateway",
"validation" (contract + citation validation together, per Task 9's own
5-stage summary: "policy -> retrieval -> model gateway -> validation ->
response composition"), "response_composition". This uses
`opentelemetry.trace.get_tracer(__name__)` directly (the standard OTel
call, not a project-specific wrapper), so this module still does not
import anything from `aico.api`/`aico.observability` - the tracer works
against whatever provider `aico.observability.telemetry.configure_tracing()`
installs (or a harmless no-op default when nothing has configured one
yet, e.g. a Day 5 test that imports this module directly). Every
attribute set below is already a value this module treats as safe to
return/log elsewhere (a category string, a count, a model alias, a
boolean) - never the question, retrieved evidence text, or the raw model
completion.

Day 8 Task 7 — `answer()` gained an optional, keyword-only
`memory_context` (`aico.memory.context_builder.MemoryContext`, Task 5).
It flows into exactly one place: `build_prompt`'s new SESSION MEMORY
section (`prompt_builder.py`). Nothing else in this pipeline reads it -
policy, retrieval, citation validation and support validation are
unchanged and still act only on the current question and the current
turn's retrieved evidence. This is what makes Day 8's core rule
structurally true here, not just documented: memory literally cannot
reach the stages that decide what is retrieved or what counts as a valid
citation, because those stages never receive it as an argument.
"""
from __future__ import annotations

import pathlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from aico.contracts.errors import ValidationFailure
from aico.contracts.models import AnswerStatus, CitedAnswer, ConfidenceLabel
from aico.contracts.semantic import validate_semantic
from aico.contracts.validator import parse_and_validate
from aico.memory.context_builder import MemoryContext
from aico.platform.errors import ModelGatewayError
from aico.platform.model_gateway import CancellationToken, ModelGateway
from aico.rag.citation_validator import EvidenceChunk, validate_citations
from aico.rag.prompt_builder import build_prompt
from aico.rag.support_validator import validate_support
from aico.retrieval.bm25 import BM25Index
from aico.retrieval.search import load_chunks
from aico.security.input_policy import PolicyDecision, PolicyOutcome, evaluate_policy
from aico.security.normalization import normalize_input

DEFAULT_TOP_K = 4

_tracer = trace.get_tracer(__name__)


# ── Retrieval adapter (Day 2 remains the source of evidence) ────────────

class Retriever(Protocol):
    """Anything that turns a query into ranked evidence chunks. Satisfied
    by `BM25Retriever` (real, over the Day 2 index) and by any fake a test
    constructs - `GroundedAnswerService` never imports a retrieval
    implementation directly, only this protocol."""

    def __call__(self, query: str) -> list[EvidenceChunk]: ...


# Day 6 Task 10 - the input/policy component as an injectable seam,
# mirroring `Retriever` above: anything `(normalized_text) -> PolicyDecision`
# satisfies this - `evaluate_policy` (Day 5, unchanged) is the real one;
# tests inject a fake to force allow/clarify/block deterministically
# without depending on `input_policy.py`'s specific pattern rules.
PolicyEvaluator = Callable[[str], PolicyDecision]


class BM25Retriever:
    """Default retriever: BM25 (Day 1/2, unchanged) over the chunk index
    `aico.retrieval.ingest` already built. No network call, no embedding
    provider required - deterministic given an unchanged index, which is
    what Day 5's attack-fixture and grounding tests need."""

    def __init__(self, index_dir: pathlib.Path = pathlib.Path("data/index"), top_k: int = DEFAULT_TOP_K):
        chunks = load_chunks(index_dir)
        self._index = BM25Index(chunks)
        self._top_k = top_k

    def __call__(self, query: str) -> list[EvidenceChunk]:
        results = self._index.search(query, top_k=self._top_k)
        return [
            EvidenceChunk(chunk_id=r.chunk["chunk_id"], source_file=r.chunk["source_file"], text=r.chunk["text"])
            for r in results
        ]


# ── Typed result paths ───────────────────────────────────────────────────

@dataclass(frozen=True)
class GroundedAnswer:
    question: str
    answer: str
    citation_ids: tuple[str, ...]
    confidence_label: str
    retrieved_ids: tuple[str, ...]


@dataclass(frozen=True)
class InsufficientEvidence:
    question: str
    explanation: str
    retrieved_ids: tuple[str, ...]


@dataclass(frozen=True)
class Clarify:
    question: str
    reason: str
    category: str


@dataclass(frozen=True)
class Blocked:
    question: str
    reason: str
    category: str


@dataclass(frozen=True)
class TypedFailure:
    question: str
    stage: str  # "gateway" | "parse" | "contract" | "semantic" | "citation" | "support"
    category: str
    message: str


AnswerResult = GroundedAnswer | InsufficientEvidence | Clarify | Blocked | TypedFailure


# ── Orchestration ─────────────────────────────────────────────────────

@dataclass
class GroundedAnswerService:
    """Construct with an already-built `ModelGateway` (Day 3 - the only
    model-call boundary this service ever uses), a `Retriever` (Day 2 -
    defaults to `BM25Retriever`, but tests inject a fake so no fixture run
    ever needs a real index), and a `PolicyEvaluator` (Day 5 - defaults to
    the real `evaluate_policy`; Day 6 Task 10 makes it swappable the same
    way gateway/retriever already are)."""

    gateway: ModelGateway
    retriever: Retriever = field(default_factory=BM25Retriever)
    policy_evaluator: PolicyEvaluator = evaluate_policy
    model_alias: str | None = None

    def answer(
        self,
        question: str,
        cancellation: CancellationToken | None = None,
        *,
        memory_context: MemoryContext | None = None,
    ) -> AnswerResult:
        # `cancellation` (Day 6 Task 5) is optional and defaults to None so
        # every existing Day 5 call site (positional `answer(question)`)
        # is unchanged. When given, it is threaded through to the Model
        # Gateway call below (step 5) - the one place in this pipeline
        # that does expensive, cancellable work - so an HTTP client
        # disconnect (app.py) reaches the Model Gateway path, not just the
        # HTTP handler (working rule).
        #
        # `memory_context` (Day 8 Task 7) is keyword-only and defaults to
        # None for the same reason: every existing call site - here and
        # in every Day 5/6/7 test - is unchanged. It is never used for
        # anything except building the prompt's SESSION MEMORY section
        # below (step 4) - it does not affect policy, retrieval, or
        # citation/support validation, all of which stay governed only by
        # the current question and the current turn's retrieved evidence
        # (Day 8's core rule: memory helps interpret the conversation,
        # retrieved evidence still determines what is true).
        with _tracer.start_as_current_span("policy") as span:
            # 1. Normalize (Task 5) - deterministic, bounded, runs before policy.
            normalized = normalize_input(question)

            # 2. Policy (Task 6) - allow / clarify / block, evaluated on the
            # normalized text so an obfuscated attack is caught the same way
            # as its plain form. `self.policy_evaluator` (Day 6 Task 10) -
            # defaults to the real `evaluate_policy`, injectable for tests.
            decision = self.policy_evaluator(normalized.normalized)
            span.set_attribute("policy.outcome", decision.outcome.value)
            span.set_attribute("policy.category", decision.category)
            if decision.outcome is PolicyOutcome.BLOCK:
                return Blocked(question=question, reason=decision.reason, category=decision.category)
            if decision.outcome is PolicyOutcome.CLARIFY:
                return Clarify(question=question, reason=decision.reason, category=decision.category)

        with _tracer.start_as_current_span("retrieval") as span:
            # 3. Retrieve (Day 2) - real evidence, never the full corpus.
            retrieved = self.retriever(question)
            span.set_attribute("retrieval.retrieved_count", len(retrieved))

        # 4. Build the explicitly-labelled prompt (Task 2; Day 8 Task 7
        # adds the optional SESSION MEMORY section). Local/in-process
        # string assembly, not worth a span of its own - it is not one of
        # the brief's named traced stages.
        prompt = build_prompt(question, retrieved, memory_context)

        with _tracer.start_as_current_span("model_gateway") as span:
            # 5. Model Gateway (Day 3) - the only model-call boundary.
            try:
                chat_result = self.gateway.chat(
                    prompt.to_chat_request(model_alias=self.model_alias, cancellation=cancellation)
                )
            except ModelGatewayError as exc:
                span.set_attribute("gateway.category", exc.category)
                span.set_status(Status(StatusCode.ERROR, exc.category))
                return TypedFailure(question=question, stage="gateway", category=exc.category, message=str(exc))
            span.set_attribute("gateway.model_alias", chat_result.metadata.model_alias)
            span.set_attribute("gateway.latency_ms", chat_result.metadata.latency_ms)
            span.set_attribute("gateway.retry_count", chat_result.metadata.retry_count)
            span.set_attribute("gateway.used_fallback", chat_result.metadata.used_fallback)
            if chat_result.metadata.token_usage:
                for token_type, count in chat_result.metadata.token_usage.items():
                    span.set_attribute(f"gateway.tokens.{token_type}", count)

        with _tracer.start_as_current_span("validation") as span:
            # 6. Day 4 typed contract validation - parse + schema, fails closed
            # on malformed JSON or a contract violation.
            parsed = parse_and_validate(chat_result.content, CitedAnswer)
            if isinstance(parsed, ValidationFailure):
                span.set_attribute("validation.result", "contract_failed")
                span.set_attribute("validation.category", parsed.category)
                span.set_status(Status(StatusCode.ERROR, parsed.category))
                return TypedFailure(
                    question=question, stage=parsed.stage, category=parsed.category, message=parsed.message
                )

            retrieved_ids = tuple(c.chunk_id for c in retrieved)

            if parsed.status is AnswerStatus.INSUFFICIENT_EVIDENCE:
                # Task 4 - explicit insufficient-evidence result. No citation
                # validation is owed to a status that admits it has none, but
                # a model that claims insufficiency while still citing is a
                # contract-abuse case we fail closed on rather than trust.
                if parsed.citations:
                    span.set_attribute("validation.result", "insufficient_evidence_with_citations")
                    span.set_status(Status(StatusCode.ERROR, "insufficient_evidence_with_citations"))
                    return TypedFailure(
                        question=question,
                        stage="contract",
                        category="insufficient_evidence_with_citations",
                        message="model returned status=insufficient_evidence but included citations",
                    )
                # Day 4 Task 3 semantic rule S2 ("insufficient_evidence must
                # not claim high confidence") - checked directly rather than
                # via the full `validate_semantic` here, because that
                # function's S5 also enforces the `INSUFFICIENT_EVIDENCE`
                # text-prefix lab convention (semantic.py's own docstring:
                # "how the Day 4 lab ties answer text to status ... that
                # belongs to Day 5"). Day 5's real `InsufficientEvidence`
                # carries the model's own free-text explanation (Task 4,
                # grounding_rules.md), which is exactly the real reasoning
                # that convention stands in for in Day 4's lab - not a
                # violation of it. S1/S4 are already excluded by the branch
                # we're in, so S2 is the only Day 4 semantic rule left to
                # apply here.
                if parsed.confidence_label is ConfidenceLabel.HIGH:
                    span.set_attribute("validation.result", "semantic_failed")
                    span.set_attribute("validation.category", "s2_insufficient_evidence_high_confidence")
                    span.set_status(Status(StatusCode.ERROR, "s2_insufficient_evidence_high_confidence"))
                    return TypedFailure(
                        question=question,
                        stage="semantic",
                        category="s2_insufficient_evidence_high_confidence",
                        message="status is 'insufficient_evidence' but confidence_label is 'high'",
                    )
                span.set_attribute("validation.result", "insufficient_evidence")
                return InsufficientEvidence(question=question, explanation=parsed.answer, retrieved_ids=retrieved_ids)

            # 6b. Symmetric guard on the other side of status (Task 4 / Day 4
            # semantic rule S1 - "an answered response needs at least one
            # citation"): the contract stage alone allows status="answered"
            # with zero citations (see models.py's CitedAnswer docstring and
            # fixture D04-09 - that separation is deliberate, Day 4 only
            # types the shape). Day 5 owns deciding groundedness, and an
            # "answered" claim anchored to nothing retrieved is exactly the
            # unsupported-answer case grounding_rules.md forbids ("a polished
            # answer that is unsupported by retrieved evidence is a failed
            # result") - fail closed here rather than let it become a
            # GroundedAnswer with an empty citation_ids tuple.
            if not parsed.citations:
                span.set_attribute("validation.result", "answered_without_citation")
                span.set_status(Status(StatusCode.ERROR, "answered_without_citation"))
                return TypedFailure(
                    question=question,
                    stage="contract",
                    category="answered_without_citation",
                    message="model returned status=answered but citations is empty - an unsupported claim is not a grounded answer",
                )

            # 6c. Day 4 Task 3 semantic validation (the complete S1-S5 path).
            # Reachable rules here are S3 ("citation chunk_ids must be unique")
            # and S5 (answer text / status prefix agreement) - S1 is already
            # excluded by the guard above. Without this call a model that
            # cites the same real chunk_id twice passes contract validation
            # (it is a well-typed CitedAnswer) and citation membership (every
            # cited id is genuinely retrieved) and would otherwise reach
            # GroundedAnswer with a duplicated citation - a real Day 4
            # semantic violation this pipeline must not silently accept.
            semantic_result = validate_semantic(parsed)
            if isinstance(semantic_result, ValidationFailure):
                span.set_attribute("validation.result", "semantic_failed")
                span.set_attribute("validation.category", semantic_result.category)
                span.set_status(Status(StatusCode.ERROR, semantic_result.category))
                return TypedFailure(
                    question=question,
                    stage=semantic_result.stage,
                    category=semantic_result.category,
                    message=semantic_result.message,
                )

            # 7. Citation validation (Task 3) - membership against the actual
            # retrieved context, fails the whole answer closed on any forged
            # citation rather than silently dropping it.
            cited_ids = [c.chunk_id for c in parsed.citations]
            citation_result = validate_citations(cited_ids, retrieved)
            span.set_attribute("validation.citation_count", len(cited_ids))
            span.set_attribute("validation.citation_valid", citation_result.valid)
            if not citation_result.valid:
                span.set_attribute("validation.result", "forged_citation")
                span.set_status(Status(StatusCode.ERROR, "forged_citation"))
                return TypedFailure(
                    question=question,
                    stage="citation",
                    category="forged_citation",
                    message=f"citation(s) not present in retrieved context: {list(citation_result.forged_citation_ids)}",
                )

            # 7b. Answer-support validation (post-review hardening,
            # aico.rag.support_validator) - citation membership alone
            # proves a cited chunk_id was genuinely retrieved, not that
            # the answer's claim is actually supported by that chunk's
            # content. A model can cite a real, retrieved chunk while
            # stating something the chunk never says (fabrication) or
            # something that exists only inside an attacker-injected
            # directive sentence within a poisoned chunk (poisoned-
            # document compliance). Fail closed rather than trust either.
            support_result = validate_support(parsed.answer, cited_ids, retrieved)
            span.set_attribute("validation.support_overlap_ratio", support_result.overlap_ratio)
            if not support_result.supported:
                span.set_attribute("validation.result", "unsupported_claim")
                span.set_status(Status(StatusCode.ERROR, "unsupported_claim"))
                return TypedFailure(
                    question=question,
                    stage="support",
                    category="unsupported_claim",
                    message=(
                        "answer content has insufficient lexical overlap with the non-suspicious text of "
                        f"its cited chunk(s) (overlap_ratio={support_result.overlap_ratio:.2f}); citing a "
                        "genuinely retrieved chunk_id does not by itself prove the claim is supported"
                    ),
                )
            span.set_attribute("validation.result", "valid")

        with _tracer.start_as_current_span("response_composition") as span:
            result = GroundedAnswer(
                question=question,
                answer=parsed.answer,
                citation_ids=tuple(cited_ids),
                confidence_label=parsed.confidence_label.value,
                retrieved_ids=retrieved_ids,
            )
            span.set_attribute("response.citation_count", len(result.citation_ids))
            span.set_attribute("response.confidence_label", result.confidence_label)
            return result
