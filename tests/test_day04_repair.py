"""
Day 4 Task 4 — one bounded repair attempt.

Proves `src/aico/contracts/repair.py` end to end against a fake
`ModelGateway` transport (never a real network call - see the working
rule "Broken-output tests use supplied fixtures and fake Model Gateway
responses; do not create avoidable cloud cost"):

- the three required cases from the assignment brief: invalid first
  response -> repaired valid response -> success; invalid first response
  -> repaired invalid response -> typed failure; non-repairable path ->
  typed failure with zero Model Gateway calls;
- repair is capped at exactly one Model Gateway call per original
  response, structurally (never a counter that could be miscoded) -
  proven by asserting `FakeTransport.chat_calls` length directly;
- the repair request is built from the validation error (category,
  field path, message all traceable back into the sent messages);
- a Model Gateway failure during the repair call itself comes back as a
  typed failure (`stage="repair"`), never a raised exception;
- the supplied D04-11/D04-12 repair fixtures in
  `structured_output_cases.json`, run end to end through `resolve()`
  with each fixture's own `fake_repair_response` as the fake gateway
  reply.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aico.contracts.errors import ValidationFailure
from aico.contracts.models import AnswerStatus, CitedAnswer, ConfidenceLabel
from aico.contracts.repair import attempt_repair, build_repair_request, is_repairable, resolve, validate_full
from aico.platform.config import (
    BudgetsConfig,
    ChatBudget,
    EmbeddingBudget,
    FallbackPolicy,
    GatewayConfig,
    ModelAliases,
    ResilienceConfig,
    RetryConfig,
    RouteEndpoint,
    RoutingPolicy,
)
from aico.platform.errors import GatewayAuthenticationError
from aico.platform.model_gateway import ModelGateway, TransportResult

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_PATH = REPO_ROOT / "data" / "day04_pack" / "fixtures" / "structured_output_cases.json"


def _load_fixture_cases() -> dict:
    cases = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))["cases"]
    return {case["id"]: case for case in cases}


FIXTURE_CASES = _load_fixture_cases()


# ── fake gateway plumbing (same pattern as test_model_gateway.py) ──────

def _make_config(**overrides) -> GatewayConfig:
    defaults = dict(
        version="1.0",
        endpoint_env="AICO_TEST_FOUNDRY_ENDPOINT",
        models=ModelAliases(chat="test-chat-alias", embedding="test-embed-alias"),
        resilience=ResilienceConfig(
            timeout_seconds=5,
            retry=RetryConfig(max_attempts=3, base_delay_ms=100, max_delay_ms=1000, jitter=True),
        ),
        budgets=BudgetsConfig(
            chat=ChatBudget(max_input_tokens=1000, max_output_tokens=500),
            embedding=EmbeddingBudget(max_items_per_call=32),
        ),
        routing=RoutingPolicy(
            primary=RouteEndpoint(
                provider="microsoft-foundry", region="uk-south", data_boundary="uk", risk_class="standard"
            ),
            fallback=FallbackPolicy(
                enabled=False,
                route=None,
                require_compatibility={
                    "provider": True, "region": True, "data_boundary": True, "risk": True, "budget": True,
                },
            ),
        ),
    )
    defaults.update(overrides)
    return GatewayConfig(**defaults)


class FakeTransport:
    """Deterministic, in-memory Transport double - satisfies
    model_gateway.Transport without touching the network. Only `chat` is
    exercised here (repair is a chat-shaped call), but `embed` is present
    so the double is a complete Transport."""

    def __init__(self, *, chat_result: str | None = None, raises: Exception | None = None):
        self._chat_result = chat_result
        self._raises = raises
        self.chat_calls: list[dict] = []

    def embed(self, *, model_alias, texts, timeout_seconds):
        raise AssertionError("repair never calls embed()")

    def chat(self, *, model_alias, messages, max_output_tokens, timeout_seconds):
        self.chat_calls.append(
            {"model_alias": model_alias, "messages": messages,
             "max_output_tokens": max_output_tokens, "timeout_seconds": timeout_seconds}
        )
        if self._raises is not None:
            raise self._raises
        return TransportResult(content=self._chat_result or "{}", dimensions=None, token_usage=None)


def _gateway(**transport_kwargs) -> tuple[ModelGateway, FakeTransport]:
    transport = FakeTransport(**transport_kwargs)
    return ModelGateway(_make_config(), transport), transport


def _valid_cited_answer_json() -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "status": "answered",
            "answer": "Supplier insurance is required.",
            "citations": [{"chunk_id": "CHK-001", "source_file": "DOC-001.md"}],
            "confidence_label": "medium",
        }
    )


# ── is_repairable policy ────────────────────────────────────────────────

@pytest.mark.parametrize("stage,expected", [("contract", True), ("semantic", True), ("parse", False)])
def test_is_repairable_by_stage(stage, expected):
    failure = ValidationFailure(stage=stage, category="x", message="x")
    assert is_repairable(failure) is expected


# ── build_repair_request: built from the validation error ──────────────

def test_build_repair_request_carries_the_validation_error():
    failure = ValidationFailure(
        stage="contract", category="missing_field", message="Field required", field_path="answer"
    )
    request = build_repair_request('{"status": "answered"}', failure, CitedAnswer)

    combined = " ".join(m.content for m in request.messages)
    assert "missing_field" in combined
    assert "answer" in combined
    assert "Field required" in combined
    assert '{"status": "answered"}' in combined
    assert "CitedAnswer" in combined


# ── required case 1: invalid first response -> repaired valid -> success ──

def test_repair_success_invalid_then_valid_repair():
    gateway, transport = _gateway(chat_result=_valid_cited_answer_json())
    invalid_raw = json.dumps(
        {
            "schema_version": "1.0",
            "status": "answered",
            # missing "answer" - a contract failure, repairable
            "citations": [{"chunk_id": "CHK-001", "source_file": "DOC-001.md"}],
            "confidence_label": "medium",
        }
    )

    result = resolve(invalid_raw, CitedAnswer, gateway)

    assert isinstance(result, CitedAnswer)
    assert result.answer == "Supplier insurance is required."
    assert len(transport.chat_calls) == 1  # exactly one repair call


# ── required case 2: invalid first response -> repaired invalid -> failure ──

def test_repair_failure_invalid_then_invalid_repair():
    gateway, transport = _gateway(chat_result='{"schema_version": "1.0", "status": "answered", "answer": 456}')
    invalid_raw = json.dumps(
        {"schema_version": "1.0", "status": "answered", "answer": 123, "citations": [], "confidence_label": "medium"}
    )

    result = resolve(invalid_raw, CitedAnswer, gateway)

    assert isinstance(result, ValidationFailure)
    assert result.stage == "contract"  # the repaired response's own failure, revalidated fully
    assert len(transport.chat_calls) == 1  # still just one call - no retry loop


# ── required case 3: non-repairable path -> typed failure, zero calls ──

def test_non_repairable_path_never_calls_the_gateway():
    gateway, transport = _gateway()
    malformed_raw = '{"schema_version": "1.0", "status": "answered",'  # malformed JSON -> stage "parse"

    result = resolve(malformed_raw, CitedAnswer, gateway)

    assert isinstance(result, ValidationFailure)
    assert result.stage == "parse"
    assert transport.chat_calls == []  # never attempted repair


# ── a valid first response never touches the gateway either ────────────

def test_valid_first_response_never_calls_the_gateway():
    gateway, transport = _gateway()
    result = resolve(_valid_cited_answer_json(), CitedAnswer, gateway)

    assert isinstance(result, CitedAnswer)
    assert transport.chat_calls == []


# ── repair is capped at one attempt, structurally ───────────────────────

def test_attempt_repair_calls_the_gateway_exactly_once_even_when_repair_fails():
    gateway, transport = _gateway(chat_result="still not valid json {")
    failure = ValidationFailure(stage="contract", category="missing_field", message="Field required")

    result = attempt_repair('{"status": "answered"}', failure, CitedAnswer, gateway)

    assert isinstance(result, ValidationFailure)
    assert len(transport.chat_calls) == 1


def test_resolve_never_calls_attempt_repair_twice_for_one_response():
    # A repaired-but-still-broken response is returned as a typed failure
    # directly - resolve() has no code path that re-enters attempt_repair.
    gateway, transport = _gateway(chat_result='{"schema_version": "1.0"}')
    invalid_raw = '{"schema_version": "1.0", "status": "answered", "citations": [], "confidence_label": "medium"}'

    result = resolve(invalid_raw, CitedAnswer, gateway)

    assert isinstance(result, ValidationFailure)
    assert len(transport.chat_calls) == 1


# ── the repaired response is revalidated through the complete pipeline ──

def test_repaired_response_is_revalidated_for_semantic_failures_too():
    # The repair fixes the *contract*-level problem (missing answer) but
    # the repaired JSON is semantically invalid (answered, no citation) -
    # validate_full must catch that on the second pass, not just re-run
    # contract validation.
    gateway, transport = _gateway(
        chat_result=json.dumps(
            {
                "schema_version": "1.0",
                "status": "answered",
                "answer": "A fixed answer.",
                "citations": [],
                "confidence_label": "medium",
            }
        )
    )
    invalid_raw = json.dumps(
        {"schema_version": "1.0", "status": "answered", "citations": [], "confidence_label": "medium"}
    )

    result = resolve(invalid_raw, CitedAnswer, gateway)

    assert isinstance(result, ValidationFailure)
    assert result.stage == "semantic"
    assert result.category == "s1_answered_without_citation"
    assert len(transport.chat_calls) == 1


# ── a Model Gateway failure during repair is a typed failure, not a raise ──

def test_gateway_failure_during_repair_is_a_typed_failure():
    # Non-retryable (GatewayAuthenticationError.retryable is False) so the
    # gateway fails on the first attempt with no Day 3 retry/backoff in
    # between - keeps this test instant and its category unambiguous.
    gateway, transport = _gateway(raises=GatewayAuthenticationError("simulated auth failure"))
    failure = ValidationFailure(stage="contract", category="missing_field", message="Field required")

    result = attempt_repair('{"status": "answered"}', failure, CitedAnswer, gateway)

    assert isinstance(result, ValidationFailure)
    assert result.stage == "repair"
    assert result.category == "repair_call_failed"
    assert "authentication" in result.message
    assert len(transport.chat_calls) == 1


# ── validate_full: the composed pipeline repair revalidates against ────

def test_validate_full_runs_both_contract_and_semantic_stages():
    valid = validate_full(_valid_cited_answer_json(), CitedAnswer)
    assert isinstance(valid, CitedAnswer)

    contract_bad = validate_full('{"schema_version": "1.0"', CitedAnswer)
    assert isinstance(contract_bad, ValidationFailure)
    assert contract_bad.stage == "parse"

    semantic_bad_raw = json.dumps(
        {
            "schema_version": "1.0",
            "status": "insufficient_evidence",
            "answer": "INSUFFICIENT_EVIDENCE: no source.",
            "citations": [],
            "confidence_label": "high",
        }
    )
    semantic_bad = validate_full(semantic_bad_raw, CitedAnswer)
    assert isinstance(semantic_bad, ValidationFailure)
    assert semantic_bad.stage == "semantic"


# ── driven by the supplied repair fixtures ──────────────────────────────

def test_fixture_d04_11_repairable_invalid_response_succeeds_after_repair():
    case = FIXTURE_CASES["D04-11"]
    gateway, transport = _gateway(chat_result=case["fake_repair_response"])

    result = resolve(case["raw"], CitedAnswer, gateway)

    assert isinstance(result, CitedAnswer)
    assert len(transport.chat_calls) == 1


def test_fixture_d04_12_repair_still_invalid_returns_typed_failure():
    case = FIXTURE_CASES["D04-12"]
    gateway, transport = _gateway(chat_result=case["fake_repair_response"])

    result = resolve(case["raw"], CitedAnswer, gateway)

    assert isinstance(result, ValidationFailure)
    assert result.stage == "contract"
    assert len(transport.chat_calls) == 1


# ── Day 4 repair is not Day 3 provider retry ────────────────────────────

def test_repair_does_not_engage_day3_retry_for_a_successful_call():
    # A successful repair call reports retry_count == 0 on the gateway's
    # own metadata path - Day 3's bounded retry is about transport
    # failures (timeouts, 5xx), never about Day 4 output shape, and nothing
    # here asks the gateway to retry for an output-validation reason.
    gateway, transport = _gateway(chat_result=_valid_cited_answer_json())
    failure = ValidationFailure(stage="contract", category="missing_field", message="Field required")
    result = attempt_repair('{"status": "answered"}', failure, CitedAnswer, gateway)
    assert isinstance(result, CitedAnswer)
    assert len(transport.chat_calls) == 1
