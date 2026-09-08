"""
Day 6 Task 4 — request protection and error contracts.

Proves, against `tests/fixtures/day06/api_cases.json`:
- API-002: an unsupported Content-Type is rejected 4xx, and the RAG/model
  pipeline never runs.
- API-003: a payload over the documented size ceiling is rejected 4xx,
  before the RAG/model pipeline runs.
- API-004: an invalid request body (missing `question`) is rejected 4xx.
- API-001: the valid case still succeeds (protection must not false-positive).

And beyond the fixture pack:
- Every 4xx/5xx source (content-type, size, identity rejection, body
  validation, an unexpected exception) uses the *same* `ErrorResponse`
  envelope shape - `error_code`/`message`/`request_id`/`correlation_id` -
  never a stack trace or raw exception text.

No network call: same `FakeGateway`/fake-retriever pattern as the other
Day 6 API test files.
"""
from __future__ import annotations

import asyncio
import json
import pathlib

from fastapi.testclient import TestClient

from aico.api.app import app
from aico.api.dependencies import get_answer_service
from aico.api.identity import TrustedIdentity, get_trusted_identity
from aico.api.request_protection import MAX_REQUEST_BODY_BYTES
from aico.platform.model_gateway import CallMetadata, ChatRequest, ChatResult
from aico.rag.answer_service import GroundedAnswerService
from aico.rag.citation_validator import EvidenceChunk

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "day06"
API_CASES = {c["id"]: c for c in json.loads((FIXTURES_DIR / "api_cases.json").read_text(encoding="utf-8"))["cases"]}

_VALID_IDENTITY = TrustedIdentity(tenant_id="TENANT-SYN-001", user_id="USER-SYN-001")
_ERROR_ENVELOPE_FIELDS = {"error_code", "message", "request_id", "correlation_id"}


class FakeGateway:
    def __init__(self, respond):
        self._respond = respond
        self.call_count = 0

    def chat(self, request: ChatRequest) -> ChatResult:
        self.call_count += 1
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


def _client(gateway=None, *, raise_server_exceptions: bool = True) -> tuple[TestClient, FakeGateway]:
    gateway = gateway or FakeGateway(_ANSWERED_JSON)
    app.dependency_overrides[get_answer_service] = lambda: GroundedAnswerService(
        gateway=gateway, retriever=_fake_retriever
    )
    app.dependency_overrides[get_trusted_identity] = lambda: _VALID_IDENTITY
    return TestClient(app, raise_server_exceptions=raise_server_exceptions), gateway


def teardown_function() -> None:
    app.dependency_overrides.clear()


def _assert_error_envelope(body: dict) -> None:
    assert set(body.keys()) == _ERROR_ENVELOPE_FIELDS
    assert isinstance(body["error_code"], str) and body["error_code"]
    assert isinstance(body["message"], str) and body["message"]
    # Never a stack trace / raw exception repr.
    assert "Traceback" not in body["message"]
    assert "  File \"" not in body["message"]


# ── Content-Type ──────────────────────────────────────────────────────


def test_unsupported_content_type_is_rejected_before_pipeline_runs():
    case = API_CASES["API-002"]
    assert case["expected_class"] == "4xx"
    assert case["must_not_reach_expensive_pipeline"] is True

    client, gateway = _client()
    resp = client.post("/ask", content=case["payload"], headers={"content-type": case["content_type"]})

    assert 400 <= resp.status_code < 500
    assert gateway.call_count == 0
    _assert_error_envelope(resp.json())
    assert resp.json()["error_code"] == "unsupported_content_type"


def test_json_with_charset_suffix_is_still_accepted():
    """application/json; charset=utf-8 is still JSON - the check must not
    be an exact string match against the bare media type."""
    client, gateway = _client()
    resp = client.post(
        "/ask",
        content=json.dumps({"question": "What payment terms are stated?"}),
        headers={"content-type": "application/json; charset=utf-8"},
    )
    assert resp.status_code == 200
    assert gateway.call_count == 1


# ── Request size ──────────────────────────────────────────────────────


def test_oversize_payload_is_rejected_before_pipeline_runs():
    case = API_CASES["API-003"]
    assert case["expected_class"] == "4xx"
    assert case["must_not_reach_expensive_pipeline"] is True

    oversized_question = "x" * (MAX_REQUEST_BODY_BYTES + 1024)
    body = json.dumps({"question": oversized_question}).encode("utf-8")
    assert len(body) > MAX_REQUEST_BODY_BYTES

    client, gateway = _client()
    resp = client.post("/ask", content=body, headers={"content-type": "application/json"})

    assert 400 <= resp.status_code < 500
    assert gateway.call_count == 0
    body_json = resp.json()
    _assert_error_envelope(body_json)
    assert body_json["error_code"] == "payload_too_large"


def test_payload_at_or_below_the_limit_is_not_rejected_for_size():
    small_body = json.dumps({"question": "What payment terms are stated in the supplier policy?"}).encode("utf-8")
    assert len(small_body) <= MAX_REQUEST_BODY_BYTES

    client, gateway = _client()
    resp = client.post("/ask", content=small_body, headers={"content-type": "application/json"})

    assert resp.status_code == 200
    assert gateway.call_count == 1


# ── Request size: missing/lying/chunked Content-Length ───────────────────
#
# `httpx`/`TestClient` (used everywhere above) always sets an honest
# Content-Length itself - it cannot send the adversarial requests below.
# These drive `app` directly over a raw ASGI scope/receive/send trio, the
# same technique `test_day06_cancellation.py` uses for behavior TestClient
# cannot exercise, to prove the streamed-byte-count guard
# (`request_protection._read_and_replay_within_limit`) - not just the
# Content-Length header check - is what stops an oversize body reaching
# `GroundedAnswerService` when a client omits, understates, or cannot
# supply (chunked transfer-encoding) that header.


def _run_ask_over_raw_asgi(gateway, body: bytes, headers: list[tuple[bytes, bytes]], *, chunk_size: int | None = None):
    """Drives one POST /ask through `app` over raw ASGI. `chunk_size` set
    splits `body` into that many bytes per `http.request` message (more
    than one message, `more_body=True` until the last) - otherwise the
    whole body is delivered in a single message, exactly like a normal
    request. Every case here is rejected by `RequestProtectionMiddleware`
    itself, before `run_cancellable`/the answer service ever runs, so
    there is no client-disconnect timing to race - `receive()` need never
    be called again after the body is rejected or fully delivered."""

    app.dependency_overrides[get_answer_service] = lambda: GroundedAnswerService(
        gateway=gateway, retriever=_fake_retriever
    )
    app.dependency_overrides[get_trusted_identity] = lambda: _VALID_IDENTITY

    chunks = [body[i : i + chunk_size] for i in range(0, len(body), chunk_size)] if chunk_size else [body]
    chunks = chunks or [b""]
    index = 0

    async def receive():
        nonlocal index
        chunk = chunks[index]
        index += 1
        return {"type": "http.request", "body": chunk, "more_body": index < len(chunks)}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/ask",
        "raw_path": b"/ask",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("testclient", 1234),
        "server": ("testserver", 80),
        "state": {},
    }

    asyncio.run(app(scope, receive, send))

    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body_bytes = b"".join(m["body"] for m in sent if m["type"] == "http.response.body")
    return status, json.loads(body_bytes)


def _oversize_ask_body() -> bytes:
    oversized_question = "x" * (MAX_REQUEST_BODY_BYTES + 1024)
    return json.dumps({"question": oversized_question}).encode("utf-8")


def test_missing_content_length_with_oversize_body_is_still_rejected():
    """A client that omits `Content-Length` entirely (legal HTTP) must not
    get an oversize body past the ceiling just because the header-only
    fast path in `RequestProtectionMiddleware` has nothing to check."""

    gateway = FakeGateway(_ANSWERED_JSON)
    status, body_json = _run_ask_over_raw_asgi(gateway, _oversize_ask_body(), [(b"content-type", b"application/json")])

    assert status == 413
    assert gateway.call_count == 0
    _assert_error_envelope(body_json)
    assert body_json["error_code"] == "payload_too_large"


def test_lying_content_length_with_oversize_body_is_still_rejected():
    """A client that declares a small `Content-Length` (e.g. `1`) while
    actually streaming far more must not get the oversize body past the
    ceiling just because the declared header happened to look fine."""

    gateway = FakeGateway(_ANSWERED_JSON)
    headers = [(b"content-type", b"application/json"), (b"content-length", b"1")]
    status, body_json = _run_ask_over_raw_asgi(gateway, _oversize_ask_body(), headers)

    assert status == 413
    assert gateway.call_count == 0
    _assert_error_envelope(body_json)
    assert body_json["error_code"] == "payload_too_large"


def test_chunked_transfer_encoding_with_oversize_body_is_still_rejected():
    """Chunked transfer-encoding carries no `Content-Length` at all, so
    this proves the streamed-byte-count guard - not the header check -
    is what actually stops it, across several delivered messages."""

    gateway = FakeGateway(_ANSWERED_JSON)
    headers = [(b"content-type", b"application/json"), (b"transfer-encoding", b"chunked")]
    status, body_json = _run_ask_over_raw_asgi(gateway, _oversize_ask_body(), headers, chunk_size=4096)

    assert status == 413
    assert gateway.call_count == 0
    _assert_error_envelope(body_json)
    assert body_json["error_code"] == "payload_too_large"


# ── Direct unit tests for the streamed-byte-count guard ──────────────────


def test_read_and_replay_within_limit_reproduces_a_multi_chunk_body_untouched():
    """A within-limit body delivered across several `http.request`
    messages must come back out of the replay channel exactly as it went
    in - proves the guard is transparent to a normal request, whatever
    the underlying server happened to split into wire chunks."""
    from aico.api.request_protection import _read_and_replay_within_limit

    chunks = [b"abc", b"def", b"ghi"]
    index = 0

    async def receive():
        nonlocal index
        message = {"type": "http.request", "body": chunks[index], "more_body": index < len(chunks) - 1}
        index += 1
        return message

    async def run() -> bytes:
        replay, total = await _read_and_replay_within_limit(receive, max_body_bytes=100)
        assert total == 9
        assert replay is not None
        collected = b""
        while True:
            message = await replay()
            collected += message["body"]
            if not message.get("more_body", False):
                break
        return collected

    assert asyncio.run(run()) == b"abcdefghi"


def test_read_and_replay_within_limit_stops_promptly_once_the_ceiling_is_crossed():
    """A body with no natural end that is already over the ceiling must
    not be read indefinitely - the guard should stop within a couple of
    messages of crossing the limit, not buffer the whole (adversarial,
    effectively unbounded) stream first."""
    from aico.api.request_protection import _read_and_replay_within_limit

    calls = {"count": 0}

    async def receive():
        calls["count"] += 1
        return {"type": "http.request", "body": b"0123456789", "more_body": True}

    replay, total = asyncio.run(_read_and_replay_within_limit(receive, max_body_bytes=25))

    assert replay is None
    assert total > 25
    assert calls["count"] <= 4


# ── Invalid request body ─────────────────────────────────────────────────


def test_invalid_request_body_returns_error_envelope():
    case = API_CASES["API-004"]
    assert case["expected_class"] == "4xx"

    client, gateway = _client()
    resp = client.post("/ask", json=case["payload"])

    assert 400 <= resp.status_code < 500
    assert gateway.call_count == 0
    body = resp.json()
    _assert_error_envelope(body)
    assert body["error_code"] == "invalid_request"


# ── Valid case still succeeds ─────────────────────────────────────────────


def test_valid_ask_still_succeeds_through_the_protection_middleware():
    case = API_CASES["API-001"]
    assert case["expected_class"] == "success"

    client, gateway = _client()
    resp = client.post("/ask", json=case["payload"])

    assert resp.status_code == 200
    assert gateway.call_count == 1


# ── Consistent envelope across every failure source ──────────────────────


def test_identity_rejection_uses_the_same_error_envelope():
    from aico.api.identity import IdentityError

    def _raise_identity_error() -> TrustedIdentity:
        raise IdentityError("missing or malformed Authorization bearer token")

    client, _ = _client()
    app.dependency_overrides[get_trusted_identity] = _raise_identity_error

    resp = client.post("/ask", json={"question": "What payment terms are stated in the supplier policy?"})

    assert resp.status_code == 401
    body = resp.json()
    _assert_error_envelope(body)
    assert body["error_code"] == "trusted_identity_rejected"


def test_unexpected_exception_returns_generic_safe_error_without_leaking_detail():
    """Starlette's `ServerErrorMiddleware` re-raises the original
    exception (by design - so it still reaches the ASGI server's own
    error logging) even after our registered handler has already sent the
    safe 500 response a real client receives - so this test needs
    `raise_server_exceptions=False` to observe that response instead of
    having the raw exception propagate into the test itself."""

    class ExplodingGateway:
        def chat(self, request):
            raise RuntimeError("provider secret=sk-should-never-leak connection to internal-db-host failed")

    client, _ = _client(ExplodingGateway(), raise_server_exceptions=False)

    resp = client.post("/ask", json={"question": "What payment terms are stated in the supplier policy?"})

    assert resp.status_code == 500
    body = resp.json()
    _assert_error_envelope(body)
    assert body["error_code"] == "internal_error"
    assert "sk-should-never-leak" not in body["message"]
    assert "internal-db-host" not in body["message"]
    assert "RuntimeError" not in body["message"]
