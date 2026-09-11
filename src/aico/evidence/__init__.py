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

Task 4 adds `validate_provenance()` (`provenance.py`): checks the actual
returned evidence -- never the corpus -- against `SourceRegistry` (Task 2)
and a real Day 10 `GateBDecision` for the five governed provenance
bullets: source existence, content-hash self-consistency
(`stable_content_hash()`), Gate-B tenant/data-classification scope, and
two package-level cross-item consistency checks (same `source_id` must
agree on `source_version`; same `chunk_id` must agree on identity). Its
typed result is `ProvenanceReport`/`ProvenanceItemResult`/
`ProvenanceFailureReason`.

Task 5 adds `validate_integrity()` (also `provenance.py`): a stronger,
independent check against a caller-supplied `GovernedProvenanceIndex` of
`GovernedProvenanceRecord`s (what governed ingestion actually recorded),
not just the returned item's own self-reported fields -- catches a
coordinated content+hash mutation Task 4's self-consistency check alone
would miss, and fails closed (never "no opinion, so allow") when no
governed record exists for an item at all. Its typed result is
`IntegrityReport`/`IntegrityResult`/`IntegrityFailureReason`.

Task 6 adds `validate_freshness()`/`evaluate_freshness()` (`freshness.py`):
a pure, deterministic core (`evaluate_freshness()`, three primitive
timestamps/hours in, one `FreshnessStatus` out -- fresh/stale/missing/
invalid, never wall-clock) plus a package-level entry point
(`validate_freshness()`) that resolves each item's own governed threshold
through `SourceRegistry` (Task 2) and `GateCPolicyRegistry` (Task 3) and
evaluates it against `EvidencePackage.as_of` -- the injectable reference
time Task 1 already reserved for exactly this. Its typed result is
`FreshnessReport`/`FreshnessItemResult`/`FreshnessFailureReason`.

Later Day 11 tasks add completeness (Task 7) and conflict detection
(Task 8), all built on the envelope, source registry, policy, provenance/
integrity and freshness defined so far.
"""

from aico.evidence.errors import (
    EvidenceEnvelopeError,
    EvidenceError,
    GateCPolicyLoadError,
    GateCPolicyLookupError,
    SourceRegistryLoadError,
    SourceRegistryLookupError,
)
from aico.evidence.freshness import (
    FreshnessFailureReason,
    FreshnessItemResult,
    FreshnessReport,
    FreshnessStatus,
    evaluate_freshness,
    validate_freshness,
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
from aico.evidence.provenance import (
    GovernedProvenanceIndex,
    GovernedProvenanceRecord,
    IntegrityFailureReason,
    IntegrityReport,
    IntegrityResult,
    ProvenanceFailureReason,
    ProvenanceItemResult,
    ProvenanceReport,
    stable_content_hash,
    validate_integrity,
    validate_provenance,
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
    "stable_content_hash",
    "ProvenanceFailureReason",
    "ProvenanceItemResult",
    "ProvenanceReport",
    "validate_provenance",
    "GovernedProvenanceRecord",
    "GovernedProvenanceIndex",
    "IntegrityFailureReason",
    "IntegrityResult",
    "IntegrityReport",
    "validate_integrity",
    "FreshnessStatus",
    "evaluate_freshness",
    "FreshnessFailureReason",
    "FreshnessItemResult",
    "FreshnessReport",
    "validate_freshness",
]
