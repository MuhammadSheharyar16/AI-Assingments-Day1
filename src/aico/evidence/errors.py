"""
Day 11 Task 1 -- typed evidence-boundary failures.

`EvidenceError` is the base class every typed failure `aico.evidence`
raises inherits from -- the same shape `OntologyRegistryError`
(`control/errors.py`) and `PolicyRegistryError` give the ontology/Gate-B
policy boundaries: one common ancestor a caller can catch broadly, plus
narrow, documented subclasses for the specific failure. Later Day 11 tasks
(source registry Task 2, Gate-C policy Task 3, provenance Task 4) add their
own subclasses here as those boundaries are built; this file starts with
the one Task 1 needs.

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
