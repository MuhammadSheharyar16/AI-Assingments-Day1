"""
Day 9 Task 2 -- the ontology registry: loads the committed Mode-A
ontology document (Task 1's `OntologyDocument`) and exposes it read-only
for the rest of the control plane.

Responsibilities (`ontology_requirements.md`; Day 9 assignment, TASK 2):
    - load committed registry
    - validate it
    - expose read-only lookups
    - resolve concepts/intents
    - expose active ontology version

`OntologyRegistry` is the only thing in `aico.control` that ever reads
`ontology/registry.v1.json` off disk -- the same "one place deserializes
this" convention Day 4/Day 8 already use (`contracts/validator.py`,
`memory/store.py`). Gate-A (Task 3/4) and the lane selector (Task 5) are
built against this class's read-only surface, never against a raw dict or
the JSON file directly.

Read-only at runtime (Day 9 working rules: "Runtime request/model output
must not mutate the registry"):
    - `OntologyRegistry` defines no method that writes to the document it
      loaded -- there is no `add_concept()` / `set_intent()` / etc.
    - every collection accessor (`domains`, `concepts`, `intents`,
      `lanes`) returns an immutable `tuple` built fresh from the loaded
      document, not a reference to a mutable list living inside it -- a
      caller cannot `.append()` its way around the missing mutators.
    - `OntologyDocument` itself is an ordinary (not frozen) Pydantic model
      purely so Task 1's own tests can build/mutate throwaway documents in
      isolation; this module never mutates the one instance it loads, and
      nothing else under `aico.control` is expected to call
      `OntologyDocument.model_validate()` on the committed file directly.

`resolve_concepts()` exists specifically for Task 4's model-assisted
interpreter: "candidate is checked against ontology" / "unknown candidate
is rejected" (Day 9 working rules) is exactly `resolve_concepts()`'s
behavior -- raise `OntologyLookupError` on the first id a proposed
candidate list names that this registry does not actually govern, rather
than requiring every caller to re-implement that check over `get_concept`
in a loop.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from pydantic import ValidationError

from aico.control.errors import OntologyLoadError, OntologyLookupError
from aico.control.ontology import Concept, Domain, Intent, LaneId, OntologyDocument

# Relative to the process working directory, matching
# `platform/config.py`'s `DEFAULT_CONFIG_PATH` convention -- `uv run`
# always runs from the repository root.
DEFAULT_REGISTRY_PATH = Path("ontology/registry.v1.json")


class OntologyRegistry:
    """Read-only, typed access to one loaded Mode-A ontology document.

    Construct via `OntologyRegistry.load(...)` in production code (loads
    and validates the committed file); tests may instead build an
    `OntologyDocument` directly and pass it to the plain constructor --
    the constructor itself never touches disk, it only wraps an
    already-validated document and builds lookup indices over it.
    """

    def __init__(self, document: OntologyDocument):
        self._document = document
        self._domains_by_id: dict[str, Domain] = {d.domain_id: d for d in document.domains}
        self._concepts_by_id: dict[str, Concept] = {c.concept_id: c for c in document.concepts}
        self._intents_by_id: dict[str, Intent] = {i.intent_id: i for i in document.intents}

    # ── Loading ──────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: str | Path = DEFAULT_REGISTRY_PATH) -> OntologyRegistry:
        """Load and validate the committed registry file at `path`
        (default `ontology/registry.v1.json` -- committed, read-only
        governed Mode-A data; see `ontology/README.md`). Raises
        `OntologyLoadError` for anything wrong with the file itself:
        missing, unreadable, not valid JSON, or failing
        `OntologyDocument`'s typed validation (Task 1 -- duplicate ids,
        dangling relationship/lane references, invalid status, missing
        version, ...). Never falls back to an empty/default registry."""
        resolved = Path(path)
        if not resolved.exists():
            raise OntologyLoadError(f"ontology registry not found: {resolved}")
        try:
            raw_text = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            raise OntologyLoadError(f"could not read ontology registry {resolved}: {exc}") from exc

        try:
            raw_data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise OntologyLoadError(f"ontology registry {resolved} is not valid JSON: {exc}") from exc

        try:
            document = OntologyDocument.model_validate(raw_data)
        except ValidationError as exc:
            raise OntologyLoadError(f"ontology registry {resolved} failed validation: {exc}") from exc

        return cls(document)

    # ── Active version ───────────────────────────────────────────────

    @property
    def ontology_version(self) -> str:
        """The active governed ontology version this registry loaded --
        included in every Gate-A / lane-selection decision (Day 9 working
        rule: "Route decisions include ontology version and reason")."""
        return self._document.ontology_version

    # ── Read-only collection lookups ─────────────────────────────────

    @property
    def domains(self) -> tuple[Domain, ...]:
        """Every governed domain, in registry order. A fresh tuple, not a
        reference into the loaded document's own list."""
        return tuple(self._document.domains)

    @property
    def concepts(self) -> tuple[Concept, ...]:
        """Every governed concept, in registry order."""
        return tuple(self._document.concepts)

    @property
    def intents(self) -> tuple[Intent, ...]:
        """Every governed intent, in registry order."""
        return tuple(self._document.intents)

    @property
    def lanes(self) -> tuple[LaneId, ...]:
        """The lanes this ontology *version* has enabled -- not
        necessarily every `LaneId` the type system allows; see
        `OntologyDocument`'s own docstring in `ontology.py`."""
        return tuple(self._document.lanes)

    # ── Resolve by id ────────────────────────────────────────────────

    def has_domain(self, domain_id: str) -> bool:
        return domain_id in self._domains_by_id

    def has_concept(self, concept_id: str) -> bool:
        return concept_id in self._concepts_by_id

    def has_intent(self, intent_id: str) -> bool:
        return intent_id in self._intents_by_id

    def get_domain(self, domain_id: str) -> Domain:
        """Resolve a governed `domain_id` to its typed `Domain`. Raises
        `OntologyLookupError` for an id this registry does not govern --
        never returns `None` or a synthesized default."""
        try:
            return self._domains_by_id[domain_id]
        except KeyError:
            raise OntologyLookupError("domain", domain_id) from None

    def get_concept(self, concept_id: str) -> Concept:
        """Resolve a governed `concept_id` to its typed `Concept`. Raises
        `OntologyLookupError` for an id this registry does not govern."""
        try:
            return self._concepts_by_id[concept_id]
        except KeyError:
            raise OntologyLookupError("concept", concept_id) from None

    def get_intent(self, intent_id: str) -> Intent:
        """Resolve a governed `intent_id` to its typed `Intent`. Raises
        `OntologyLookupError` for an id this registry does not govern --
        Gate-A (Task 3/4) is expected to fail closed on this, exactly as
        it does for "no governed match": never invent an intent."""
        try:
            return self._intents_by_id[intent_id]
        except KeyError:
            raise OntologyLookupError("intent", intent_id) from None

    def resolve_concepts(self, concept_ids: Iterable[str]) -> tuple[Concept, ...]:
        """Resolve zero or more governed `concept_id`s to their typed
        `Concept` objects, preserving order -- the primitive a
        model-assisted interpreter's *candidate* concept ids (Task 4) must
        be checked against before Gate-A ever treats one as matched.
        Raises `OntologyLookupError` on the first id that is not governed
        by this registry, the same "unknown candidate is rejected" rule
        Task 4 requires, enforced once here rather than by every caller
        re-implementing it over `get_concept` in a loop. Also what
        resolves a `Concept.relationships` id list into the `Concept`
        objects it names, once Task 1's own validation has already proven
        every one of those ids exists."""
        return tuple(self.get_concept(concept_id) for concept_id in concept_ids)
