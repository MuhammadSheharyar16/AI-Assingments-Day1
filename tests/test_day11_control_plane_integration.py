"""
Day 11 Task 13 -- integrating Gate-C into the RAG flow
(`ControlPlaneAnswerService`, `src/aico/rag/control_plane_answer_service.py`).

Proves the full required order end to end, against the real committed
ontology registry, Gate-B policy, source registry and Gate-C policy, a
real `GateA`/`LaneSelector`/`GateB`/`GateC`, and a `GroundedAnswerService`
wired to fakes (same `_CountingGateway`/`_CountingRetriever` pattern
`test_day09/10_control_plane_integration.py` already use):

    trusted identity -> session -> input policy -> Gate-A -> lane selector
    -> Gate-B -> retrieval -> Gate-C -> Model Gateway -> typed contract
    validation -> semantic validation -> citation validation

  - Gate-C `reject`/`insufficient_evidence`/`clarify` -> zero Model
    Gateway calls, a typed `GateCRejected`/`GateCInsufficientEvidence`/
    `GateCClarify` -- retrieval still runs (it must, for Gate-C to have
    anything to evaluate), but generation never does.
  - Gate-C `allow` -> the Model Gateway IS reached, exactly once, and the
    prompt it receives contains only the Gate-C-validated chunk's text --
    a chunk Gate-C rejected (mixed into the same retrieved batch) never
    appears in it.
  - Citation validation still runs, unmodified, over the Gate-C-narrowed
    evidence -- a forged citation still fails exactly as it always has
    ("do not let Gate-C replace post-generation citation validation").
  - Day 5's own `InsufficientEvidence` (the model declining to answer)
    and Gate-C's `GateCInsufficientEvidence` (evidence never reached
    generation at all) are distinct, never conflated.
  - A service built without `source_registry`/`gate_c_policy_registry`/
    `evidence_adapter` behaves exactly as Day 9/10 left the `rag` lane --
    a direct side-by-side confirmation, the same way
    `test_day10_control_plane_integration.py` adds one for Gate-B over
    Day 9.
  - Partial Gate-C configuration, and Gate-C configured without Gate-B,
    both raise `GateCIntegrationError` at construction time rather than
    failing confusingly mid-request.
  - `mode_b`/`clarify`/`block`/`safe_fast_path` are entirely unaffected by
    a Gate-C-configured service -- Gate-C only ever runs inside the `rag`
    branch.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from aico.api.identity import TrustedIdentity
from aico.control.gate_b import GateBRequest
from aico.control.models import LaneDecision
from aico.control.ontology_registry import OntologyRegistry
from aico.control.policy_models import DataClassification
from aico.control.policy_registry import PolicyRegistry
from aico.evidence.models import EvidencePackage
from aico.evidence.policy import GateCPolicyRegistry
from aico.evidence.provenance import stable_content_hash
from aico.evidence.source_registry import SourceRegistry
from aico.platform.model_gateway import CallMetadata, ChatRequest, ChatResult
from aico.rag.answer_service import GroundedAnswer, GroundedAnswerService, InsufficientEvidence, TypedFailure
from aico.rag.citation_validator import EvidenceChunk
from aico.rag.control_plane_answer_service import (
    ControlPlaneAnswerService,
    EvidenceAdapter,
    GateCClarify,
    GateCInsufficientEvidence,
    GateCIntegrationError,
    GateCRejected,
    ModeBSelected,
)

# ---------------------------------------------------------------------------
# Real-shaped counting fakes (same pattern test_day09/10's own integration
# files use)
# ---------------------------------------------------------------------------


def _never_called_retriever(query: str) -> list[EvidenceChunk]:  # pragma: no cover - only reached on failure
    raise AssertionError(f"retrieval must not be called (query: {query!r})")


class _NeverCalledGateway:
    def chat(self, request: ChatRequest) -> ChatResult:  # pragma: no cover - only reached on failure
        raise AssertionError(f"Model Gateway must not be called (request: {request!r})")


@dataclass
class _CountingGateway:
    response_content: str
    calls: list[ChatRequest] = field(default_factory=list)

    def chat(self, request: ChatRequest) -> ChatResult:
        self.calls.append(request)
        return ChatResult(
            content=self.response_content,
            metadata=CallMetadata(
                operation="chat", model_alias="fake-chat-alias", latency_ms=1.0, retry_count=0,
                token_usage=None, budget_status="within_budget",
            ),
        )


@dataclass
class _CountingRetriever:
    chunks: list[EvidenceChunk] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    def __call__(self, query: str) -> list[EvidenceChunk]:
        self.calls.append(query)
        return self.chunks


def _answered_json(*, answer: str, citations: list[dict]) -> str:
    import json

    return json.dumps(
        {
            "schema_version": "1.0",
            "status": "answered",
            "answer": answer,
            "citations": citations,
            "confidence_label": "high",
        }
    )


_INSUFFICIENT_JSON = (
    '{"schema_version": "1.0", "status": "insufficient_evidence", '
    '"answer": "The retrieved evidence does not state the payment terms.", "citations": [], '
    '"confidence_label": "low"}'
)


# ---------------------------------------------------------------------------
# Evidence adapter: maps a real EvidenceChunk into Gate-C's governed shape.
# `chunk.source_file` carries the governed `source_id` (test convention --
# see `control_plane_answer_service.py`'s own docstring on why no real
# adapter ships: a real EvidenceChunk has no governed provenance metadata
# of its own).
# ---------------------------------------------------------------------------


def _make_evidence_adapter(
    *,
    facets_by_chunk_id: dict[str, list[str]],
    required_facets: list[str],
    request_kind: str | None,
    claims_by_chunk_id: dict[str, dict[str, str]] | None = None,
    as_of: str = "2026-09-11T12:00:00+05:00",
    source_updated_at: str = "2026-09-05T09:00:00+05:00",
    tenant_id: str = "TENANT-A",
    data_classification: str = "internal",
) -> EvidenceAdapter:
    claims_by_chunk_id = claims_by_chunk_id or {}

    def _adapter(question: str, lane_decision: LaneDecision, retrieved: list[EvidenceChunk]) -> tuple[EvidencePackage, str | None]:
        items = [
            {
                "evidence_id": chunk.chunk_id,
                "chunk_id": chunk.chunk_id,
                "source_id": chunk.source_file,
                "source_version": "3",
                "source_updated_at": source_updated_at,
                "retrieved_at": as_of,
                "content_hash": stable_content_hash(chunk.text),
                "tenant_id": tenant_id,
                "data_classification": data_classification,
                "evidence_facets": facets_by_chunk_id.get(chunk.chunk_id, []),
                "content": chunk.text,
                "claims": claims_by_chunk_id.get(chunk.chunk_id, {}),
            }
            for chunk in retrieved
        ]
        package = EvidencePackage.model_validate(
            {
                "request_id": "REQ-TEST",
                "intent_id": lane_decision.intent_id,
                "lane": "rag",
                "as_of": as_of,
                "required_facets": required_facets,
                "items": items,
            }
        )
        return package, request_kind

    return _adapter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_registry() -> OntologyRegistry:
    return OntologyRegistry.load()


@pytest.fixture(scope="module")
def real_policy_registry(real_registry: OntologyRegistry) -> PolicyRegistry:
    return PolicyRegistry.load(ontology_registry=real_registry)


@pytest.fixture(scope="module")
def real_source_registry(real_registry: OntologyRegistry) -> SourceRegistry:
    return SourceRegistry.load(ontology_registry=real_registry)


@pytest.fixture(scope="module")
def real_gate_c_policy_registry(real_registry: OntologyRegistry, real_source_registry: SourceRegistry) -> GateCPolicyRegistry:
    return GateCPolicyRegistry.load(ontology_registry=real_registry, source_registry=real_source_registry)


_SUPPLIER_READER = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("supplier_reader",))
_QUESTION = "What are the payment terms?"
_REQUESTED = GateBRequest(data_class=DataClassification.INTERNAL)

_GOOD_CHUNK = EvidenceChunk(
    chunk_id="C1", source_file="SRC-POLICY-A", text="Synthetic Supplier Alpha uses net 30 payment terms."
)
_UNKNOWN_SOURCE_CHUNK = EvidenceChunk(
    chunk_id="C2", source_file="SRC-UNKNOWN", text="REJECTED_SENTINEL_TEXT_MUST_NOT_REACH_PROMPT"
)


def _service(
    *,
    real_registry,
    real_policy_registry,
    real_source_registry,
    real_gate_c_policy_registry,
    gateway,
    retriever,
    evidence_adapter,
) -> ControlPlaneAnswerService:
    rag_service = GroundedAnswerService(gateway=gateway, retriever=retriever)
    return ControlPlaneAnswerService(
        registry=real_registry,
        rag_service=rag_service,
        policy_registry=real_policy_registry,
        source_registry=real_source_registry,
        gate_c_policy_registry=real_gate_c_policy_registry,
        evidence_adapter=evidence_adapter,
    )


# ---------------------------------------------------------------------------
# Gate-C allow -> generation reached exactly once, over validated evidence
# ---------------------------------------------------------------------------


def test_gate_c_allow_reaches_generation_with_validated_evidence_only(
    real_registry, real_policy_registry, real_source_registry, real_gate_c_policy_registry
):
    gateway = _CountingGateway(_answered_json(answer="Net 30.", citations=[{"chunk_id": "C1", "source_file": "SRC-POLICY-A"}]))
    retriever = _CountingRetriever(chunks=[_GOOD_CHUNK])
    adapter = _make_evidence_adapter(
        facets_by_chunk_id={"C1": ["supplier_identity", "payment_terms"]},
        required_facets=["supplier_identity", "payment_terms"],
        request_kind="payment_terms_only",
    )
    service = _service(
        real_registry=real_registry, real_policy_registry=real_policy_registry, real_source_registry=real_source_registry,
        real_gate_c_policy_registry=real_gate_c_policy_registry, gateway=gateway, retriever=retriever, evidence_adapter=adapter,
    )

    result = service.answer(_QUESTION, identity=_SUPPLIER_READER, requested=_REQUESTED)

    assert isinstance(result, GroundedAnswer)
    assert result.answer == "Net 30."
    assert len(gateway.calls) == 1
    assert len(retriever.calls) == 1
    prompt_text = "\n".join(m.content for m in gateway.calls[0].messages)
    assert "net 30 payment terms" in prompt_text


def test_rejected_chunk_content_never_reaches_the_prompt(
    real_registry, real_policy_registry, real_source_registry, real_gate_c_policy_registry
):
    """A mixed retrieval batch -- one governed chunk, one from an
    unregistered source -- still allows (using only the good one), and the
    prompt actually sent to the Model Gateway must not contain the
    rejected chunk's distinctive text."""
    gateway = _CountingGateway(_answered_json(answer="Net 30.", citations=[{"chunk_id": "C1", "source_file": "SRC-POLICY-A"}]))
    retriever = _CountingRetriever(chunks=[_GOOD_CHUNK, _UNKNOWN_SOURCE_CHUNK])
    adapter = _make_evidence_adapter(
        facets_by_chunk_id={"C1": ["supplier_identity", "payment_terms"], "C2": ["payment_terms"]},
        required_facets=["supplier_identity", "payment_terms"],
        request_kind="payment_terms_only",
    )
    service = _service(
        real_registry=real_registry, real_policy_registry=real_policy_registry, real_source_registry=real_source_registry,
        real_gate_c_policy_registry=real_gate_c_policy_registry, gateway=gateway, retriever=retriever, evidence_adapter=adapter,
    )

    result = service.answer(_QUESTION, identity=_SUPPLIER_READER, requested=_REQUESTED)

    assert isinstance(result, GroundedAnswer)
    assert len(gateway.calls) == 1
    prompt_text = "\n".join(m.content for m in gateway.calls[0].messages)
    assert "net 30 payment terms" in prompt_text
    assert "REJECTED_SENTINEL_TEXT_MUST_NOT_REACH_PROMPT" not in prompt_text


# ---------------------------------------------------------------------------
# Gate-C reject/insufficient_evidence/clarify -> zero Model Gateway calls
# (retrieval still runs -- Gate-C needs something to evaluate)
# ---------------------------------------------------------------------------


def test_gate_c_reject_unknown_source_zero_model_calls(
    real_registry, real_policy_registry, real_source_registry, real_gate_c_policy_registry
):
    gateway = _CountingGateway("should never be read")
    retriever = _CountingRetriever(chunks=[_UNKNOWN_SOURCE_CHUNK])
    adapter = _make_evidence_adapter(
        facets_by_chunk_id={"C2": ["payment_terms"]},
        required_facets=["payment_terms"],
        request_kind="payment_terms_only",
    )
    service = _service(
        real_registry=real_registry, real_policy_registry=real_policy_registry, real_source_registry=real_source_registry,
        real_gate_c_policy_registry=real_gate_c_policy_registry, gateway=gateway, retriever=retriever, evidence_adapter=adapter,
    )

    result = service.answer(_QUESTION, identity=_SUPPLIER_READER, requested=_REQUESTED)

    assert isinstance(result, GateCRejected)
    assert "unknown_source" in result.reason_codes
    assert gateway.calls == []
    assert len(retriever.calls) == 1  # retrieval DID run -- Gate-C needed something to evaluate


def test_gate_c_insufficient_evidence_zero_model_calls(
    real_registry, real_policy_registry, real_source_registry, real_gate_c_policy_registry
):
    gateway = _CountingGateway("should never be read")
    retriever = _CountingRetriever(chunks=[_GOOD_CHUNK])
    adapter = _make_evidence_adapter(
        facets_by_chunk_id={"C1": ["supplier_identity"]},  # missing payment_terms
        required_facets=["supplier_identity", "payment_terms"],
        request_kind="payment_terms_only",
    )
    service = _service(
        real_registry=real_registry, real_policy_registry=real_policy_registry, real_source_registry=real_source_registry,
        real_gate_c_policy_registry=real_gate_c_policy_registry, gateway=gateway, retriever=retriever, evidence_adapter=adapter,
    )

    result = service.answer(_QUESTION, identity=_SUPPLIER_READER, requested=_REQUESTED)

    assert isinstance(result, GateCInsufficientEvidence)
    assert result.missing_facets == ("payment_terms",)
    assert gateway.calls == []


def test_gate_c_clarify_when_request_kind_unresolved_zero_model_calls(
    real_registry, real_policy_registry, real_source_registry, real_gate_c_policy_registry
):
    gateway = _CountingGateway("should never be read")
    retriever = _CountingRetriever(chunks=[_GOOD_CHUNK])
    adapter = _make_evidence_adapter(
        facets_by_chunk_id={"C1": ["supplier_identity", "payment_terms"]},
        required_facets=[],
        request_kind=None,  # the adapter could not determine which governed rule applies
    )
    service = _service(
        real_registry=real_registry, real_policy_registry=real_policy_registry, real_source_registry=real_source_registry,
        real_gate_c_policy_registry=real_gate_c_policy_registry, gateway=gateway, retriever=retriever, evidence_adapter=adapter,
    )

    result = service.answer(_QUESTION, identity=_SUPPLIER_READER, requested=_REQUESTED)

    assert isinstance(result, GateCClarify)
    assert gateway.calls == []


# ---------------------------------------------------------------------------
# Citation validation still runs, unmodified, over the Gate-C-narrowed set
# ---------------------------------------------------------------------------


def test_forged_citation_still_fails_after_gate_c_allows(
    real_registry, real_policy_registry, real_source_registry, real_gate_c_policy_registry
):
    """"Do not let Gate-C replace post-generation citation validation":
    Gate-C allows the real evidence, but the model cites a chunk_id that
    was never in the (Gate-C-narrowed) retrieved context at all -- Day 5's
    own citation validation still catches this."""
    gateway = _CountingGateway(_answered_json(answer="Net 30.", citations=[{"chunk_id": "FORGED-ID", "source_file": "nowhere.md"}]))
    retriever = _CountingRetriever(chunks=[_GOOD_CHUNK])
    adapter = _make_evidence_adapter(
        facets_by_chunk_id={"C1": ["supplier_identity", "payment_terms"]},
        required_facets=["supplier_identity", "payment_terms"],
        request_kind="payment_terms_only",
    )
    service = _service(
        real_registry=real_registry, real_policy_registry=real_policy_registry, real_source_registry=real_source_registry,
        real_gate_c_policy_registry=real_gate_c_policy_registry, gateway=gateway, retriever=retriever, evidence_adapter=adapter,
    )

    result = service.answer(_QUESTION, identity=_SUPPLIER_READER, requested=_REQUESTED)

    assert isinstance(result, TypedFailure)
    assert result.stage == "citation"
    assert result.category == "forged_citation"


def test_a_citation_naming_a_gate_c_rejected_chunk_also_fails(
    real_registry, real_policy_registry, real_source_registry, real_gate_c_policy_registry
):
    """A citation naming the *rejected* chunk (C2, from an unregistered
    source) fails exactly as a forged one -- it is not in `filtered_chunks`
    either way."""
    gateway = _CountingGateway(_answered_json(answer="Net 30.", citations=[{"chunk_id": "C2", "source_file": "SRC-UNKNOWN"}]))
    retriever = _CountingRetriever(chunks=[_GOOD_CHUNK, _UNKNOWN_SOURCE_CHUNK])
    adapter = _make_evidence_adapter(
        facets_by_chunk_id={"C1": ["supplier_identity", "payment_terms"], "C2": ["payment_terms"]},
        required_facets=["supplier_identity", "payment_terms"],
        request_kind="payment_terms_only",
    )
    service = _service(
        real_registry=real_registry, real_policy_registry=real_policy_registry, real_source_registry=real_source_registry,
        real_gate_c_policy_registry=real_gate_c_policy_registry, gateway=gateway, retriever=retriever, evidence_adapter=adapter,
    )

    result = service.answer(_QUESTION, identity=_SUPPLIER_READER, requested=_REQUESTED)

    assert isinstance(result, TypedFailure)
    assert result.stage == "citation"
    assert result.category == "forged_citation"


# ---------------------------------------------------------------------------
# Day 5's own InsufficientEvidence vs. Gate-C's GateCInsufficientEvidence
# ---------------------------------------------------------------------------


def test_model_declined_answer_is_day5_insufficient_evidence_not_gate_c(
    real_registry, real_policy_registry, real_source_registry, real_gate_c_policy_registry
):
    """Gate-C already allowed the evidence -- the model itself then
    declines to answer from it. Distinct typed result from Gate-C's own
    `GateCInsufficientEvidence` (which fires *before* generation)."""
    gateway = _CountingGateway(_INSUFFICIENT_JSON)
    retriever = _CountingRetriever(chunks=[_GOOD_CHUNK])
    adapter = _make_evidence_adapter(
        facets_by_chunk_id={"C1": ["supplier_identity", "payment_terms"]},
        required_facets=["supplier_identity", "payment_terms"],
        request_kind="payment_terms_only",
    )
    service = _service(
        real_registry=real_registry, real_policy_registry=real_policy_registry, real_source_registry=real_source_registry,
        real_gate_c_policy_registry=real_gate_c_policy_registry, gateway=gateway, retriever=retriever, evidence_adapter=adapter,
    )

    result = service.answer(_QUESTION, identity=_SUPPLIER_READER, requested=_REQUESTED)

    assert isinstance(result, InsufficientEvidence)
    assert not isinstance(result, GateCInsufficientEvidence)
    assert len(gateway.calls) == 1  # generation DID happen -- Gate-C allowed it


# ---------------------------------------------------------------------------
# Inactive by default: preserves Day 9/10 behavior exactly
# ---------------------------------------------------------------------------


def test_gate_c_inactive_by_default_preserves_day9_day10_rag_behavior(real_registry, real_policy_registry):
    gateway = _CountingGateway(_answered_json(answer="Net 30.", citations=[{"chunk_id": "C1", "source_file": "DOC-001.md"}]))
    retriever = _CountingRetriever(chunks=[EvidenceChunk(chunk_id="C1", source_file="DOC-001.md", text="Payment terms are net 30 days.")])
    rag_service = GroundedAnswerService(gateway=gateway, retriever=retriever)
    service = ControlPlaneAnswerService(registry=real_registry, rag_service=rag_service, policy_registry=real_policy_registry)

    result = service.answer(_QUESTION, identity=_SUPPLIER_READER, requested=_REQUESTED)

    assert isinstance(result, GroundedAnswer)
    assert not isinstance(result, (GateCRejected, GateCInsufficientEvidence, GateCClarify))
    assert len(gateway.calls) == 1
    assert len(retriever.calls) == 1
    assert service.gate_c is None


# ---------------------------------------------------------------------------
# Misconfiguration fails loudly at construction time
# ---------------------------------------------------------------------------


def test_partial_gate_c_configuration_raises(real_registry, real_policy_registry, real_source_registry):
    rag_service = GroundedAnswerService(gateway=_NeverCalledGateway(), retriever=_never_called_retriever)
    with pytest.raises(GateCIntegrationError):
        ControlPlaneAnswerService(
            registry=real_registry,
            rag_service=rag_service,
            policy_registry=real_policy_registry,
            source_registry=real_source_registry,  # gate_c_policy_registry/evidence_adapter missing
        )


def test_gate_c_without_gate_b_raises(real_registry, real_source_registry, real_gate_c_policy_registry):
    rag_service = GroundedAnswerService(gateway=_NeverCalledGateway(), retriever=_never_called_retriever)
    adapter = _make_evidence_adapter(facets_by_chunk_id={}, required_facets=[], request_kind="payment_terms_only")
    with pytest.raises(GateCIntegrationError):
        ControlPlaneAnswerService(
            registry=real_registry,
            rag_service=rag_service,
            source_registry=real_source_registry,
            gate_c_policy_registry=real_gate_c_policy_registry,
            evidence_adapter=adapter,
            # policy_registry (Gate-B) deliberately omitted
        )


# ---------------------------------------------------------------------------
# Non-rag lanes are entirely unaffected by a Gate-C-configured service
# ---------------------------------------------------------------------------


def test_mode_b_lane_unaffected_by_gate_c_configuration(
    real_registry, real_policy_registry, real_source_registry, real_gate_c_policy_registry
):
    rag_service = GroundedAnswerService(gateway=_NeverCalledGateway(), retriever=_never_called_retriever)
    adapter = _make_evidence_adapter(facets_by_chunk_id={}, required_facets=[], request_kind="payment_terms_only")
    service = ControlPlaneAnswerService(
        registry=real_registry,
        rag_service=rag_service,
        policy_registry=real_policy_registry,
        source_registry=real_source_registry,
        gate_c_policy_registry=real_gate_c_policy_registry,
        evidence_adapter=adapter,
    )
    sourcing_analyst = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-2", roles=("sourcing_analyst",))

    result = service.answer(
        "List active contracts.", identity=sourcing_analyst, requested=GateBRequest(data_class=DataClassification.INTERNAL)
    )

    assert isinstance(result, ModeBSelected)
