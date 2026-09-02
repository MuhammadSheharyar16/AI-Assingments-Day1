"""
Day 5 Task 8 — attack fixture suite report generator.

Run: python scripts/day05_generate_attack_report.py
(needs PYTHONPATH=src - see README Setup, or `uv run python scripts/...`)

Runs the real Day 5 security pipeline - `aico.security.normalization.
normalize_input` then `aico.security.input_policy.evaluate_policy`, the
exact two calls `GroundedAnswerService.answer()` makes - against every
fixture in `day05_pack/attacks/attack_fixtures.json`, then writes
`artifacts/day05/attack_results.md` straight from those results, the same
discipline the Day 4 validation-report generator uses (never a separate,
hand-summarized reimplementation of the policy logic).

Each fixture is also run through the full `GroundedAnswerService` (Task 1)
against a fake Model Gateway - never a real network call, per the working
rule "do not create avoidable cloud cost" - to prove the wiring, not just
the policy function in isolation: a `block`/`clarify` fixture must reach
zero gateway calls, an `allow` fixture must reach exactly one.

No secrets or production/customer data appear here - every fixture is
synthetic text already committed in `day05_pack/attacks/attack_fixtures.json`
(the resource pack's own README: "Synthetic data only"). Per
grounding_rules.md and the working rule "do not claim universal jailbreak
prevention," this report documents a fixed, deterministic corpus - it is
not a claim of general jailbreak resistance.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from aico.platform.model_gateway import CallMetadata, ChatRequest, ChatResult
from aico.rag.answer_service import Blocked, Clarify, GroundedAnswerService, InsufficientEvidence
from aico.rag.citation_validator import EvidenceChunk
from aico.security.input_policy import evaluate_policy
from aico.security.normalization import normalize_input

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_PATH = REPO_ROOT / "day05_pack" / "attacks" / "attack_fixtures.json"
REPORT_PATH = REPO_ROOT / "artifacts" / "day05" / "attack_results.md"

# The brief's required attack-category list (Task 6), mapped onto the
# fixture-pack category label that satisfies it - see
# tests/test_day05_input_policy.py for the same mapping and why
# "poisoned retrieved document" maps to the quoted-as-data fixture rather
# than having a fixture of its own (that defense is structural - Task 2/7 -
# not something input policy classifies).
REQUIRED_CATEGORIES = {
    "instruction override": "instruction_override",
    "role escalation": "role_escalation",
    "poisoned retrieved document": "quoted_poisoned_text_as_data",
    "citation forgery": "citation_forgery",
    "tool coercion": "tool_coercion",
    "system-prompt extraction": "system_prompt_extraction",
    "obfuscated instruction override": "obfuscated_override",
    "benign ambiguous request requiring clarification": "ambiguous_request",
}


# ── fake gateway plumbing (same pattern as the test suite) ──────────────

class _FakeGateway:
    """Always answers `insufficient_evidence` (schema-valid, zero
    citations) - sufficient to prove the wiring (did the gateway get
    called at all?) without needing a per-fixture scripted answer."""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, request: ChatRequest) -> ChatResult:
        self.calls += 1
        content = json.dumps(
            {
                "schema_version": "1.0",
                "status": "insufficient_evidence",
                "answer": "Report-generation fixture run: no real evidence was retrieved for this check.",
                "citations": [],
                "confidence_label": "low",
            }
        )
        return ChatResult(
            content=content,
            metadata=CallMetadata(
                operation="chat", model_alias="report-fake-alias", latency_ms=0.1, retry_count=0,
                token_usage=None, budget_status="within_budget",
            ),
        )


def _empty_retriever(query: str) -> list[EvidenceChunk]:
    return []


# ── run the real pipeline against every fixture ──────────────────────────

@dataclass(frozen=True)
class FixtureRow:
    id: str
    category: str
    input_text: str
    expected: str
    actual: str
    policy_category: str
    passed: bool
    gateway_calls: int
    reason: str


def _load_fixtures() -> list[dict]:
    return json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))["fixtures"]


def run_fixture_suite() -> list[FixtureRow]:
    rows: list[FixtureRow] = []
    for fixture in _load_fixtures():
        normalized = normalize_input(fixture["input"])
        decision = evaluate_policy(normalized.normalized)
        passed = decision.outcome.value == fixture["expected"]

        gateway = _FakeGateway()
        service = GroundedAnswerService(gateway=gateway, retriever=_empty_retriever)
        result = service.answer(fixture["input"])
        # Sanity cross-check: the full service's short-circuit behavior
        # must agree with the standalone policy call above.
        if decision.outcome.value == "block":
            assert isinstance(result, Blocked) and gateway.calls == 0
        elif decision.outcome.value == "clarify":
            assert isinstance(result, Clarify) and gateway.calls == 0
        else:
            assert isinstance(result, InsufficientEvidence) and gateway.calls == 1

        reason = "" if passed else (
            f"expected {fixture['expected']!r} but policy returned {decision.outcome.value!r} "
            f"(category={decision.category!r}, reason={decision.reason!r})"
        )
        rows.append(
            FixtureRow(
                id=fixture["id"],
                category=fixture["category"],
                input_text=fixture["input"],
                expected=fixture["expected"],
                actual=decision.outcome.value,
                policy_category=decision.category,
                passed=passed,
                gateway_calls=gateway.calls,
                reason=reason,
            )
        )
    return rows


# ── render markdown ──────────────────────────────────────────────────────

def render_report(rows: list[FixtureRow]) -> str:
    fixture_categories = {r.category for r in rows}
    failures = [r for r in rows if not r.passed]

    lines: list[str] = []
    lines.append("# Day 5 Attack Fixture Results")
    lines.append("")
    lines.append(
        f"Generated {date.today().isoformat()} by `scripts/day05_generate_attack_report.py` against "
        f"`day05_pack/attacks/attack_fixtures.json`. Every row runs the real "
        f"`aico.security.normalization.normalize_input` -> `aico.security.input_policy.evaluate_policy` "
        f"pipeline, cross-checked against the full `GroundedAnswerService` (Task 1) wired to a fake Model "
        f"Gateway - no real network call is made generating this report."
    )
    lines.append("")
    lines.append(
        "**Scope note** (`day05_pack/README.md`, `grounding_rules.md`): this is a fixed, deterministic "
        "corpus. Passing it does not imply universal jailbreak prevention."
    )
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append(f"{len(rows)} fixtures, {len(rows) - len(failures)} passed, {len(failures)} failed.")
    lines.append("")

    lines.append("## Fixture results")
    lines.append("")
    lines.append("| ID | Category | Input | Expected | Actual | Gateway calls | Pass/Fail |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in rows:
        status = "PASS" if r.passed else "**FAIL**"
        input_cell = r.input_text.replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {r.id} | {r.category} | {input_cell} | {r.expected} | {r.actual} | {r.gateway_calls} | {status} |"
        )
    lines.append("")

    lines.append("## Failures")
    lines.append("")
    if failures:
        for r in failures:
            lines.append(f"- `{r.id}` ({r.category}): {r.reason}")
    else:
        lines.append("_None._")
    lines.append("")

    lines.append("## Required category coverage")
    lines.append("")
    lines.append("Task 6's required attack corpus, mapped onto the fixture category that satisfies it "
                  "(`tests/test_day05_input_policy.py::test_all_required_attack_categories_are_represented_"
                  "in_the_pack` proves this same mapping in code):")
    lines.append("")
    lines.append("| Required category | Fixture category | Covered |")
    lines.append("|---|---|---|")
    for required, mapped in REQUIRED_CATEGORIES.items():
        covered = "yes" if mapped in fixture_categories else "**NO**"
        lines.append(f"| {required} | `{mapped}` | {covered} |")
    lines.append("")
    lines.append(
        "\"Poisoned retrieved document\" has no fixture of its own here because input policy classifies "
        "*user input*, not retrieved evidence - that defense is structural (evidence is always labelled "
        "untrusted data in the prompt, Task 2) and is proven separately in "
        "`tests/test_day05_poisoned_documents.py` (Task 7). The closest input-level analog, a user quoting "
        "poisoned-looking text and explicitly asking it to be treated as data, is `ATK-009`."
    )
    lines.append("")

    lines.append("## Wiring cross-check")
    lines.append("")
    lines.append(
        "For every fixture above, the full `GroundedAnswerService` pipeline was also run (fake Model Gateway, "
        "empty retrieval) and asserted to agree with the standalone policy call: a `block`/`clarify` outcome "
        "reaches the Model Gateway **zero** times (policy short-circuits before retrieval, prompt-building, or "
        "any model call), and an `allow` outcome reaches it **exactly once**. This run raised no assertion "
        "failure, so that agreement holds for all {n} fixtures.".format(n=len(rows))
    )
    lines.append("")

    lines.append("## Policy classifier")
    lines.append("")
    lines.append(
        "`evaluate_policy` is pure pattern-matching (`aico.security.input_policy`) - no Model Gateway call, no "
        "LLM in the loop for any fixture above, per the working rule \"do not use an LLM as the only policy "
        "classifier for these required deterministic fixtures.\""
    )
    lines.append("")

    return "\n".join(lines) + "\n"


def main() -> None:
    rows = run_fixture_suite()
    report = render_report(rows)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    failed = [r for r in rows if not r.passed]
    print(f"wrote {REPORT_PATH.relative_to(REPO_ROOT)} ({len(rows)} fixtures, {len(failed)} failed)")


if __name__ == "__main__":
    main()
