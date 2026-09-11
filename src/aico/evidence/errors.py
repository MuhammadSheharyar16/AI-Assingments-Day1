"""
Day 11 Task 1 -- typed evidence-boundary failures.
Day 11 Task 2 -- typed governed-source-registry failures.
Day 11 Task 3 -- typed Gate-C policy failures.

`EvidenceError` is the base class every typed failure `aico.evidence`
raises inherits from -- the same shape `OntologyRegistryError`
(`control/errors.py`) and `PolicyRegistryError` give the ontology/Gate-B
policy boundaries: one common ancestor a caller can catch broadly, plus
narrow, documented subclasses for the specific failure. Later Day 11 tasks
(provenance Task 4, freshness Task 6, completeness Task 7, conflicts
Task 8) add their own subclasses here as those boundaries are built; this
file so far carries Task 1's envelope error, Task 2's source-registry
pair, and Task 3's Gate-C-policy pair.

`EvidenceEnvelopeError` (Task 1) is raised when a raw candidate-evidence
payload -- the actual evidence returned by retrieval / the protected data
adapter, not the corpus at large (working rule: "Gate-C evaluates the
actual evidence returned by retrieval") -- fails `EvidenceItem`'s or
`EvidencePackage`'s typed validation (`models.py`): missing source id,
missing source version, missing provenance identifier (`chunk_id`),
invalid/naive timestamps, missing content hash, invalid classification
enum, a duplicate `evidence_id` within one package, or any other malformed
shape (`evidence_policy_requirements.md`'s "Required failure behavior").
Carries one sanitized message plus, when Pydantic located it, the
offending field's path -- a caller reasons about "the evidence envelope
failed to validate", never about Pydantic's own `ValidationError` shape
(the same boundary `contracts/validator.py`'s `ValidationFailure` draws
for Day 4's model-output contracts). Never raised with raw evidence
content in the message -- only field names/paths and Pydantic's own
structural error text, per the working rule against logging raw
protected content.

There is never a silent fallback to a partially-validated envelope or an
unchecked dict standing in for one (working rule: "Do not pass unchecked
dictionaries into Gate-C") -- a payload that fails this validation simply
does not become an `EvidenceItem`/`EvidencePackage`, and nothing downstream
(Gate-C included) is permitted to construct one by hand from raw data
instead.
"""
from __future__ import annotations


class EvidenceError(Exception):
    """Base class for every typed failure `aico.evidence` raises. Never
    raised directly -- see `EvidenceEnvelopeError` (Task 1) and the
    subclasses later Day 11 tasks add for the source registry, Gate-C
    policy, provenance, freshness, completeness and conflict boundaries."""


class EvidenceEnvelopeError(EvidenceError):
    """Raised by `parse_evidence_item()` / `parse_evidence_package()`
    (`models.py`) when a raw candidate-evidence payload fails typed
    validation. See module docstring for the full list of conditions this
    covers."""

    def __init__(self, message: str, *, field_path: str | None = None):
        self.field_path = field_path
        super().__init__(message)


class SourceRegistryLoadError(EvidenceError):
    """Raised by `SourceRegistry.load()` (`source_registry.py`, Task 2)
    when the committed `evidence/source_registry.v1.json` cannot be read,
    is not valid JSON, fails `SourceRegistryDocument`'s typed validation
    (duplicate `source_id`, an `allowed_intents` entry naming an intent the
    loaded `OntologyRegistry` does not govern, invalid `status`, missing
    `registry_version`, ...), or fails the registry's own integrity check.
    Mirrors `OntologyLoadError`/`PolicyLoadError`'s fail-loud contract --
    there is never a silent fallback to an empty/default/permissive
    registry."""


class SourceRegistryLookupError(EvidenceError):
    """Raised by `SourceRegistry.get_source()` (and the compatibility
    checks built on it) when a given `source_id` does not exist in the
    loaded, governed registry. Callers are expected to treat this the same
    way `OntologyLookupError`/`PolicyLookupError` are treated -- fail
    closed, never invent a fallback source record for an id the registry
    does not govern (working rule: "unknown ... source cannot pass
    Gate-C")."""

    def __init__(self, kind: str, identifier: str):
        self.kind = kind
        self.identifier = identifier
        super().__init__(f"unknown {kind}: {identifier!r}")


class GateCPolicyLoadError(EvidenceError):
    """Raised by `GateCPolicyRegistry.load()` (`policy.py`, Task 3) when
    the committed `policy/gate_c_policy.v1.json` cannot be read, is not
    valid JSON, fails `GateCPolicyDocument`'s typed validation (duplicate
    freshness `policy_id`, duplicate `rule_id`, a rule referencing an
    intent the loaded `OntologyRegistry` does not govern, a rule
    referencing a source type the loaded `SourceRegistry` does not govern,
    invalid `status`, missing `policy_version`, ...), fails this
    registry's own ambiguous-rule-combination check, or -- the
    reverse-direction half of the Task 2/Task 3 cross-reference -- a
    governed source's `freshness_policy_id` (Task 2) names a freshness
    policy this document does not itself declare. Mirrors
    `PolicyLoadError`'s fail-loud contract -- there is never a silent
    fallback to an empty/default/permissive policy."""


class GateCPolicyLookupError(EvidenceError):
    """Raised by `GateCPolicyRegistry.get_freshness_policy()` /
    `get_rule()` when a given id does not exist in the loaded, governed
    Gate-C policy. Callers are expected to treat this the same way every
    other typed lookup error in this codebase is treated -- fail closed,
    never invent a fallback record for an id the policy does not govern."""

    def __init__(self, kind: str, identifier: str):
        self.kind = kind
        self.identifier = identifier
        super().__init__(f"unknown {kind}: {identifier!r}")
