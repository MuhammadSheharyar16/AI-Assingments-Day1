"""
Day 9 -- typed control-plane failures (Task 2).

Shared by `OntologyRegistry` (`ontology_registry.py`) so a caller can
distinguish "the committed registry file itself is missing/invalid" from
"a caller asked this registry to resolve a domain/concept/intent id it
does not govern" with one `except OntologyLoadError` / `except
OntologyLookupError`, the same pattern Day 8's `SessionError` family uses
for the memory boundary (`memory/errors.py`) and Day 6's `IdentityError`
uses for the trust boundary (`api/identity.py`).
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
