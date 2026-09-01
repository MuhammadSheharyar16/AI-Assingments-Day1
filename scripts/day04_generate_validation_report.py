"""
Day 4 Task 7 — validation report generator.

Run: python scripts/day04_generate_validation_report.py
(needs PYTHONPATH=src - see README Setup)

Runs the real Day 4 contract-layer code (`aico.contracts.repair.
validate_full`/`resolve` - never a separate, summarized reimplementation)
against every fixture in `data/day04_pack/fixtures/structured_output_
cases.json` and the compatibility check against `existing_caller_v1.json`,
then writes `artifacts/day04/validation_report.md` straight from those
results - not hand-transcribed, the same discipline Day 1/Day 2/Day 3's
generated reports use. Re-run this after any change to
`src/aico/contracts/` or the fixtures to regenerate a report that cannot
drift out of sync with what the code actually does.

Every case runs against a fake Model Gateway transport - never a real
network call, per the working rule "do not create avoidable cloud cost."
No raw fixture JSON is embedded in the report, only sanitized summaries
(stage, category, field path where relevant, and small structural facts
extracted programmatically for the one semantic-failure example) - per
"do not include secrets or complete unsafe model outputs."
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from aico.contracts.errors import ValidationFailure
from aico.contracts.models import (
    CITED_ANSWER_SCHEMA_VERSION,
    RESPONSE_ENVELOPE_SCHEMA_VERSION,
    CitedAnswer,
    ResponseEnvelope,
)
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
EXISTING_CALLER_PATH = REPO_ROOT / "data" / "day04_pack" / "fixtures" / "existing_caller_v1.json"
REPORT_PATH = REPO_ROOT / "artifacts" / "day04" / "validation_report.md"

SCHEMA_PATHS = [
    "contracts/schema/cited_answer.v1.schema.json",
    "contracts/schema/response_envelope.v1.schema.json",
]

# The one semantic rule this report singles out (Task 7's required
# "one schema-valid but semantically invalid example") - D04-09.
SEMANTIC_EXAMPLE_CASE_ID = "D04-09"


# ── fake gateway plumbing (same pattern as the test suite) ─────────────

def _make_config() -> GatewayConfig:
    return GatewayConfig(
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


class _FakeTransport:
    """Deterministic, in-memory Transport double - never touches the network."""

    def __init__(self, chat_result: str):
        self._chat_result = chat_result
        self.calls = 0

    def embed(self, *, model_alias, texts, timeout_seconds):
        raise AssertionError("report generation never calls embed()")

    def chat(self, *, model_alias, messages, max_output_tokens, timeout_seconds):
        self.calls += 1
        return TransportResult(content=self._chat_result, dimensions=None, token_usage=None)


def _gateway_for(chat_result: str) -> tuple[ModelGateway, _FakeTransport]:
    transport = _FakeTransport(chat_result)
    return ModelGateway(_make_config(), transport), transport


# ── run the real pipeline against every fixture ─────────────────────────

def _load_cases() -> list[dict]:
    return json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))["cases"]


def run_fixture_suite() -> list[dict]:
    """Runs each fixture through the real pipeline exactly the way
    tests/test_day04_broken_output_suite.py does: fixtures carrying a
    `fake_repair_response` (D04-11/D04-12) go through the full `resolve()`
    pipeline against a fake gateway wired to that response; every other
    fixture goes through `validate_full()` (parse -> contract -> semantic,
    no repair) - see that test file's module docstring for why."""
    rows = []
    for case in _load_cases():
        if "fake_repair_response" in case:
            gateway, transport = _gateway_for(case["fake_repair_response"])
            result = resolve(case["raw"], CitedAnswer, gateway)
            repair_attempts = transport.calls
        else:
            result = validate_full(case["raw"], CitedAnswer)
            repair_attempts = 0

        if isinstance(result, CitedAnswer):
            outcome, stage, category = "valid", None, None
        else:
            assert isinstance(result, ValidationFailure)
            outcome, stage, category = "failure", result.stage, result.category

        rows.append(
            {
                "id": case["id"],
                "name": case["name"],
                "expected_stage": case["expected_stage"],
                "outcome": outcome,
                "stage": stage,
                "category": category,
                "repair_attempts": repair_attempts,
            }
        )
    return rows


def run_compatibility_check() -> dict:
    payload = json.loads(EXISTING_CALLER_PATH.read_text(encoding="utf-8"))["sample_response"]
    envelope = ResponseEnvelope.model_validate(payload)
    return {
        "passed": True,
        "warning_defaulted_to_none": envelope.warning is None,
        "schema_version": envelope.schema_version,
    }


def describe_semantic_example() -> str:
    """Builds the one required 'schema-valid but semantically invalid'
    example from small structural facts extracted from the fixture's raw
    JSON - never the raw text itself - per 'do not include secrets or
    complete unsafe model outputs.'"""
    cases = {c["id"]: c for c in _load_cases()}
    parsed = json.loads(cases[SEMANTIC_EXAMPLE_CASE_ID]["raw"])
    return (
        f"Fixture `{SEMANTIC_EXAMPLE_CASE_ID}` (`{cases[SEMANTIC_EXAMPLE_CASE_ID]['name']}`): "
        f"a response with `status=\"{parsed['status']}\"`, `{len(parsed['citations'])}` citation(s), "
        f"and `confidence_label=\"{parsed['confidence_label']}\"`. It is well-typed - every required "
        f"field present, every type and enum correct - so it **passes contract/schema validation** "
        f"and becomes a typed `CitedAnswer`. It still **fails semantic validation** under rule S1 "
        f"(`data/day04_pack/semantic_rules.md`): an `answered` response must carry at least one "
        f"citation, and this one carries none."
    )


# ── render markdown ─────────────────────────────────────────────────────

def _rows_table(rows: list[dict]) -> str:
    if not rows:
        return "_None._\n"
    header = "| ID | Name | Stage | Category |\n|---|---|---|---|\n"
    lines = [
        f"| {r['id']} | {r['name']} | {r['stage'] or '-'} | {r['category'] or '-'} |" for r in rows
    ]
    return header + "\n".join(lines) + "\n"


def render_report(rows: list[dict], compatibility: dict) -> str:
    valid_first_pass = [r for r in rows if r["outcome"] == "valid" and r["repair_attempts"] == 0]
    contract_schema_failures = [r for r in rows if r["outcome"] == "failure" and r["stage"] in ("parse", "contract")]
    semantic_failures = [r for r in rows if r["stage"] == "semantic"]
    repair_attempted = [r for r in rows if r["repair_attempts"] > 0]
    repair_successes = [r for r in repair_attempted if r["outcome"] == "valid"]
    final_failures = [r for r in rows if r["outcome"] == "failure"]

    non_repairable = [r for r in final_failures if r["stage"] == "parse"]
    not_exercised_for_repair = [
        r for r in final_failures if r["stage"] in ("contract", "semantic") and r["repair_attempts"] == 0
    ]
    repair_exhausted = [r for r in final_failures if r["repair_attempts"] > 0]

    lines: list[str] = []
    lines.append("# Day 4 Validation Report")
    lines.append("")
    lines.append(f"Generated {date.today().isoformat()} by `scripts/day04_generate_validation_report.py` "
                  f"against `data/day04_pack/fixtures/`. Every result below comes from running the real "
                  f"`aico.contracts` pipeline against these fixtures and a fake Model Gateway - no real "
                  f"network call is made generating this report.")
    lines.append("")

    lines.append("## Contract/schema version")
    lines.append("")
    lines.append(f"- `CitedAnswer.schema_version`: `{CITED_ANSWER_SCHEMA_VERSION}`")
    lines.append(f"- `ResponseEnvelope.schema_version`: `{RESPONSE_ENVELOPE_SCHEMA_VERSION}`")
    lines.append("")

    lines.append("## Generated schema paths")
    lines.append("")
    lines.append("Generated from the source Pydantic models by `scripts/day04_generate_schemas.py` "
                  "and committed (never hand-maintained):")
    lines.append("")
    for path in SCHEMA_PATHS:
        lines.append(f"- `{path}`")
    lines.append("")

    lines.append("## Fixture summary")
    lines.append("")
    lines.append(f"{len(rows)} cases from `structured_output_cases.json`, each run through the real pipeline "
                  f"(`validate_full()`, or `resolve()` for the two repair fixtures):")
    lines.append("")
    lines.append("| ID | Name | Expected stage | Outcome | Stage | Category | Repair calls |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in rows:
        lines.append(
            f"| {r['id']} | {r['name']} | {r['expected_stage']} | {r['outcome']} | "
            f"{r['stage'] or '-'} | {r['category'] or '-'} | {r['repair_attempts']} |"
        )
    lines.append("")

    lines.append("## Valid first-pass cases")
    lines.append("")
    lines.append("Passed the complete pipeline (parse -> contract/schema -> semantic) with zero repair calls:")
    lines.append("")
    for r in valid_first_pass:
        lines.append(f"- `{r['id']}` ({r['name']})")
    lines.append("")

    lines.append("## Contract/schema failures")
    lines.append("")
    lines.append("Rejected at the parse or contract/schema stage (Task 2), before semantic validation ever runs:")
    lines.append("")
    lines.append(_rows_table(contract_schema_failures))

    lines.append("## Semantic failures")
    lines.append("")
    lines.append("Passed contract/schema validation as a well-typed `CitedAnswer`, then rejected by a "
                  "semantic rule (Task 3):")
    lines.append("")
    lines.append(_rows_table(semantic_failures))

    lines.append("## Repair attempts")
    lines.append("")
    lines.append(f"{len(repair_attempted)} fixture(s) triggered a bounded repair call (exactly one Model "
                  f"Gateway call each, never more): " + ", ".join(f"`{r['id']}`" for r in repair_attempted) + ".")
    lines.append("")
    lines.append("The other contract/schema and semantic failures above (`D04-04`-`D04-10`) are "
                  "repair-*eligible* under `repair.is_repairable` (any `contract`/`semantic` stage failure "
                  "is), but this run exercises them through `validate_full()` only, matching "
                  "`tests/test_day04_broken_output_suite.py`: those fixtures carry no `fake_repair_response` "
                  "in `structured_output_cases.json`, so they exist to prove correct rejection at their "
                  "stage, not to exercise repair - the repair path itself is proven exhaustively by "
                  "`D04-11`/`D04-12` below. A `parse`-stage failure (`D04-02`) is never repair-eligible at "
                  "all - see `repair.py`'s module docstring.")
    lines.append("")

    lines.append("## Repair successes")
    lines.append("")
    if repair_successes:
        for r in repair_successes:
            lines.append(f"- `{r['id']}` ({r['name']}): invalid first response -> one repair call -> "
                          f"revalidated successfully as a typed `CitedAnswer`.")
    else:
        lines.append("_None._")
    lines.append("")

    lines.append("## Final failures")
    lines.append("")
    lines.append(f"{len(final_failures)} of {len(rows)} fixtures end as a typed failure after this run:")
    lines.append("")
    lines.append(f"- Non-repairable by policy (`stage=\"parse\"`, zero Model Gateway calls): "
                  + (", ".join(f"`{r['id']}`" for r in non_repairable) or "none") + ".")
    lines.append(f"- Repair-eligible but not exercised for repair in this fixture run (no "
                  f"`fake_repair_response` supplied): "
                  + (", ".join(f"`{r['id']}`" for r in not_exercised_for_repair) or "none") + ".")
    lines.append(f"- Repair attempted and still failed (repair capped at one call, never retried): "
                  + (", ".join(f"`{r['id']}`" for r in repair_exhausted) or "none") + ".")
    lines.append("")

    lines.append("## Compatibility test result")
    lines.append("")
    if compatibility["passed"]:
        lines.append(f"**Passed.** `data/day04_pack/fixtures/existing_caller_v1.json` (a v1 caller snapshot "
                      f"that never sends `warning`) still validates against the current `ResponseEnvelope` "
                      f"(`schema_version=\"{compatibility['schema_version']}\"`), with `warning` defaulting "
                      f"to `None`. See `docs/adr/ADR-004-day4-contract-versioning.md` for the full "
                      f"backward-compatibility policy and the breaking-change examples it documents, each "
                      f"proven in `tests/test_day04_compatibility.py`.")
    else:  # pragma: no cover - would only fire on a real regression
        lines.append(f"**Failed.** {compatibility['error']}")
    lines.append("")

    lines.append("## Schema-valid but semantically invalid example")
    lines.append("")
    lines.append(describe_semantic_example())
    lines.append("")

    return "\n".join(lines) + "\n"


def main() -> None:
    rows = run_fixture_suite()
    compatibility = run_compatibility_check()
    report = render_report(rows, compatibility)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"wrote {REPORT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
