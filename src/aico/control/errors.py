"""
Day 9 -- typed control-plane failures (Tasks 2/5).

`OntologyLoadError`/`OntologyLookupError` are shared by `OntologyRegistry`
(`ontology_registry.py`, Task 2) so a caller can distinguish "the
committed registry file itself is missing/invalid" from "a caller asked
this registry to resolve a domain/concept/intent id it does not govern"
with one `except OntologyLoadError` / `except OntologyLookupError`, the
same pattern Day 8's `SessionError` family uses for the memory boundary
(`memory/errors.py`) and Day 6's `IdentityError` uses for the trust
boundary (`api/identity.py`). `LaneSelectionError` (Task 5) is unrelated
to the registry itself -- see its own docstring.
"""
from __future__ import annotations


class OntologyRegistryError(Exception):
    """Base class for every typed failure `aico.control` raises. Never
    raised directly -- see `OntologyLoadError` / `OntologyLookupError`."""


class OntologyLoadError(OntologyRegistryError):
    """Raised by `OntologyRegistry.load()` when the committed registry
    file cannot be read, is not valid JSON, or fails `OntologyDocument`'s
    typed validation (Task 1: duplicate ids, dangling relationship/lane
    references, invalid status, missing version, ...). Carries a single
    sanitized message describing the problem -- callers reason about
    "the registry failed to load", never about Pydantic's own exception
    shape, and there is never a silent fallback to an empty/default
    registry."""


class OntologyLookupError(OntologyRegistryError):
    """Raised by `OntologyRegistry.get_domain()` / `get_concept()` /
    `get_intent()` / `resolve_concepts()` when a given id does not exist
    in the loaded, governed registry. Gate-A (Task 3/4) is expected to
    treat this the same way it treats "no governed match" -- fail closed,
    never invent a fallback record for an id the registry does not
    govern."""

    def __init__(self, kind: str, identifier: str):
        self.kind = kind
        self.identifier = identifier
        super().__init__(f"unknown {kind}: {identifier!r}")


class LaneSelectionError(Exception):
    """Raised by `LaneSelector.select()` (Task 5) when the `GateADecision`
    it was given violates an invariant lane selection depends on -- e.g. a
    `MATCHED` decision with no `intent_id`, or an `intent_id` naming a
    governed intent this selector's own registry does not carry.
    `GateA.classify()` (Task 3) never produces such a decision by
    construction; this exists to fail loudly rather than silently
    misroute if some other caller ever constructs or passes a malformed
    `GateADecision` by hand."""
