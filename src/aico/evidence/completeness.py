"""
Day 11 Task 7 -- completeness validation.

`Day 11 Task.pdf`'s own framing is the whole spec: completeness is not "we
retrieved 5 chunks" -- it is "the validated evidence covers the facets
required for this governed request." This module owns exactly that: it
never counts items, only which governed facets (`data/day11_pack/
completeness_cases.json`'s own examples: `supplier_identity`,
`payment_terms`, `invoice_window`, `contract_status`) the *valid* evidence
in one package actually covers, against `EvidencePackage.required_facets`
-- the field Task 1 already reserved for exactly this ("Governed facets
this request needs covered (Task 7), from Gate-C policy").

Required behavior, each traced to where it is enforced:

    - all required facets covered
      -> may pass                     -> `missing_facets` is empty ->
                                          `CompletenessStatus.COMPLETE`.
                                          "May", not "will": Task 3's
                                          separate `minimum_valid_items`
                                          floor and every other Gate-C
                                          check (provenance/integrity/
                                          freshness/conflicts) still apply
                                          -- Gate-C (Task 9) composes all
                                          of them, this module answers
                                          facet coverage alone.
    - one required facet missing
      -> insufficient                 -> `missing_facets` non-empty ->
                                          `CompletenessStatus.
                                          INSUFFICIENT_EVIDENCE`.
    - duplicate chunks covering the
      same facet do not count as
      another missing facet           -> `covered_facets` is a `set`
                                          union across every valid item's
                                          facets, not a count -- a facet
                                          claimed by five items is exactly
                                          as covered as one claimed by a
                                          single item.
    - evidence from an invalid/stale
      item does not count toward
      completeness                    -> only items whose `evidence_id`
                                          is in the caller-supplied
                                          `valid_evidence_ids` contribute
                                          facets at all -- the set Gate-C
                                          (Task 9) is expected to build by
                                          intersecting `ProvenanceReport.
                                          validated_evidence_ids`,
                                          `IntegrityReport.
                                          validated_evidence_ids` and
                                          `FreshnessReport.
                                          validated_evidence_ids`. A
                                          rejected item's `evidence_facets`
                                          are never read at all.
    - unsupported facet claim cannot
      be manufactured by the model    -> even for a valid item, only
                                          facets that are *both* claimed
                                          (`item.evidence_facets`) *and*
                                          governed for that item's own
                                          source (Task 2's `SourceRecord.
                                          supported_facets`, via
                                          `source_registry.
                                          supports_facet()`) are credited
                                          -- an item claiming a facet its
                                          own governed source is not
                                          declared to ever supply
                                          contributes nothing for that
                                          facet, whatever the item's own
                                          `evidence_facets` list says.

`gate_c_cases.json`'s `completeness_cases.json` fixture cases (COMP-001..
004) are proven directly against this module's own API in the test file --
COMP-001/002/003 map onto the `valid_evidence_ids` filter unmodified
(every item in those cases is already-validated); COMP-004's explicit
per-item `valid` flag is exactly what `valid_evidence_ids` is for -- the
invalid item there is simply excluded from that set.
"""
from __future__ import annotations

from collections.abc import Iterable
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from aico.evidence.models import EvidencePackage
from aico.evidence.source_registry import SourceRegistry


class CompletenessStatus(str, Enum):
    """The closed set of outcomes `validate_completeness()` can return."""

    COMPLETE = "complete"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class CompletenessResult(BaseModel):
    """Completeness's one, package-level verdict: which facets were
    required, which were actually covered by valid, source-governed
    evidence, and -- when incomplete -- exactly which required facets
    still have no legitimate coverage at all."""

    model_config = ConfigDict(extra="forbid")

    status: CompletenessStatus
    required_facets: tuple[str, ...] = Field(default_factory=tuple)
    covered_facets: tuple[str, ...] = Field(default_factory=tuple)
    missing_facets: tuple[str, ...] = Field(default_factory=tuple)


def validate_completeness(
    package: EvidencePackage,
    *,
    valid_evidence_ids: Iterable[str],
    source_registry: SourceRegistry,
) -> CompletenessResult:
    """Validate `package.required_facets` coverage against only the items
    named in `valid_evidence_ids` -- never the full candidate list, and
    never a raw item count. Deterministic, never mutates `package`/
    `source_registry`, never calls the Model Gateway (working rule: "The
    model does not decide whether evidence is trusted/fresh/complete")."""
    valid_ids = set(valid_evidence_ids)
    required_facets = tuple(package.required_facets)

    covered: set[str] = set()
    for item in package.items:
        if item.evidence_id not in valid_ids:
            continue
        if not source_registry.has_source(item.source_id):
            continue
        source = source_registry.get_source(item.source_id)
        for facet in item.evidence_facets:
            if source.supports_facet(facet):
                covered.add(facet)

    missing = tuple(facet for facet in required_facets if facet not in covered)
    status = CompletenessStatus.COMPLETE if not missing else CompletenessStatus.INSUFFICIENT_EVIDENCE

    return CompletenessResult(
        status=status,
        required_facets=required_facets,
        covered_facets=tuple(facet for facet in required_facets if facet in covered),
        missing_facets=missing,
    )
