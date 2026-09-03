"""
Day 6 Task 2 — trusted identity context.

Two layers, both proven here:

1. `build_trusted_identity` (identity.py) - the pure claims -> TrustedIdentity
   decision - exercised directly against every case in
   `tests/fixtures/day06/identity_claim_cases.json` (ID-001..004), so
   the fixture pack's synthetic trusted-principal cases are the actual
   source of truth for allow/reject behavior, not a hand-copied duplicate.
2. The full `/ask` route (app.py) - proving the trust boundary is actually
   wired on the endpoint (an override that raises `IdentityError` rejects
   the HTTP request before it reaches the RAG pipeline; conflicting
   caller-supplied "identity" in the request body can never override the
   trusted claims - see `test_body_identity_cannot_override_trusted_claims`,
   ID-005).

No real JWT/network call anywhere in this file: dependency behavior is
tested by overriding `get_trusted_identity` directly through
`app.dependency_overrides` (Task 10's seam), exactly like
`test_day06_api.py` overrides `get_answer_service`.
"""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from aico.api.app import app
from aico.api.dependencies import get_answer_service
from aico.api.identity import IdentityError, TrustedIdentity, build_trusted_identity, get_trusted_identity
from aico.platform.model_gateway import CallMetadata, ChatRequest, ChatResult
from aico.rag.answer_service import GroundedAnswerService
from aico.rag.citation_validator import EvidenceChunk

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "day06"
IDENTITY_CASES = json.loads((FIXTURES_DIR / "identity_claim_cases.json").read_text(encoding="utf-8"))["cases"]


def teardown_function() -> None:
    app.dependency_overrides.clear()


# ── build_trusted_identity: direct fixture-driven proof ─────────────────


@pytest.mark.parametrize("case", IDENTITY_CASES, ids=[c["id"] for c in IDENTITY_CASES])
def test_build_trusted_identity_matches_fixture_expectation(case):
    claims = case["trusted_claims"]

    if case["expected"] in ("allow_request_to_continue", "trusted_claims_remain_authoritative"):
        # ID-001 and ID-005 both carry valid trusted_claims - ID-005's
        # `request_body_extra_identity` is a separate, untrusted field the
        # claims-decision function never even receives (it only sees
        # verified `trusted_claims`), so build_trusted_identity has
        # nothing to reject here; the actual override attempt is proven
        # rejected end-to-end in test_body_identity_cannot_override_trusted_claims.
        identity = build_trusted_identity(claims)
        assert identity.tenant_id == claims["tenant_id"]
        assert identity.user_id == claims["user_id"]
        if "request_body_extra_identity" in case:
            attack = case["request_body_extra_identity"]
            assert identity.tenant_id != attack["tenant_id"]
            assert identity.user_id != attack["user_id"]
    elif case["expected"] == "reject":
        with pytest.raises(IdentityError):
            build_trusted_identity(claims)
    else:  # pragma: no cover - guard against an unrecognized fixture expectation
        pytest.fail(f"unhandled fixture expectation: {case['expected']!r}")


def test_empty_identity_is_rejected():
    with pytest.raises(IdentityError):
        build_trusted_identity({})


def test_missing_tenant_is_rejected():
    with pytest.raises(IdentityError, match="tenant_id"):
        build_trusted_identity({"user_id": "USER-SYN-001"})


def test_missing_user_is_rejected():
    with pytest.raises(IdentityError, match="user_id"):
        build_trusted_identity({"tenant_id": "TENANT-SYN-001"})


def test_valid_claims_are_accepted():
    identity = build_trusted_identity({"tenant_id": "TENANT-SYN-001", "user_id": "USER-SYN-001"})
    assert identity == TrustedIdentity(tenant_id="TENANT-SYN-001", user_id="USER-SYN-001")


@pytest.mark.parametrize("bad_value", ["", "   ", None, 42, True])
def test_non_string_or_blank_claim_values_are_rejected(bad_value):
    with pytest.raises(IdentityError):
        build_trusted_identity({"tenant_id": bad_value, "user_id": "USER-SYN-001"})
    with pytest.raises(IdentityError):
        build_trusted_identity({"tenant_id": "TENANT-SYN-001", "user_id": bad_value})


# ── /ask route: the dependency actually gates the endpoint ──────────────


class FakeGateway:
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


def _fake_service() -> GroundedAnswerService:
    return GroundedAnswerService(gateway=FakeGateway(_ANSWERED_JSON), retriever=_fake_retriever)


def test_valid_trusted_identity_allows_request_to_continue():
    app.dependency_overrides[get_answer_service] = lambda: _fake_service()
    app.dependency_overrides[get_trusted_identity] = lambda: TrustedIdentity(
        tenant_id="TENANT-SYN-001", user_id="USER-SYN-001"
    )
    client = TestClient(app)

    resp = client.post("/ask", json={"question": "What payment terms are stated in the supplier policy?"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "answered"


def test_missing_or_invalid_trusted_identity_is_rejected_before_pipeline_runs():
    calls: list[str] = []

    class TrackingGateway(FakeGateway):
        def chat(self, request):  # pragma: no cover - must never run
            calls.append("chat_called")
            return super().chat(request)

    def _raise_identity_error() -> TrustedIdentity:
        raise IdentityError("missing or malformed Authorization bearer token")

    app.dependency_overrides[get_answer_service] = lambda: GroundedAnswerService(
        gateway=TrackingGateway(_ANSWERED_JSON), retriever=_fake_retriever
    )
    app.dependency_overrides[get_trusted_identity] = _raise_identity_error
    client = TestClient(app)

    resp = client.post("/ask", json={"question": "What payment terms are stated in the supplier policy?"})

    assert resp.status_code == 401
    assert calls == []  # the expensive RAG/model pipeline never ran


def test_no_authorization_header_is_rejected_by_default_provider():
    """No override at all: the real `get_trusted_identity` (JWT bearer
    verification) runs and rejects a request with no Authorization
    header - proving the default provider fails closed, not open."""
    app.dependency_overrides[get_answer_service] = lambda: _fake_service()
    client = TestClient(app)

    resp = client.post("/ask", json={"question": "What payment terms are stated in the supplier policy?"})

    assert resp.status_code == 401


def test_body_identity_cannot_override_trusted_claims():
    """ID-005: a caller-supplied tenant_id/user_id in the request body
    must never override the trusted claims. `AskRequest` (contracts.py)
    already forbids unknown fields, so this attack is rejected at the
    contract boundary - it can not even reach a point where trusted vs.
    caller-supplied identity would need to be reconciled."""
    app.dependency_overrides[get_answer_service] = lambda: _fake_service()
    app.dependency_overrides[get_trusted_identity] = lambda: TrustedIdentity(
        tenant_id="TENANT-SYN-001", user_id="USER-SYN-001"
    )
    client = TestClient(app)

    resp = client.post(
        "/ask",
        json={
            "question": "What payment terms are stated in the supplier policy?",
            "tenant_id": "TENANT-ATTACK",
            "user_id": "USER-ATTACK",
        },
    )

    assert resp.status_code == 422  # rejected by the contract, not silently accepted with attacker-supplied identity
