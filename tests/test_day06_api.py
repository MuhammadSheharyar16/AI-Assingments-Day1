"""
Day 6 Task 1 — typed FastAPI service.

Proves:
- OpenAPI generates and documents `POST /ask` with its request/response
  models.
- A successful `/ask` call returns the public, typed `AskResponse`
  contract.
- The HTTP contract is a real mapping, not the internal `AnswerResult`
  dataclass leaking through unchecked (API/domain separation).

No network call, ever: `GroundedAnswerService` is built with a `FakeGateway`
(same duck-typed pattern as `tests/test_day05_grounding.py`) and a fake
retriever, injected through `app.dependency_overrides` (Task 10's seam),
so these tests never touch `config/model-routing.yaml`, Azure identity, or
the real `data/index` on disk.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from aico.api.app import app
from aico.api.dependencies import get_answer_service
from aico.platform.model_gateway import CallMetadata, ChatRequest, ChatResult
from aico.rag.answer_service import GroundedAnswerService
from aico.rag.citation_validator import EvidenceChunk


class FakeGateway:
    """Duck-typed `ModelGateway` stand-in - `GroundedAnswerService` only
    ever calls `.chat(request)`."""

    def __init__(self, respond):
        self._respond = respond
        self.calls: list[ChatRequest] = []

    def chat(self, request: ChatRequest) -> ChatResult:
        self.calls.append(request)
        content = self._respond(request) if callable(self._respond) else self._respond
        return ChatResult(
            content=content,
            metadata=CallMetadata(
                operation="chat",
                model_alias="fake-chat",
                latency_ms=1.0,
                retry_count=0,
                token_usage={"prompt_tokens": 10, "completion_tokens": 5},
                budget_status="within_budget",
            ),
        )


def _fake_retriever(question: str) -> list[EvidenceChunk]:
    return [
        EvidenceChunk(chunk_id="DOC-003::chunk-0", source_file="DOC-003-pricing-payment.md", text="Payment is net 30."),
    ]


_ANSWERED_JSON = """
{
  "schema_version": "1.0",
  "status": "answered",
  "answer": "Payment terms are net 30 days from invoice date.",
  "citations": [{"chunk_id": "DOC-003::chunk-0", "source_file": "DOC-003-pricing-payment.md"}],
  "confidence_label": "high"
}
"""


def _build_service(respond=_ANSWERED_JSON) -> GroundedAnswerService:
    return GroundedAnswerService(gateway=FakeGateway(respond), retriever=_fake_retriever)


def _client_with_service(service: GroundedAnswerService) -> TestClient:
    app.dependency_overrides[get_answer_service] = lambda: service
    return TestClient(app)


def teardown_function() -> None:
    app.dependency_overrides.clear()


# ── OpenAPI generation ───────────────────────────────────────────────────


def test_openapi_generates_and_documents_ask():
    client = TestClient(app)
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()

    assert "/ask" in schema["paths"]
    assert "post" in schema["paths"]["/ask"]

    request_body_ref = schema["paths"]["/ask"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert "AskRequest" in request_body_ref

    response_ref = schema["paths"]["/ask"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert "AskResponse" in response_ref

    assert "AskRequest" in schema["components"]["schemas"]
    assert "AskResponse" in schema["components"]["schemas"]


# ── Typed /ask success ───────────────────────────────────────────────────


def test_ask_success_returns_typed_response():
    client = _client_with_service(_build_service())

    resp = client.post("/ask", json={"question": "What payment terms are stated in the supplier policy?"})

    assert resp.status_code == 200
    body = resp.json()

    assert body["status"] == "answered"
    assert body["answer"] == "Payment terms are net 30 days from invoice date."
    assert body["citations"] == [{"chunk_id": "DOC-003::chunk-0", "source_file": None}]
    assert body["confidence_label"] == "high"
    # Task 1 minimum: request/correlation metadata is present on every response.
    assert body["request_id"]
    assert body["correlation_id"]
    assert body["request_id"] != body["correlation_id"]


def test_ask_insufficient_evidence_maps_to_typed_status():
    insufficient_json = """
    {
      "schema_version": "1.0",
      "status": "insufficient_evidence",
      "answer": "The retrieved evidence does not state a delivery window.",
      "citations": [],
      "confidence_label": "low"
    }
    """
    client = _client_with_service(_build_service(insufficient_json))

    resp = client.post("/ask", json={"question": "What is the delivery window?"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "insufficient_evidence"
    assert body["citations"] == []


# ── API / domain separation ──────────────────────────────────────────────


def test_response_contract_is_not_the_internal_domain_object():
    """The public AskResponse shape must not simply be the internal
    GroundedAnswer dataclass reflected back - it has its own field set
    (request_id/correlation_id/status/category/message) that
    GroundedAnswer never carries, proving app.py maps rather than passes
    the dataclass straight through."""
    from aico.api.contracts import AskResponse
    from aico.rag.answer_service import GroundedAnswer

    domain_fields = set(GroundedAnswer.__dataclass_fields__)
    api_fields = set(AskResponse.model_fields)

    assert "citation_ids" in domain_fields
    assert "citation_ids" not in api_fields  # renamed/reshaped as `citations`, not exposed verbatim
    assert {"request_id", "correlation_id", "status"} <= api_fields
    assert not api_fields <= domain_fields  # API contract is strictly its own shape


def test_invalid_request_body_is_rejected():
    client = _client_with_service(_build_service())

    resp = client.post("/ask", json={})

    assert 400 <= resp.status_code < 500
