"""
Day 11 evidence-trust boundary. `src/aico/evidence/` owns evidence
trust/quality responsibilities -- source registry, provenance, freshness,
completeness, conflicts -- kept separate from `src/aico/control/gate_c.py`,
which owns the Gate-C *decision* built from them (structure rule:
"Evidence validation must not be scattered as ad hoc checks throughout
API/model code").

Task 1 exports the typed evidence envelope (`models.py`): one candidate
evidence record as actually returned by retrieval / the protected data
adapter is `EvidenceItem`; the package-level request context wrapping a
batch of them is `EvidencePackage`. `parse_evidence_item()` /
`parse_evidence_package()` are the boundary parsers -- raw dict in, typed
model out, or a sanitized `EvidenceEnvelopeError` (`errors.py`), never an
unchecked dict passed further into Gate-C.

Task 2 adds `SourceRegistry` (`source_registry.py`), the read-only, typed
loader/lookup service Gate-C (Task 9) is built against -- loading the
committed `evidence/source_registry.v1.json` through `SourceRecord`/
`SourceRegistryDocument`, cross-checked against the real committed
`OntologyRegistry`'s governed intent ids -- plus its typed failures
(`SourceRegistryLoadError`/`SourceRegistryLookupError`, `errors.py`).

Task 3 adds `GateCPolicyRegistry` (`policy.py`), the read-only, typed
loader/lookup service for the governed evidence-quality policy -- loading
the committed `policy/gate_c_policy.v1.json` through
`GateCPolicyDocument`/`IntentEvidenceRequirement`/`FreshnessPolicy`/
`ConflictPolicy`, cross-checked against both the real committed
`OntologyRegistry` and Task 2's `SourceRegistry` -- plus its typed
failures (`GateCPolicyLoadError`/`GateCPolicyLookupError`, `errors.py`).

Later Day 11 tasks add provenance (Task 4), integrity (Task 5), freshness
(Task 6), completeness (Task 7) and conflict detection (Task 8), all built
on the envelope, source registry and policy defined so far.
"""

from aico.evidence.errors import (
    EvidenceEnvelopeError,
    EvidenceError,
    GateCPolicyLoadError,
    GateCPolicyLookupError,
    SourceRegistryLoadError,
    SourceRegistryLookupError,
)
from aico.evidence.models import EvidenceItem, EvidencePackage, parse_evidence_item, parse_evidence_package
from aico.evidence.policy import (
    DEFAULT_GATE_C_POLICY_PATH,
    ConflictPolicy,
    FreshnessPolicy,
    GateCPolicyDocument,
    GateCPolicyRegistry,
    IntentEvidenceRequirement,
)
from aico.evidence.source_registry import (
    DEFAULT_SOURCE_REGISTRY_PATH,
    SourceRecord,
    SourceRegistry,
    SourceRegistryDocument,
    SourceStatus,
)

__all__ = [
    "EvidenceItem",
    "EvidencePackage",
    "parse_evidence_item",
    "parse_evidence_package",
    "EvidenceError",
    "EvidenceEnvelopeError",
    "SourceRecord",
    "SourceRegistryDocument",
    "SourceStatus",
    "SourceRegistry",
    "DEFAULT_SOURCE_REGISTRY_PATH",
    "SourceRegistryLoadError",
    "SourceRegistryLookupError",
    "ConflictPolicy",
    "FreshnessPolicy",
    "IntentEvidenceRequirement",
    "GateCPolicyDocument",
    "GateCPolicyRegistry",
    "DEFAULT_GATE_C_POLICY_PATH",
    "GateCPolicyLoadError",
    "GateCPolicyLookupError",
]
