"""
Day 6 Task 10 — dependency injection.

Proves each item on the assignment's "at least" list is independently
replaceable with a deterministic fake, without editing `app.py`/`health.py`:

    - RAG/answer service       `get_answer_service`
    - gateway                  `get_gateway`             (a sub-dependency
    - retriever                `get_retriever`             of
    - input/policy component   `get_policy_evaluator`      `get_answer_service`)
    - dependency-health checks `get_retrieval_health_check` /
                                `get_model_gateway_health_check`

The gateway/retriever/policy tests below deliberately do NOT override
`get_answer_service` - they override only the one sub-dependency under
test (dependencies.py wires `get_gateway`/`get_retriever`/
`get_policy_evaluator` as `Depends(...)` parameters of
`get_answer_service` itself) and let `get_answer_service` assemble the
rest for real. That proves the seam is load-bearing in the actual FastAPI
dependency graph, not merely swappable in principle.

No network call, no real `data/index`/`config/model-routing.yaml` read
anywhere in this file - every test overrides enough of the graph to avoid
touching either.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from aico.api.app import app
from aico.api.dependencies import (
    get_answer_service,
    get_gateway,
    get_model_gateway_health_check,
    get_policy_evaluator,
    get_retrieval_health_check,
    get_retriever,
)
from aico.api.health import DependencyCheckResult, DependencyStatus
from aico.api.identity import TrustedIdentity, get_trusted_identity
from aico.platform.model_gateway import CallMetadata, ChatRequest, ChatResult
from aico.rag.answer_service import GroundedAnswerService
from aico.rag.citation_validator import EvidenceChunk
from aico.security.input_policy import PolicyDecision, PolicyOutcome

_VALID_IDENTITY = TrustedIdentity(tenant_id="TENANT-SYN-001", user_id="USER-SYN-001")

_ANSWERED_JSON = """
{
  "schema_version": "1.0",
  "status": "answered",
  "answer": "Payment terms are net 30 days.",
  "citations": [{"chunk_id": "fake-chunk-1", "source_file": "fake.md"}],
  "confidence_label": "high"
}
"""


class FakeGateway:
    def __init__(self):
        self.call_count = 0

    def chat(self, request: ChatRequest) -> ChatResult:
        self.call_count += 1
        return ChatResult(
            content=_ANSWERED_JSON,
            metadata=CallMetadata(
                operation="chat",
                model_alias="fake-chat",
                latency_ms=1.0,
                retry_count=0,
                token_usage=None,
                budget_status="unknown",
            ),
        )


def _fake_retriever(question: str) -> list[EvidenceChunk]:
    return [EvidenceChunk(chunk_id="fake-chunk-1", source_file="fake.md", text="Payment terms are net 30 days.")]


def teardown_function() -> None:
    app.dependency_overrides.clear()


def _client_with_identity() -> TestClient:
    app.dependency_overrides[get_trusted_identity] = lambda: _VALID_IDENTITY
    return TestClient(app)


_QUESTION = {"question": "What payment terms are stated in the supplier policy?"}


# ── 1. RAG/answer service - replaceable as a whole ───────────────────────


def test_answer_service_is_replaceable_as_a_whole():
    app.dependency_overrides[get_answer_service] = lambda: GroundedAnswerService(
        gateway=FakeGateway(), retriever=_fake_retriever
    )
    client = _client_with_identity()

    resp = client.post("/ask", json=_QUESTION)

    assert resp.status_code == 200
    assert resp.json()["status"] == "answered"


# ── 2 & 3. gateway / retriever - independently replaceable ──────────────


def test_gateway_alone_is_replaceable_without_overriding_answer_service():
    fake_gateway = FakeGateway()
    app.dependency_overrides[get_gateway] = lambda: fake_gateway
    app.dependency_overrides[get_retriever] = lambda: _fake_retriever  # avoid the real data/index
    client = _client_with_identity()

    resp = client.post("/ask", json=_QUESTION)

    assert resp.status_code == 200
    assert resp.json()["status"] == "answered"
    assert fake_gateway.call_count == 1
    assert get_answer_service not in app.dependency_overrides  # the real assembly logic ran


def test_retriever_alone_is_replaceable_without_overriding_answer_service():
    app.dependency_overrides[get_gateway] = lambda: FakeGateway()
    app.dependency_overrides[get_retriever] = lambda: _fake_retriever
    client = _client_with_identity()

    resp = client.post("/ask", json=_QUESTION)

    assert resp.status_code == 200
    body = resp.json()
    # The fake retriever's distinctive chunk_id shows up in the response,
    # proving it - not BM25Retriever over the real index - actually ran.
    assert body["citations"][0]["chunk_id"] == "fake-chunk-1"
    assert get_answer_service not in app.dependency_overrides


# ── 4. input/policy component - independently replaceable ───────────────


def test_policy_evaluator_alone_is_replaceable_and_short_circuits_before_the_gateway():
    fake_gateway = FakeGateway()
    app.dependency_overrides[get_gateway] = lambda: fake_gateway
    app.dependency_overrides[get_retriever] = lambda: _fake_retriever

    def _always_block(_normalized_text: str) -> PolicyDecision:
        return PolicyDecision(PolicyOutcome.BLOCK, "forced_by_test", "forced block for Task 10's DI proof")

    app.dependency_overrides[get_policy_evaluator] = lambda: _always_block
    client = _client_with_identity()

    # An otherwise entirely benign question - it is blocked only because
    # the fake policy evaluator forces it, not because of its content.
    resp = client.post("/ask", json={"question": "What are your business hours?"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "blocked"
    assert body["category"] == "forced_by_test"
    assert fake_gateway.call_count == 0  # the pipeline never reached the gateway
    assert get_answer_service not in app.dependency_overrides


# ── 5. dependency-health checks - independently replaceable ─────────────


def test_dependency_health_checks_are_independently_replaceable():
    app.dependency_overrides[get_retrieval_health_check] = lambda: (
        lambda: DependencyCheckResult(status=DependencyStatus.UNAVAILABLE, detail="forced unavailable by test")
    )
    app.dependency_overrides[get_model_gateway_health_check] = lambda: (
        lambda: DependencyCheckResult(status=DependencyStatus.HEALTHY, detail="forced healthy by test")
    )
    client = TestClient(app)

    resp = client.get("/health/dependencies")

    assert resp.status_code == 200
    reports = {d["name"]: d["status"] for d in resp.json()["dependencies"]}
    assert reports["retrieval"] == "unavailable"
    assert reports["model_gateway"] == "healthy"
