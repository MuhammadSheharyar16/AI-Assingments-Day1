"""
Day 6 Task 3 — request and correlation IDs.

Proves:
- Missing request_id/correlation_id are generated (API-005 in
  `tests/fixtures/day06/api_cases.json`).
- A caller-supplied `X-Request-ID`/`X-Correlation-ID` is honored (echoed
  back), not overwritten - and a blank header is treated the same as an
  absent one.
- Both IDs are returned to the caller via *both* documented surfaces: the
  `AskResponse` body (Task 1) and response headers (Task 3) - and body/
  header always agree, because `get_request_context` (correlation.py)
  reads the one decision `CorrelationMiddleware` already made rather than
  computing its own.
- The correlation_id set by the middleware is the same one visible deep
  inside request handling via `current_correlation_id()` - the mechanism
  Tasks 7-9 (logs/metrics/spans) will read from - proving propagation,
  not just generation.
- An error response (identity rejection, Task 2) still carries
  request_id/correlation_id - a rejected request is still traceable.

No network call: same `FakeGateway`/fake-retriever pattern as
`test_day06_api.py`.
"""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from aico.api.app import app
from aico.api.correlation import CORRELATION_ID_HEADER, REQUEST_ID_HEADER, current_correlation_id, current_request_id
from aico.api.dependencies import get_answer_service
from aico.api.identity import TrustedIdentity, get_trusted_identity
from aico.platform.model_gateway import CallMetadata, ChatRequest, ChatResult
from aico.rag.answer_service import GroundedAnswerService
from aico.rag.citation_validator import EvidenceChunk

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "day06"
API_CASES = {c["id"]: c for c in json.loads((FIXTURES_DIR / "api_cases.json").read_text(encoding="utf-8"))["cases"]}

_VALID_IDENTITY = TrustedIdentity(tenant_id="TENANT-SYN-001", user_id="USER-SYN-001")


class FakeGateway:
    def __init__(self, respond):
        self._respond = respond

    def chat(self, request: ChatRequest) -> ChatResult:
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
    return [EvidenceChunk(chunk_id="DOC-003::chunk-0", source_file="DOC-003-pricing-payment.md", text="Payment is net 30.")]


_ANSWERED_JSON = """
{
  "schema_version": "1.0",
  "status": "answered",
  "answer": "Payment terms are net 30 days from invoice date.",
  "citations": [{"chunk_id": "DOC-003::chunk-0", "source_file": "DOC-003-pricing-payment.md"}],
  "confidence_label": "high"
}
"""


def _client() -> TestClient:
    app.dependency_overrides[get_answer_service] = lambda: GroundedAnswerService(
        gateway=FakeGateway(_ANSWERED_JSON), retriever=_fake_retriever
    )
    app.dependency_overrides[get_trusted_identity] = lambda: _VALID_IDENTITY
    return TestClient(app)


def teardown_function() -> None:
    app.dependency_overrides.clear()


_QUESTION = {"question": "What payment terms are stated in the supplier policy?"}


# ── Generation ────────────────────────────────────────────────────────


def test_missing_correlation_context_is_generated():
    """API-005 from the fixture pack: no incoming correlation id -> the
    server generates one."""
    case = API_CASES["API-005"]
    assert case["incoming_correlation_id"] is None
    assert case["expected"] == "server_generates_correlation_id"

    resp = _client().post("/ask", json=case["payload"])

    assert resp.status_code == 200
    body = resp.json()
    assert body["correlation_id"]
    assert body["request_id"]
    # Task 3: returned via headers too, and headers/body agree.
    assert resp.headers[CORRELATION_ID_HEADER] == body["correlation_id"]
    assert resp.headers[REQUEST_ID_HEADER] == body["request_id"]


def test_missing_request_and_correlation_ids_are_both_generated_when_no_headers_sent():
    resp = _client().post("/ask", json=_QUESTION)

    assert resp.status_code == 200
    assert resp.headers[REQUEST_ID_HEADER]
    assert resp.headers[CORRELATION_ID_HEADER]
    assert resp.headers[REQUEST_ID_HEADER] != resp.headers[CORRELATION_ID_HEADER]


# ── Caller-supplied IDs are honored ──────────────────────────────────────


def test_incoming_request_and_correlation_ids_are_echoed_not_replaced():
    headers = {REQUEST_ID_HEADER: "caller-req-123", CORRELATION_ID_HEADER: "caller-corr-abc"}

    resp = _client().post("/ask", json=_QUESTION, headers=headers)

    assert resp.status_code == 200
    assert resp.headers[REQUEST_ID_HEADER] == "caller-req-123"
    assert resp.headers[CORRELATION_ID_HEADER] == "caller-corr-abc"
    body = resp.json()
    assert body["request_id"] == "caller-req-123"
    assert body["correlation_id"] == "caller-corr-abc"


def test_blank_incoming_id_is_treated_as_absent():
    headers = {CORRELATION_ID_HEADER: "   "}

    resp = _client().post("/ask", json=_QUESTION, headers=headers)

    assert resp.status_code == 200
    assert resp.headers[CORRELATION_ID_HEADER].strip() != ""
    assert resp.headers[CORRELATION_ID_HEADER] != "   "


# ── Propagation: same ID reachable from deep inside the request ─────────


def test_correlation_id_is_reachable_via_contextvar_during_the_request():
    """The mechanism Tasks 7-9 (structured logs/metrics/spans) will read
    from: `current_correlation_id()`, called from inside
    `GroundedAnswerService.answer()`, must see the exact same value the
    middleware decided and the response carries."""
    observed: dict[str, object] = {}

    class ObservingGateway(FakeGateway):
        def chat(self, request: ChatRequest) -> ChatResult:
            observed["request_id"] = current_request_id()
            observed["correlation_id"] = current_correlation_id()
            return super().chat(request)

    app.dependency_overrides[get_answer_service] = lambda: GroundedAnswerService(
        gateway=ObservingGateway(_ANSWERED_JSON), retriever=_fake_retriever
    )
    app.dependency_overrides[get_trusted_identity] = lambda: _VALID_IDENTITY
    client = TestClient(app)

    headers = {REQUEST_ID_HEADER: "req-propagation-1", CORRELATION_ID_HEADER: "corr-propagation-1"}
    resp = client.post("/ask", json=_QUESTION, headers=headers)

    assert resp.status_code == 200
    assert observed["request_id"] == "req-propagation-1"
    assert observed["correlation_id"] == "corr-propagation-1"
    assert resp.json()["correlation_id"] == "corr-propagation-1"


def test_context_vars_are_not_set_outside_a_request():
    assert current_request_id() is None
    assert current_correlation_id() is None


# ── An error response is still traceable ─────────────────────────────────


def test_identity_rejection_response_still_carries_request_and_correlation_ids():
    from aico.api.identity import IdentityError

    def _raise_identity_error() -> TrustedIdentity:
        raise IdentityError("missing or malformed Authorization bearer token")

    app.dependency_overrides[get_answer_service] = lambda: GroundedAnswerService(
        gateway=FakeGateway(_ANSWERED_JSON), retriever=_fake_retriever
    )
    app.dependency_overrides[get_trusted_identity] = _raise_identity_error
    client = TestClient(app)

    headers = {REQUEST_ID_HEADER: "req-rejected-1", CORRELATION_ID_HEADER: "corr-rejected-1"}
    resp = client.post("/ask", json=_QUESTION, headers=headers)

    assert resp.status_code == 401
    assert resp.headers[REQUEST_ID_HEADER] == "req-rejected-1"
    assert resp.headers[CORRELATION_ID_HEADER] == "corr-rejected-1"
    body = resp.json()
    assert body["request_id"] == "req-rejected-1"
    assert body["correlation_id"] == "corr-rejected-1"
