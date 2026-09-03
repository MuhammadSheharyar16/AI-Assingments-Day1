"""
Day 5 Task 1 — grounded answer service.

The orchestration that wires every earlier piece into the one path
described in the assignment brief (build_outcome diagram):

    user question -> normalize -> policy -> retrieve -> build prompt
                   -> Model Gateway -> Day 4 typed contract validation
                   -> citation validation -> final result

`GroundedAnswerService.answer()` is the single entry point. It never
bypasses retrieval, the Model Gateway, Day 4 typed validation or citation
validation (working rules) - each stage below returns early only with one
of the five typed result values, and every one of those returns happens
*after* the stage responsible for it has actually run.

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
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Callable, Protocol, Union

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from aico.contracts.errors import ValidationFailure
from aico.contracts.models import AnswerStatus, CitedAnswer
from aico.contracts.validator import parse_and_validate
from aico.platform.errors import ModelGatewayError
from aico.platform.model_gateway import CancellationToken, ModelGateway
from aico.rag.citation_validator import EvidenceChunk, validate_citations
from aico.rag.prompt_builder import build_prompt
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
    stage: str  # "gateway" | "parse" | "contract" | "citation"
    category: str
    message: str


AnswerResult = Union[GroundedAnswer, InsufficientEvidence, Clarify, Blocked, TypedFailure]


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

    def answer(self, question: str, cancellation: CancellationToken | None = None) -> AnswerResult:
        # `cancellation` (Day 6 Task 5) is optional and defaults to None so
        # every existing Day 5 call site (positional `answer(question)`)
        # is unchanged. When given, it is threaded through to the Model
        # Gateway call below (step 5) - the one place in this pipeline
        # that does expensive, cancellable work - so an HTTP client
        # disconnect (app.py) reaches the Model Gateway path, not just the
        # HTTP handler (working rule).
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

        # 4. Build the explicitly-labelled prompt (Task 2). Local/in-process
        # string assembly, not worth a span of its own - it is not one of
        # the brief's named traced stages.
        prompt = build_prompt(question, retrieved)

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
