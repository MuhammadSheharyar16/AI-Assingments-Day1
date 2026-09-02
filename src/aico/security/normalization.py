"""
Day 5 Task 5 — input normalization.

Normalizes the small, bounded set of obfuscation techniques used by the
supplied attack corpus (data/day05_pack/attacks/attack_fixtures.json, see
ATK-006) before the deterministic input policy (input_policy.py) ever
looks at the text. This is deliberately not a general-purpose text
cleaner - see grounding_rules.md #9 ("normalize supported obfuscations
before deterministic policy evaluation") and the working rule that
normalization must be deterministic and bounded to the supplied scope.

Two obfuscations are handled:
1. Unicode compatibility tricks - full-width characters, other NFKC-
   foldable forms, and zero-width characters (U+200B etc.) sometimes
   inserted mid-word to dodge a keyword match.
2. Character-spaced words ("I G N O R E") - collapsed back into "IGNORE"
   so a keyword rule downstream sees the intended word.

An ordinary benign question with none of these patterns is returned
unchanged (module docstring / working rule: "normal benign questions must
not be needlessly rewritten") - NFKC normalization and whitespace
collapsing are no-ops on already-plain, single-spaced ASCII text.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Zero-width / invisible characters sometimes inserted mid-word to dodge
# keyword matching. Written as explicit \u escapes rather than literal
# invisible characters - literal zero-width characters pasted into source
# are themselves impossible for a reviewer or a diff to verify by eye.
#   U+200B zero-width space       U+200C zero-width non-joiner
#   U+200D zero-width joiner      U+2060 word joiner
#   U+FEFF zero-width no-break space (BOM)
_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")

# Collapses runs of single letters separated by whitespace ("I G N O R E")
# back into one word ("IGNORE"). Requires at least FOUR single-letter
# "tokens" in a row - long enough to catch the obfuscated keywords the
# supplied corpus actually uses (ATK-006's "IGNORE" is 6), short enough
# that a benign three-letter enumeration ("options are a b c") is never
# rewritten. Bounded to the obfuscation shape the corpus demonstrates,
# not a general "detect any spaced-out word" heuristic.
_SPACED_LETTERS_RE = re.compile(r"\b(?:[A-Za-z]\s+){3,}[A-Za-z]\b")

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class NormalizationResult:
    """`applied` names which rules actually fired - empty for an
    already-clean benign question, so a caller/test can assert "nothing
    was rewritten" without string-diffing."""

    original: str
    normalized: str
    applied: tuple[str, ...]


def _collapse_spaced_letters(text: str) -> tuple[str, bool]:
    applied = False

    def _replace(match: re.Match) -> str:
        nonlocal applied
        applied = True
        return "".join(match.group(0).split())

    result = _SPACED_LETTERS_RE.sub(_replace, text)
    return result, applied


def normalize_input(text: str) -> NormalizationResult:
    """Deterministic, bounded normalization pipeline. Order matters: NFKC
    and zero-width stripping run first so a spaced-out word hidden behind
    invisible characters still collapses correctly, and whitespace
    collapsing runs last so it also cleans up spacing left behind by the
    letter-collapse step."""
    applied: list[str] = []

    working = unicodedata.normalize("NFKC", text)
    if working != text:
        applied.append("unicode_nfkc")

    stripped = _ZERO_WIDTH_RE.sub("", working)
    if stripped != working:
        applied.append("zero_width_strip")
    working = stripped

    collapsed, spaced_applied = _collapse_spaced_letters(working)
    if spaced_applied:
        applied.append("spaced_letter_collapse")
    working = collapsed

    squeezed = _WHITESPACE_RE.sub(" ", working).strip()
    if squeezed != working.strip():
        applied.append("whitespace_collapse")
    working = squeezed

    return NormalizationResult(original=text, normalized=working, applied=tuple(applied))
