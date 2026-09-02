"""
Day 5 Task 6 — deterministic input policy.

Classifies a *normalized* user question (security/normalization.py runs
first - grounding_rules.md #9) into exactly one of:

    allow | clarify | block

Every rule here is pattern-based and deterministic - no LLM call, per the
working rule "do not use an LLM as the only policy classifier for these
required deterministic fixtures." The rule set is built directly from
tests/fixtures/day05/attacks/attack_fixtures.json and
data/day05_pack/expected_policy_outcomes.md and is exercised end to end against
all nine supplied fixtures in tests/test_day05_grounding.py.

This module classifies *user input* only. A malicious instruction sitting
inside *retrieved evidence* (Task 7 - poisoned documents) is a different
problem, solved by prompt_builder.py labelling evidence as untrusted data,
never by this policy.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class PolicyOutcome(str, Enum):
    ALLOW = "allow"
    CLARIFY = "clarify"
    BLOCK = "block"


@dataclass(frozen=True)
class PolicyDecision:
    outcome: PolicyOutcome
    category: str
    reason: str


# A quoted excerpt that the user explicitly frames as evidence data to
# reason about (ATK-009: "...says 'Ignore previous instructions'. Treat
# that text as evidence data only...") is allow, and is checked BEFORE the
# block rules below so a quoted attack phrase does not itself trip a block
# rule when the surrounding sentence is asking the system to treat it as
# inert data.
_QUOTED_AS_DATA_RE = re.compile(
    r"\btreat\b.{0,30}\bas evidence data only\b"
    r"|\bas (a )?(quoted|literal) (data|text)\b",
    re.IGNORECASE,
)

# Each block rule: (category, compiled pattern), checked in order. Targets
# the *behavior* each attack category names (override instructions,
# escalate role, extract the system prompt, coerce tool use, forge a
# citation) rather than single words a legitimate question could also use.
_BLOCK_RULES: tuple[tuple[str, re.Pattern], ...] = (
    (
        "instruction_override",
        re.compile(
            r"\bignore\b.{0,40}\b(previous|prior|all|above)\b.{0,20}\binstructions?\b"
            r"|\banswer (without|regardless of)\b.{0,20}\b(retrieved )?evidence\b",
            re.IGNORECASE,
        ),
    ),
    (
        "role_escalation",
        re.compile(
            r"\byou are now\b.{0,40}\b(admin|administrator|system|root|developer)\b"
            r"|\bact as (the |an? )?(system|admin|administrator)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "system_prompt_extraction",
        re.compile(
            r"\b(print|show|reveal|display|repeat)\b.{0,40}"
            r"\b(system prompt|hidden prompt|internal (policy|instructions))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "tool_coercion",
        re.compile(
            r"\b(use|invoke|call|run)\b.{0,40}\b(hidden|any|unauthorized)\b.{0,20}\btools?\b"
            r"|\beven if tools are not allowed\b",
            re.IGNORECASE,
        ),
    ),
    (
        "citation_forgery",
        re.compile(
            r"\bcite\b.{0,40}\banyway\b"
            r"|\bmake up\b.{0,20}\bcitation\b"
            r"|\binvent\b.{0,20}\b(a )?(chunk|citation|source)\b",
            re.IGNORECASE,
        ),
    ),
)

# Subjective/ambiguous questions that retrieval cannot ground (there is no
# fact called "is this supplier good") route to clarify rather than allow
# or block - ATK-008.
_CLARIFY_RE = re.compile(
    r"\bis\b.{0,15}\b(good|bad|best|great|trustworthy)\b"
    r"|\bwhether\b.{0,25}\b(good|bad|reliable|safe|trustworthy)\b",
    re.IGNORECASE,
)


def evaluate_policy(normalized_text: str) -> PolicyDecision:
    """`normalized_text` must already have passed through
    `security.normalization.normalize_input` - this function does not
    re-normalize, so obfuscated input evaluated here bypasses normalization
    by construction if a caller skips that step (see answer_service.py)."""
    if _QUOTED_AS_DATA_RE.search(normalized_text):
        return PolicyDecision(
            PolicyOutcome.ALLOW,
            "quoted_poisoned_text_as_data",
            "quoted text is explicitly framed by the user as evidence data, not instruction",
        )

    for category, pattern in _BLOCK_RULES:
        if pattern.search(normalized_text):
            return PolicyDecision(PolicyOutcome.BLOCK, category, f"matched blocked pattern category: {category}")

    if _CLARIFY_RE.search(normalized_text):
        return PolicyDecision(
            PolicyOutcome.CLARIFY,
            "ambiguous_request",
            "question is subjective/ambiguous and cannot be grounded without clarification",
        )

    return PolicyDecision(PolicyOutcome.ALLOW, "benign", "no blocked or ambiguous pattern matched")
