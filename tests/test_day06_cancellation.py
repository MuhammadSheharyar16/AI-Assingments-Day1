"""
Day 6 Task 5 — cancellation propagation.

Required deterministic test (assignment brief, Task 5): "Use a fake slow
dependency. Start an /ask operation and cancel/disconnect it. Prove: (1)
in-flight fake work receives cancellation, (2) expensive work does not
continue to normal completion, (3) no successful response is fabricated
after cancellation." No real model call anywhere in this file.

Two layers:

1. `test_cancellation_stops_in_flight_fake_work_before_normal_completion`
   - the primary required test. A `FakeSlowGateway.chat()` simulates
   "expensive work" by polling its `ChatRequest.cancellation` token in
   small increments (this is exactly what a transport plugged into
   `ModelGateway` is expected to do to be cancellation-aware - see
   model_gateway.py's `CancellationToken` docstring) instead of blocking
   uninterruptibly. A `threading.Event` proves the fake had actually
   started - not merely queued - before the token is cancelled, so this
   is unambiguously a mid-flight interruption, not a pre-start rejection.

2. `test_http_disconnect_propagates_cancellation_into_the_pipeline` -
   proves the plumbing in app.py/request_cancellation.py: a real client
   disconnect detected at the HTTP layer reaches the same fake gateway's
   cancellation token, without going through `TestClient` (whose own
   `receive()` only reports a disconnect after the response is already
   complete, which is too late to prove mid-request propagation - see the
   test's docstring) - a raw ASGI `scope`/`receive`/`send` call instead,
   which is what the assignment's "cancel/disconnect it" is actually
   describing. This test's `receive()` reports disconnect starting on its
   second call - it cannot pin down exactly how many polls
   `FakeSlowGateway` completed first (unlike the precisely-timed
   deterministic test above), but `FakeSlowGateway.started` is set at
   entry to `.chat()`, before its polling loop even begins, so
   `cancelled_mid_flight=True` still proves the fake's work was genuinely
   entered and running, not merely rejected before starting - Starlette's
   `BaseHTTPMiddleware` (which `CorrelationMiddleware` is) validates that
   a `receive()` it wraps yields at most one real `http.request` message
   followed only by `http.disconnect`, so this cannot be relaxed further
   without breaking that contract.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

from aico.api.app import app
from aico.api.dependencies import get_answer_service
from aico.api.identity import TrustedIdentity, get_trusted_identity
from aico.platform.errors import GatewayCancelledError
from aico.platform.model_gateway import CallMetadata, CancellationToken, ChatRequest, ChatResult
from aico.rag.answer_service import GroundedAnswer, GroundedAnswerService, TypedFailure
from aico.rag.citation_validator import EvidenceChunk

_VALID_IDENTITY = TrustedIdentity(tenant_id="TENANT-SYN-001", user_id="USER-SYN-001")


def teardown_function() -> None:
    app.dependency_overrides.clear()


def _fake_retriever(question: str) -> list[EvidenceChunk]:
    return [EvidenceChunk(chunk_id="DOC-003::chunk-0", source_file="DOC-003-pricing-payment.md", text="Payment is net 30.")]


class FakeSlowGateway:
    """Duck-typed `ModelGateway` stand-in whose `.chat()` simulates
    expensive work by polling `request.cancellation` in small increments
    - proving cancellation reaches the Model Gateway path, never a real
    model call (Task 5's own rule). `started` fires the instant polling
    begins, so a caller can wait for genuinely in-flight work before
    cancelling."""

    def __init__(self, poll_interval: float = 0.01, max_polls: int = 1000):
        self.poll_interval = poll_interval
        self.max_polls = max_polls
        self.started = threading.Event()
        self.cancelled_mid_flight = False
        self.completed_normally = False
        self.polls_before_cancellation: int | None = None

    def chat(self, request: ChatRequest) -> ChatResult:
        self.started.set()
        token = request.cancellation
        for i in range(self.max_polls):
            if token is not None and token.is_cancelled():
                self.cancelled_mid_flight = True
                self.polls_before_cancellation = i
                raise GatewayCancelledError("fake slow work cancelled mid-flight")
            time.sleep(self.poll_interval)
        self.completed_normally = True
        return ChatResult(
            content='{"schema_version": "1.0", "status": "answered", "answer": "should never be produced", '
            '"citations": [], "confidence_label": "high"}',
            metadata=CallMetadata(
                operation="chat",
                model_alias="fake-slow",
                latency_ms=1.0,
                retry_count=0,
                token_usage=None,
                budget_status="unknown",
            ),
        )


# ── Required deterministic test ──────────────────────────────────────────


def test_cancellation_stops_in_flight_fake_work_before_normal_completion():
    gateway = FakeSlowGateway()
    service = GroundedAnswerService(gateway=gateway, retriever=_fake_retriever)
    cancellation = CancellationToken()

    result_holder: dict[str, object] = {}

    def run() -> None:
        result_holder["result"] = service.answer(
            "What payment terms are stated in the supplier policy?", cancellation
        )

    worker = threading.Thread(target=run)
    worker.start()

    # Wait for the fake work to actually be in flight before cancelling -
    # this is what makes the assertion below "mid-flight", not "pre-start".
    assert gateway.started.wait(timeout=2), "fake gateway never started - test setup is broken"
    cancellation.cancel()
    worker.join(timeout=5)
    assert not worker.is_alive(), "answer() did not return after cancellation"

    # (1) in-flight fake work receives cancellation
    assert gateway.cancelled_mid_flight is True
    assert gateway.polls_before_cancellation is not None

    # (2) expensive work does not continue to normal completion
    assert gateway.completed_normally is False

    # (3) no successful response is fabricated after cancellation
    result = result_holder["result"]
    assert not isinstance(result, GroundedAnswer)
    assert isinstance(result, TypedFailure)
    assert result.stage == "gateway"
    assert result.category == "cancelled"


def test_cancellation_before_any_work_starts_also_never_fabricates_success():
    """A token cancelled before the fake gateway is ever invoked must
    still prevent a successful result - cancellation is checked, not
    just cleaned up after the fact."""
    gateway = FakeSlowGateway()
    service = GroundedAnswerService(gateway=gateway, retriever=_fake_retriever)
    cancellation = CancellationToken()
    cancellation.cancel()

    result = service.answer("What payment terms are stated in the supplier policy?", cancellation)

    assert not isinstance(result, GroundedAnswer)


def test_uncancelled_request_still_completes_normally():
    """Sanity check: the cancellation seam must not interfere with the
    ordinary, non-cancelled path - a fast fake gateway with no
    cancellation still returns a normal answer."""

    class FastFakeGateway:
        def chat(self, request: ChatRequest) -> ChatResult:
            return ChatResult(
                content='{"schema_version": "1.0", "status": "answered", "answer": "Net 30.", '
                '"citations": [{"chunk_id": "DOC-003::chunk-0", "source_file": "DOC-003-pricing-payment.md"}], '
                '"confidence_label": "high"}',
                metadata=CallMetadata(
                    operation="chat",
                    model_alias="fast-fake",
                    latency_ms=1.0,
                    retry_count=0,
                    token_usage=None,
                    budget_status="unknown",
                ),
            )

    service = GroundedAnswerService(gateway=FastFakeGateway(), retriever=_fake_retriever)
    result = service.answer("What payment terms are stated in the supplier policy?", CancellationToken())

    assert isinstance(result, GroundedAnswer)


# ── HTTP-layer plumbing: a real disconnect reaches the pipeline ─────────


def test_http_disconnect_propagates_cancellation_into_the_pipeline():
    """Drives `app` directly over a raw ASGI `scope`/`receive`/`send`
    trio instead of `TestClient` - `TestClient`'s own `receive()` only
    reports `http.disconnect` *after* the response it is waiting on has
    already completed (see starlette's testclient.py), which cannot prove
    a disconnect observed *during* request handling. A custom `receive`
    reports the request body once, then reports `http.disconnect` on every
    call *after* `FakeSlowGateway` has actually started polling - so, like
    the deterministic test above, this is unambiguously mid-flight, now
    proven through the real HTTP handler (app.py) and
    `request_cancellation.run_cancellable`, not by calling
    `GroundedAnswerService` directly."""

    gateway = FakeSlowGateway()
    app.dependency_overrides[get_answer_service] = lambda: GroundedAnswerService(
        gateway=gateway, retriever=_fake_retriever
    )
    app.dependency_overrides[get_trusted_identity] = lambda: _VALID_IDENTITY

    body = json.dumps({"question": "What payment terms are stated in the supplier policy?"}).encode("utf-8")
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
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("testclient", 1234),
        "server": ("testserver", 80),
        "state": {},
    }

    request_delivered = False

    async def receive():
        nonlocal request_delivered
        if not request_delivered:
            request_delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        # Every call after the body message reports disconnect. No
        # `await` here, so Starlette's `Request.is_disconnected()` (which
        # calls `receive()` inside an already-cancelled `anyio.CancelScope`,
        # specifically to avoid ever blocking) always sees this function
        # run to completion - see request_cancellation.py's docstring.
        return {"type": "http.disconnect"}

    sent_messages: list[dict] = []

    async def send(message):
        sent_messages.append(message)

    async def invoke():
        await app(scope, receive, send)

    asyncio.run(invoke())

    # (1) in-flight fake work receives cancellation - proven through the
    # real HTTP handler this time, not a direct service call.
    assert gateway.cancelled_mid_flight is True
    # (2) expensive work does not continue to normal completion
    assert gateway.completed_normally is False

    # (3) no successful response is fabricated after cancellation: the
    # response actually sent must not claim status="answered".
    body_bytes = b"".join(m["body"] for m in sent_messages if m["type"] == "http.response.body")
    response_body = json.loads(body_bytes)
    assert response_body["status"] != "answered"
