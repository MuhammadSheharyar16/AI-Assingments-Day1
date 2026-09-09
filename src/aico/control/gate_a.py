"""
Day 9 Task 3 -- Gate-A: deterministic domain/intent classification before
lane selection.

"Gate-A determines whether the request maps to a governed domain/intent
before lane selection" (Day 9 assignment). `GateA.classify()` never
guesses and never invents: every non-`BLOCKED` decision is derived only
from the governed `Concept`/`Intent` records `OntologyRegistry` (Task 2)
exposes, and `BLOCKED` is derived only from Day 5's existing, already-
deterministic input policy (`aico.security.input_policy.evaluate_policy`)
-- Gate-A adds no new permission/PII/disclosure checking of its own
(Day 9 rule: "Gate-A is not permission/PII/disclosure checking. Those
belong to Gate-B on a later day").

Classification runs in two tiers, cheapest and most certain first:

  Tier 0 -- blocked.  The normalized input is run through Day 5's own
  `evaluate_policy`. A `block` outcome short-circuits everything below it
  with `GateAStatus.BLOCKED` -- matches `gate_a_cases.json`'s GA-006
  ("blocked_input") being classified directly by Gate-A, without needing
  the full request pipeline (Task 9) wired up around it.

  Tier 1 -- exact governed phrase.  The normalized input's *full* token
  sequence is compared against every active intent's own governed
  `phrases` (`gate_a_cases.json` GA-001, "exact_policy_intent"). An exact
  match is unambiguous by construction (the pack's fixture uses no
  duplicate phrases across intents) and needs no scoring at all.

  Tier 2 -- governed content-term overlap.  For every active intent, each
  of its governed phrases is expanded into a "vocabulary": the phrase's
  own content words (stopwords removed), unioned with the content words of
  every active concept whose `name` is a substring of that phrase (so a
  concept's own governed `synonyms` become recognizable stand-ins for its
  canonical name inside that phrase -- GA-002, "registered_synonym":
  "vendor payment window" resolves to `INT-POLICY-QUESTION` via
  `CON-SUPPLIER`'s synonym "vendor" and `CON-PAYMENT-TERMS`'s synonym
  "payment window", both folded into the vocabulary built from that
  intent's own phrase "what are the payment terms"). Each intent's score
  is the largest word-overlap count between the normalized input and any
  one of its own phrase-vocabularies.

  A score must reach `_MIN_OVERLAP_SCORE` (2) to count as a candidate
  match at all -- a single incidentally-shared word is not evidence of a
  governed match. This is what correctly keeps GA-005
  ("unknown_supplier_intent", "Predict Supplier Alpha's stock price next
  year.") `UNSUPPORTED`: the word "Supplier" is present, but it is the
  *only* governed word shared with any intent's vocabulary (nothing about
  "predict"/"stock price"/"next year" is governed at all) -- one shared
  word is exactly the kind of coincidental overlap the threshold exists to
  reject, distinct from GA-002's two shared words ("vendor", "payment
  window" -> "payment"/"window") or GA-004's zero. Below the threshold for
  every intent -> `UNSUPPORTED`, never a synthesized fallback intent
  (Day 9 rule: "Unknown/unsupported requests do not silently route to
  RAG"). Exactly one intent at or above the threshold, strictly higher
  than every other -> `MATCHED`. Two or more intents tied at the same
  (qualifying) top score -> `AMBIGUOUS`, with all of them listed in
  `candidate_intents` (`ambiguity_cases.json` AMB-001, "Show me the
  supplier information." -- both `INT-POLICY-QUESTION` and
  `INT-STRUCTURED-LOOKUP` share the one governed word "supplier").

`GateADecision.matched_concepts`, for a `MATCHED`/`AMBIGUOUS` decision, is
every active governed concept whose `name` or a `synonym` is a substring
of the normalized input -- independent of which phrase actually won.
This is deliberately broader than "just the concept(s) behind the winning
phrase": Task 4's "multiple known concepts" case is GA-002 itself, whose
input ("What is the vendor payment window?") references *two* governed
concepts at once (`CON-SUPPLIER` via "vendor", `CON-PAYMENT-TERMS` via
"payment window") even though it resolves to exactly one governed intent
-- `matched_concepts` reports both, `intent_id` still reports only the
one intent Tier 2's scoring actually picked.

Only `status="active"` domains/concepts/intents are ever eligible to match
(`LifecycleStatus`, Task 1) -- a deprecated/retired entry stops being
something new language can resolve onto, without needing to be deleted out
from under anything that still references it historically.

What this module deliberately does NOT do: it never performs its own
free-form fuzzy/semantic matching beyond the two deterministic tiers
above, it never calls the Model Gateway (that is Task 4's optional
model-assisted interpreter, layered *on top of* this deterministic
classifier, never replacing it), and it never decides which lane a
`MATCHED`/`AMBIGUOUS`/`UNSUPPORTED`/`BLOCKED` decision routes to (Task 5).
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from aico.control.models import GateADecision, GateAStatus
from aico.control.ontology import Concept, Intent, LifecycleStatus
from aico.control.ontology_registry import OntologyRegistry
from aico.security.input_policy import PolicyDecision, PolicyOutcome, evaluate_policy
from aico.security.normalization import normalize_input

# Same shape as `aico.rag.answer_service.PolicyEvaluator` (Day 5/6) -
# defined again here, not imported from `aico.rag`, so the control plane
# (a stage that runs *before* RAG in the pipeline: Gate-A -> lane selector
# -> RAG/Mode B) never depends on the RAG package for a bare type alias.
PolicyEvaluator = Callable[[str], PolicyDecision]

# A single overlapping governed word is not evidence of a governed match
# (see GA-005 in the module docstring) -- at least two independent shared
# content words are required before an intent counts as a candidate at all.
_MIN_OVERLAP_SCORE = 2

# Low-content function/question words that appear across multiple governed
# phrases and would otherwise create false-positive overlap (GA-004: "what
# is ..." sharing "what"/"is" with "what is the invoice policy" despite
# being about the weather, not invoice policy). Deliberately narrow --
# action/content words that happen to recur across several intents' own
# phrases ("show", "list", "lookup", "help", ...) are kept, since the
# scoring itself (not a stopword list) is what disambiguates between them.
_STOPWORDS = frozenset({"a", "an", "the", "of", "in", "on", "to", "about", "is", "are", "what", "me", "it", "its"})

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> tuple[str, ...]:
    """Lowercase, punctuation/whitespace-insensitive tokenization shared by
    every tier below -- deliberately just alnum runs, no stemming/lemmas:
    the registry's own governed phrasing is the vocabulary, not a general
    NLP pipeline."""
    return tuple(_TOKEN_RE.findall(text.lower()))


def _content_tokens(tokens: tuple[str, ...]) -> frozenset[str]:
    return frozenset(t for t in tokens if t not in _STOPWORDS)


@dataclass(frozen=True)
class _PhraseVocabulary:
    """One governed phrase's expanded, scoreable vocabulary (Tier 2)."""

    intent_id: str
    phrase: str
    content_tokens: frozenset[str]
    # concept_ids whose name is a substring of `phrase` -- what makes this
    # phrase's vocabulary richer than the phrase's own words alone, and
    # what `matched_concepts`/ambiguity provenance is drawn from.
    related_concept_ids: tuple[str, ...]


@dataclass
class GateA:
    """Gate-A: deterministic domain/intent classification, built once
    against a loaded `OntologyRegistry` (Task 2) and reused for every
    request. `classify()` is the only public entry point -- see the
    module docstring for the two-tier algorithm it runs.

    `policy_evaluator` defaults to Day 5's real `evaluate_policy`,
    injectable for tests -- the same swappable-dependency shape
    `GroundedAnswerService` (`rag/answer_service.py`) already uses for the
    identical reason (Day 6 Task 10)."""

    registry: OntologyRegistry
    policy_evaluator: PolicyEvaluator = evaluate_policy
    _phrase_vocabularies: tuple[_PhraseVocabulary, ...] = field(init=False, repr=False)
    _active_concepts: tuple[Concept, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        active_concepts = [c for c in self.registry.concepts if c.status is LifecycleStatus.ACTIVE]
        self._active_concepts = tuple(active_concepts)
        self._phrase_vocabularies = tuple(
            self._build_phrase_vocabulary(intent, phrase, active_concepts)
            for intent in self.registry.intents
            if intent.status is LifecycleStatus.ACTIVE
            for phrase in intent.phrases
        )

    @staticmethod
    def _build_phrase_vocabulary(intent: Intent, phrase: str, active_concepts: list[Concept]) -> _PhraseVocabulary:
        phrase_lower = phrase.lower()
        related = [c for c in active_concepts if c.name.lower() in phrase_lower]
        vocab = set(_content_tokens(_tokenize(phrase)))
        for concept in related:
            for synonym in concept.synonyms:
                vocab |= _content_tokens(_tokenize(synonym))
        return _PhraseVocabulary(
            intent_id=intent.intent_id,
            phrase=phrase,
            content_tokens=frozenset(vocab),
            related_concept_ids=tuple(c.concept_id for c in related),
        )

    def _recognized_concept_ids(self, normalized_lower: str) -> list[str]:
        """Every active governed concept whose `name` or a `synonym` is a
        substring of the (already-lowercased) normalized input -- see the
        module docstring's "multiple known concepts" paragraph. Registry
        order, so the result is deterministic regardless of dict/set
        iteration order."""
        return [
            concept.concept_id
            for concept in self._active_concepts
            if any(term.lower() in normalized_lower for term in [concept.name, *concept.synonyms])
        ]

    # ── Public entry point ──────────────────────────────────────────

    def classify(self, text: str) -> GateADecision:
        """Classify one piece of user-facing request text into a typed
        `GateADecision`. Never raises for ordinary input -- every outcome,
        including "nothing governed matched" and "this is a blocked
        request", is a normal, typed result (Day 9 rule: fail closed with
        a typed result, never a fall-through)."""
        version = self.registry.ontology_version

        normalized = normalize_input(text).normalized
        policy = self.policy_evaluator(normalized)
        if policy.outcome is PolicyOutcome.BLOCK:
            return GateADecision(
                status=GateAStatus.BLOCKED,
                reason_code=f"policy_blocked_{policy.category}",
                ontology_version=version,
            )

        recognized_concepts = self._recognized_concept_ids(normalized.lower())
        input_tokens = _tokenize(normalized)

        exact = self._match_exact_phrase(input_tokens)
        if exact is not None:
            intent = self.registry.get_intent(exact.intent_id)
            return GateADecision(
                status=GateAStatus.MATCHED,
                domain=intent.domain,
                intent_id=intent.intent_id,
                matched_concepts=recognized_concepts,
                reason_code="exact_phrase_match",
                ontology_version=version,
            )

        return self._match_by_overlap(input_tokens, recognized_concepts, version)

    # ── Tier 1: exact governed phrase ───────────────────────────────

    def _match_exact_phrase(self, input_tokens: tuple[str, ...]) -> _PhraseVocabulary | None:
        for vocab in self._phrase_vocabularies:
            if _tokenize(vocab.phrase) == input_tokens:
                return vocab
        return None

    # ── Tier 2: governed content-term overlap ───────────────────────

    def _match_by_overlap(
        self, input_tokens: tuple[str, ...], recognized_concepts: list[str], version: str
    ) -> GateADecision:
        input_content = _content_tokens(input_tokens)

        best_per_intent: dict[str, int] = {}
        for vocab in self._phrase_vocabularies:
            score = len(vocab.content_tokens & input_content)
            if score > best_per_intent.get(vocab.intent_id, -1):
                best_per_intent[vocab.intent_id] = score

        qualifying = {
            intent_id: score for intent_id, score in best_per_intent.items() if score >= _MIN_OVERLAP_SCORE
        }
        if not qualifying:
            return GateADecision(
                status=GateAStatus.UNSUPPORTED,
                reason_code="no_governed_match",
                ontology_version=version,
            )

        top_score = max(qualifying.values())
        winning_intent_ids = {intent_id for intent_id, score in qualifying.items() if score == top_score}

        if len(winning_intent_ids) > 1:
            # Registry order, not set-iteration order, for a deterministic
            # `candidate_intents` list every time.
            candidate_intent_ids = [i.intent_id for i in self.registry.intents if i.intent_id in winning_intent_ids]
            return GateADecision(
                status=GateAStatus.AMBIGUOUS,
                matched_concepts=recognized_concepts,
                candidate_intents=candidate_intent_ids,
                reason_code="ambiguous_multiple_intents",
                ontology_version=version,
            )

        (winning_intent_id,) = winning_intent_ids
        intent = self.registry.get_intent(winning_intent_id)
        return GateADecision(
            status=GateAStatus.MATCHED,
            domain=intent.domain,
            intent_id=intent.intent_id,
            matched_concepts=recognized_concepts,
            reason_code="concept_synonym_match",
            ontology_version=version,
        )
