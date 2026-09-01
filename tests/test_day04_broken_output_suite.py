"""
Day 4 Task 5 — broken-output fixture suite.

The single place every case in
`data/day04_pack/fixtures/structured_output_cases.json` is run end to end
and asserted against its documented final outcome. This is distinct from
Tasks 2-4's own test files, which each prove one *stage* of the pipeline
in isolation (sometimes reusing the same fixture) - this file instead
asserts that a given raw response reaches the right *final* resolution,
and that all twelve supplied cases are actually covered somewhere.

Two different pipeline boundaries are used, deliberately:

- D04-01..D04-10 go through `validate_full()` (parse -> contract ->
  semantic, no repair). None of these ten fixtures carries a
  `fake_repair_response` in the supplied JSON - that omission is the
  signal that they exist to prove correct behavior at a *stage*, not to
  exercise repair. A contract- or semantic-stage failure is technically
  repair-eligible per `repair.is_repairable`; running the *full*
  `resolve()` pipeline on these would trigger a repair call these
  fixtures never provisioned a response for. `test_valid_and_parse_
  failure_fixtures_never_touch_the_gateway_even_via_resolve` below
  proves the two fixtures where `resolve()` *is* safe to use directly
  (D04-01, D04-02 - and D04-03, which resolves to valid the same way)
  still make zero Model Gateway calls, using a transport double that
  fails the test if it is ever called.
- D04-11/D04-12 go through `resolve()` with a fake `ModelGateway` wired
  to each fixture's own `fake_repair_response` - these are the two
  fixtures that exist specifically to exercise the bounded repair path
  end to end (Task 4).

Markdown-wrapped JSON (D04-03) - this suite's documented policy choice,
per the brief's "either reject it deterministically, or support one
clearly documented bounded unwrapping behavior": bounded unwrap. See
`validator.py`'s `_MARKDOWN_FENCE_RE` for the exact rule (one ```json
fence, nothing outside it but whitespace).
`test_markdown_fence_unwrap_never_accepts_surrounding_prose` below is the
explicit proof that choice never extends to accepting arbitrary prose
around JSON, per the brief's closing instruction.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aico.contracts.errors import ValidationFailure
from aico.contracts.models import CitedAnswer
from aico.contracts.repair import resolve, validate_full
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
from aico.platform.model_gateway import ModelGateway, TransportResult

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_PATH = REPO_ROOT / "data" / "day04_pack" / "fixtures" / "structured_output_cases.json"


def _load_fixture_cases() -> dict:
    cases = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))["cases"]
    return {case["id"]: case for case in cases}


FIXTURE_CASES = _load_fixture_cases()


# ── fake gateway plumbing (same pattern as test_day04_repair.py) ───────

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
    """Deterministic, in-memory Transport double - never touches the network."""

    def __init__(self, *, chat_result: str | None = None):
        self._chat_result = chat_result
        self.chat_calls: list[dict] = []

    def embed(self, *, model_alias, texts, timeout_seconds):
        raise AssertionError("this suite never calls embed()")

    def chat(self, *, model_alias, messages, max_output_tokens, timeout_seconds):
        self.chat_calls.append({"model_alias": model_alias})
        return TransportResult(content=self._chat_result or "{}", dimensions=None, token_usage=None)


def _repair_gateway(chat_result: str) -> tuple[ModelGateway, FakeTransport]:
    transport = FakeTransport(chat_result=chat_result)
    return ModelGateway(_make_config(), transport), transport


class _MustNotBeCalledTransport:
    """Fails the test immediately if the Model Gateway is ever called -
    for fixtures that must resolve without any repair attempt."""

    def embed(self, *, model_alias, texts, timeout_seconds):
        raise AssertionError("this fixture must not call the Model Gateway")

    def chat(self, *, model_alias, messages, max_output_tokens, timeout_seconds):
        raise AssertionError("this fixture must not call the Model Gateway")


def _no_repair_gateway() -> ModelGateway:
    return ModelGateway(_make_config(), _MustNotBeCalledTransport())


# ═══════════════════════════════════════════════════════════════════════
# D04-01..D04-10 — table-driven pass through validate_full() (no repair)
# ═══════════════════════════════════════════════════════════════════════

# (case_id, outcome, category) - outcome is "valid" or the expected
# ValidationFailure.stage; category is the expected ValidationFailure.category
# (None for "valid").
NON_REPAIR_CASES = [
    ("D04-01", "valid", None),  # valid_first_pass
    ("D04-02", "parse", "malformed_json"),  # malformed_json
    ("D04-03", "valid", None),  # markdown_wrapped_json - documented bounded unwrap
    ("D04-04", "contract", "missing_field"),  # missing_required_field
    ("D04-05", "contract", "extra_field"),  # extra_field
    ("D04-06", "contract", "wrong_type"),  # wrong_type
    ("D04-07", "contract", "invalid_enum"),  # invalid_enum
    ("D04-08", "contract", "out_of_range"),  # out_of_range_value
    ("D04-09", "semantic", "s1_answered_without_citation"),  # semantic_answered_without_citation
    ("D04-10", "semantic", "s2_insufficient_evidence_high_confidence"),  # semantic_insufficient_with_high_confidence
]


@pytest.mark.parametrize("case_id,outcome,category", NON_REPAIR_CASES, ids=[c[0] for c in NON_REPAIR_CASES])
def test_fixture_resolves_to_its_documented_outcome(case_id, outcome, category):
    case = FIXTURE_CASES[case_id]
    result = validate_full(case["raw"], CitedAnswer)

    if outcome == "valid":
        assert isinstance(result, CitedAnswer), f"{case_id} ({case['name']}) should validate"
    else:
        assert isinstance(result, ValidationFailure), f"{case_id} ({case['name']}) should be rejected"
        assert result.stage == outcome, f"{case_id}: expected stage {outcome!r}, got {result.stage!r}"
        assert result.category == category, f"{case_id}: expected category {category!r}, got {result.category!r}"


@pytest.mark.parametrize("case_id", ["D04-01", "D04-02", "D04-03"])
def test_valid_and_parse_failure_fixtures_never_touch_the_gateway_even_via_resolve(case_id):
    """A valid response needs no repair; a parse-stage failure is never
    repair-eligible (`repair.is_repairable`). Running these through the
    *full* `resolve()` pipeline (repair included) - not just
    `validate_full()` - still makes zero Model Gateway calls; the fake
    transport raises if that's ever violated."""
    result = resolve(FIXTURE_CASES[case_id]["raw"], CitedAnswer, _no_repair_gateway())
    if case_id == "D04-02":
        assert isinstance(result, ValidationFailure)
    else:
        assert isinstance(result, CitedAnswer)


# ═══════════════════════════════════════════════════════════════════════
# D04-11, D04-12 — the repair fixtures, through the full resolve() pipeline
# ═══════════════════════════════════════════════════════════════════════

def test_fixture_d04_11_repairable_invalid_response_resolves_successfully():
    case = FIXTURE_CASES["D04-11"]
    gateway, transport = _repair_gateway(case["fake_repair_response"])

    result = resolve(case["raw"], CitedAnswer, gateway)

    assert isinstance(result, CitedAnswer)
    assert len(transport.chat_calls) == 1


def test_fixture_d04_12_repair_still_invalid_resolves_to_typed_failure():
    case = FIXTURE_CASES["D04-12"]
    gateway, transport = _repair_gateway(case["fake_repair_response"])

    result = resolve(case["raw"], CitedAnswer, gateway)

    assert isinstance(result, ValidationFailure)
    assert len(transport.chat_calls) == 1  # repair capped at one attempt


# ═══════════════════════════════════════════════════════════════════════
# markdown-wrapped JSON: bounded unwrap only, never arbitrary prose
# ═══════════════════════════════════════════════════════════════════════

def test_markdown_fence_unwrap_never_accepts_surrounding_prose():
    """Task 5's explicit closing rule: 'Do not silently accept arbitrary
    prose around JSON.' D04-03 above proves the documented bounded unwrap
    (one ```json fence, nothing else). This proves the boundary: the
    exact same fenced content, with prose before/after it, is rejected."""
    prose_wrapped = (
        "Sure, here is the answer:\n"
        + FIXTURE_CASES["D04-03"]["raw"]
        + "\nLet me know if you need anything else!"
    )
    result = validate_full(prose_wrapped, CitedAnswer)
    assert isinstance(result, ValidationFailure)
    assert result.stage == "parse"


# ═══════════════════════════════════════════════════════════════════════
# coverage discipline: every supplied fixture is exercised somewhere above
# ═══════════════════════════════════════════════════════════════════════

def test_every_fixture_case_is_covered_by_this_suite():
    covered = {case_id for case_id, _, _ in NON_REPAIR_CASES} | {"D04-11", "D04-12"}
    assert covered == set(FIXTURE_CASES.keys()), (
        "a fixture case was added/removed in structured_output_cases.json "
        "without updating this suite's coverage table"
    )
