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
unchecked dict passed further into Gate-C. Later Day 11 tasks add the
governed source registry (Task 2), Gate-C policy (Task 3), provenance
(Task 4), integrity (Task 5), freshness (Task 6), completeness (Task 7)
and conflict detection (Task 8) this envelope feeds into.
"""

from aico.evidence.errors import EvidenceEnvelopeError, EvidenceError
from aico.evidence.models import EvidenceItem, EvidencePackage, parse_evidence_item, parse_evidence_package

__all__ = [
    "EvidenceItem",
    "EvidencePackage",
    "parse_evidence_item",
    "parse_evidence_package",
    "EvidenceError",
    "EvidenceEnvelopeError",
]
