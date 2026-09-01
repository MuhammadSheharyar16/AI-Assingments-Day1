"""
Day 4 Task 3 — semantic validation.

Contract/schema validation (`validator.py`) checks *shape*: required
fields present, types correct, enums valid, extra fields rejected. A
response can pass all of that and still be application-nonsense - e.g. an
`answered` response with zero citations is a perfectly well-typed
`CitedAnswer`, but nothing backs the claim. That's what this module
checks, strictly *after* contract/schema validation has already
succeeded - see `data/day04_pack/semantic_rules.md` for the five
deterministic rules below (S1-S5). It does not verify a citation is
actually grounded in retrieved evidence - that's Day 5.

`validate_semantic` takes an already-typed `CitedAnswer` (never a raw
dict/string - if it hasn't passed `validate_contract` yet, it has no
business here) and returns either that *same* object unchanged or a typed
`ValidationFailure` (`stage="semantic"`), so a caller can tell a semantic
rejection apart from a contract/schema one (`stage="contract"` or
`"parse"`) by `result.stage` alone, without needing a second type.
Deliberately never mutates or "fixes" the input to make it pass - a
semantically invalid response stays invalid; it is never silently
coerced into a valid one.
"""
from __future__ import annotations

from aico.contracts.errors import ValidationFailure
from aico.contracts.models import AnswerStatus, CitedAnswer, ConfidenceLabel

# Lab convention (semantic_rules.md, rule S5): this deterministic marker
# is how the Day 4 lab ties answer text to status without needing real
# grounding/insufficiency reasoning - that belongs to Day 5.
INSUFFICIENT_EVIDENCE_PREFIX = "INSUFFICIENT_EVIDENCE"


def _rule_s1(answer: CitedAnswer) -> ValidationFailure | None:
    """S1 — an `answered` response needs at least one citation."""
    if answer.status is AnswerStatus.ANSWERED and len(answer.citations) == 0:
        return ValidationFailure(
            stage="semantic",
            category="s1_answered_without_citation",
            message="status is 'answered' but citations is empty",
            field_path="citations",
        )
    return None


def _rule_s2(answer: CitedAnswer) -> ValidationFailure | None:
    """S2 — an `insufficient_evidence` response must not claim high confidence."""
    if answer.status is AnswerStatus.INSUFFICIENT_EVIDENCE and answer.confidence_label is ConfidenceLabel.HIGH:
        return ValidationFailure(
            stage="semantic",
            category="s2_insufficient_evidence_high_confidence",
            message="status is 'insufficient_evidence' but confidence_label is 'high'",
            field_path="confidence_label",
        )
    return None


def _rule_s3(answer: CitedAnswer) -> ValidationFailure | None:
    """S3 — citation chunk_ids must be unique within the response."""
    seen: set[str] = set()
    for index, citation in enumerate(answer.citations):
        if citation.chunk_id in seen:
            return ValidationFailure(
                stage="semantic",
                category="s3_duplicate_citation",
                message=f"duplicate chunk_id {citation.chunk_id!r} in citations",
                field_path=f"citations.{index}.chunk_id",
            )
        seen.add(citation.chunk_id)
    return None


def _rule_s4(answer: CitedAnswer) -> ValidationFailure | None:
    """S4 — an `insufficient_evidence` response must carry no citations."""
    if answer.status is AnswerStatus.INSUFFICIENT_EVIDENCE and len(answer.citations) > 0:
        return ValidationFailure(
            stage="semantic",
            category="s4_insufficient_evidence_with_citations",
            message="status is 'insufficient_evidence' but citations is not empty",
            field_path="citations",
        )
    return None


def _rule_s5(answer: CitedAnswer) -> ValidationFailure | None:
    """S5 — answer text must agree with status, via the deterministic
    INSUFFICIENT_EVIDENCE prefix convention."""
    starts_with_marker = answer.answer.startswith(INSUFFICIENT_EVIDENCE_PREFIX)
    if answer.status is AnswerStatus.ANSWERED and starts_with_marker:
        return ValidationFailure(
            stage="semantic",
            category="s5_answer_status_mismatch",
            message=f"status is 'answered' but answer begins with {INSUFFICIENT_EVIDENCE_PREFIX!r}",
            field_path="answer",
        )
    if answer.status is AnswerStatus.INSUFFICIENT_EVIDENCE and not starts_with_marker:
        return ValidationFailure(
            stage="semantic",
            category="s5_answer_status_mismatch",
            message=f"status is 'insufficient_evidence' but answer does not begin with {INSUFFICIENT_EVIDENCE_PREFIX!r}",
            field_path="answer",
        )
    return None


# Evaluated in this fixed S1..S5 order so the same input always reports
# the same first violation - deterministic, per the working rule "Tests
# must be deterministic." A response failing more than one rule at once
# is still possible (e.g. answered + no citation + wrong-prefixed text);
# only the first-in-order failure is reported, mirroring validate_contract's
# same "first error, not all errors" convention in validator.py.
_RULES = (_rule_s1, _rule_s2, _rule_s3, _rule_s4, _rule_s5)


def validate_semantic(answer: CitedAnswer) -> CitedAnswer | ValidationFailure:
    """Run every semantic rule (S1-S5) against an already contract-valid
    `CitedAnswer`. Returns the *same* object unchanged on success - never
    a copy, never a repaired/mutated version - or the first violated rule
    as a typed `ValidationFailure` (`stage="semantic"`) on failure."""
    for rule in _RULES:
        failure = rule(answer)
        if failure is not None:
            return failure
    return answer
