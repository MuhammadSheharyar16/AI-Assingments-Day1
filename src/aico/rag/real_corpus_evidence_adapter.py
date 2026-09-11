"""
Day 11 Task 13 (real-corpus extension) -- `RealCorpusEvidenceAdapter`: the
concrete, production `EvidenceAdapter` (`aico.rag.control_plane_answer_
service.EvidenceAdapter`) for the real `data/documents/` corpus, built
entirely from real, already-governed data:

    - `source_id`/`source_version`/`owner`/`data_classification` -- each
      real document's own front-matter (`**Document ID:**`/`**Version:**`/
      `**Owner:**`/`**Classification:**`), read once into `evidence/
      real_corpus_source_registry.v1.json` and `evidence/real_corpus_
      manifest.v1.json` by `scripts/day11_generate_real_corpus_registry.py`
      -- never invented at request time.
    - `source_updated_at` -- that same document's real last-commit
      timestamp (`git log -1 --format=%aI`), not filesystem mtime (a
      fresh checkout resets mtime to checkout time, so it is not a real
      freshness signal at all).
    - `content_hash` -- recomputed fresh from the actual retrieved
      `EvidenceChunk.text` via `provenance.py`'s own `stable_content_hash()`
      (Task 4's self-consistency check); the *expected* value Task 5's
      stronger integrity check verifies against comes independently from
      `data/index/index.json`'s own already-committed `content_hash` per
      `chunk_id` (`provenance_index()` below) -- the real chunker's own
      output, joined by `chunk_id`, never re-derived from the returned
      item.
    - `tenant_id` -- `TENANT-A`, this deployment's one governed tenant
      (`config/control-plane.yaml`'s own `gate_b` section / `policy/
      gate_b_policy.v1.json`'s committed roles); the whole corpus is
      shared reference material within it, not owned by a narrower tenant
      the real documents do not actually distinguish.
    - `evidence_facets` -- `supplier_governance_policy` (every document)
      plus one informational `doc_NNN_<slug>` facet per document, both
      recorded in the manifest, never inferred from content at request
      time.
    - `claims` -- deliberately left empty (the default). This corpus is
      internal governance *policy* text (rules), not per-supplier
      *records* (facts about one specific supplier) -- there is no
      claimed value here for Task 8's conflict detection to meaningfully
      reconcile (`validate_conflicts()` finds zero claims for every facet
      and returns `NO_CONFLICT` trivially, which is the honest outcome
      for this evidence shape, not a gap this adapter should paper over
      with invented claims).

## Why one shared facet, not one per document, as the *governed
## requirement*

`policy/real_corpus_gate_c_policy.v1.json`'s one rule (`RC-R001`)
requires only `supplier_governance_policy` -- every document supports it,
so completeness never depends on which 1-2 of the five real documents a
particular question's retrieval actually returned (a real question about
payment terms will only ever retrieve `DOC-003`, never all five). A
completeness rule requiring every document's own facet would make every
real request `insufficient_evidence` regardless of whether retrieval
correctly answered the question -- exactly the "the model/gate cannot
manufacture coverage of a facet nothing retrieved" concern Task 7 warns
about, inverted: it would manufacture a *requirement* retrieval was never
going to satisfy. The per-document `doc_NNN_<slug>` facets still exist and
are still carried on every item (visible in `missing_facets`/observability
if a caller's own governed policy is later narrowed further), they are
just not part of what this one governed rule *requires* today.

## Unknown documents

A retrieved chunk whose `source_file` is not in the manifest (should not
happen -- the manifest covers every file `data/documents/` committed, the
same corpus `BM25Retriever` indexes) is still turned into a typed
`EvidenceItem`, pointed at the deliberately ungoverned `source_id`
`"SRC-UNKNOWN-DOCUMENT"` -- never silently dropped. Gate-C's own registry
lookup (`_registry_policy_reasons()`/`validate_provenance()`) then rejects
it with `unknown_source`, the identical fail-closed outcome an actually
unknown source produces anywhere else in this pack; this adapter itself
never decides what counts as trusted, only what a chunk *claims*.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from opentelemetry import trace

from aico.control.models import LaneDecision
from aico.control.policy_models import DataClassification
from aico.evidence.models import EvidenceItem, EvidencePackage
from aico.evidence.provenance import GovernedProvenanceIndex, GovernedProvenanceRecord, stable_content_hash
from aico.rag.citation_validator import EvidenceChunk

DEFAULT_MANIFEST_PATH = Path("evidence/real_corpus_manifest.v1.json")
DEFAULT_INDEX_PATH = Path("data/index/index.json")

# The one governed request shape `policy/real_corpus_gate_c_policy.v1.json`
# defines a rule for -- every real request this adapter builds a package
# for is evaluated under it (see module docstring, "Why one shared facet").
REQUEST_KIND = "policy_lookup"

# Deliberately ungoverned -- never a real entry in `evidence/real_corpus_
# source_registry.v1.json` (see module docstring, "Unknown documents").
_UNKNOWN_SOURCE_ID = "SRC-UNKNOWN-DOCUMENT"


@dataclass(frozen=True)
class _DocumentRecord:
    source_id: str
    source_version: str
    data_classification: DataClassification
    source_updated_at: datetime
    facets: tuple[str, ...]


class RealCorpusEvidenceAdapter:
    """Loads `evidence/real_corpus_manifest.v1.json` and `data/index/
    index.json` once at construction (both committed, read-only files --
    the identical "load once, reuse" contract `SourceRegistry`/
    `GateCPolicyRegistry` already give their own governed data), then maps
    every `(question, lane_decision, retrieved)` call into a typed
    `EvidencePackage` -- `ControlPlaneAnswerService`'s `EvidenceAdapter`
    seam (`control_plane_answer_service.py`)."""

    def __init__(self, manifest_path: Path = DEFAULT_MANIFEST_PATH, index_path: Path = DEFAULT_INDEX_PATH):
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        self._tenant_id: str = manifest["tenant_id"]
        self._documents: dict[str, _DocumentRecord] = {
            doc["source_file"]: _DocumentRecord(
                source_id=doc["source_id"],
                source_version=doc["source_version"],
                data_classification=DataClassification(doc["data_classification"]),
                source_updated_at=datetime.fromisoformat(doc["source_updated_at"]),
                facets=tuple(doc["facets"]),
            )
            for doc in manifest["documents"]
        }

        index = json.loads(Path(index_path).read_text(encoding="utf-8"))
        # Real chunker output (`aico.retrieval.chunker`/`aico.retrieval.
        # ingest`, already committed to `data/index/index.json`), joined
        # here by `chunk_id` -- the independent "governed ingestion path"
        # value Task 5's `validate_integrity()` compares the *returned*
        # item against, never re-derived from what retrieval returns.
        self._chunk_hash_by_id: dict[str, str] = {c["chunk_id"]: c["content_hash"] for c in index["chunks"]}
        self._chunk_source_file_by_id: dict[str, str] = {c["chunk_id"]: c["source_file"] for c in index["chunks"]}

    def provenance_index(self) -> GovernedProvenanceIndex:
        """Task 5's independent expected-provenance lookup, built once
        from the same real, already-committed `data/index/index.json` +
        manifest this adapter itself reads -- every real chunk this corpus
        can ever retrieve is covered, so an item Task 4's self-consistency
        check already passed can still be caught by Task 5's stronger,
        independently-sourced integrity check."""
        records = []
        for chunk_id, content_hash in self._chunk_hash_by_id.items():
            source_file = self._chunk_source_file_by_id[chunk_id]
            document = self._documents.get(source_file)
            if document is None:
                continue
            records.append(
                GovernedProvenanceRecord(
                    source_id=document.source_id,
                    chunk_id=chunk_id,
                    source_version=document.source_version,
                    content_hash=content_hash,
                )
            )
        return GovernedProvenanceIndex(records)

    def _request_id(self) -> str:
        """A per-call identifier for `EvidencePackage.request_id` (Task
        1's required, non-blank field). This module deliberately never
        imports `aico.api`/`aico.observability` (the same boundary
        `control_plane_answer_service.py`/`answer_service.py` already
        keep -- see their own module docstrings), so it cannot read a
        caller's own `request_id`; the ambient OTel trace id (set by
        whatever span is already current when this adapter runs, the
        identical correlation mechanism every span in this pipeline
        already relies on) gives a real per-request value when one is
        available, falling back to a fresh id only when no span is
        current at all (e.g. this adapter called directly, outside any
        traced request)."""
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            return format(span_context.trace_id, "032x")
        return uuid.uuid4().hex

    def __call__(
        self, question: str, lane_decision: LaneDecision, retrieved: list[EvidenceChunk]
    ) -> tuple[EvidencePackage, str | None]:
        now = datetime.now(UTC)
        items = []
        for chunk in retrieved:
            document = self._documents.get(chunk.source_file)
            content_hash = stable_content_hash(chunk.text)
            if document is None:
                items.append(
                    EvidenceItem(
                        evidence_id=chunk.chunk_id,
                        chunk_id=chunk.chunk_id,
                        source_id=_UNKNOWN_SOURCE_ID,
                        source_version="0",
                        source_updated_at=now,
                        retrieved_at=now,
                        content_hash=content_hash,
                        tenant_id=self._tenant_id,
                        data_classification=DataClassification.INTERNAL,
                        evidence_facets=(),
                        content=chunk.text,
                    )
                )
                continue

            items.append(
                EvidenceItem(
                    evidence_id=chunk.chunk_id,
                    chunk_id=chunk.chunk_id,
                    source_id=document.source_id,
                    source_version=document.source_version,
                    source_updated_at=document.source_updated_at,
                    retrieved_at=now,
                    content_hash=content_hash,
                    tenant_id=self._tenant_id,
                    data_classification=document.data_classification,
                    evidence_facets=document.facets,
                    content=chunk.text,
                )
            )

        package = EvidencePackage(
            request_id=self._request_id(),
            intent_id=lane_decision.intent_id,
            lane=lane_decision.lane,
            as_of=now,
            # Gate-C derives the authoritative `required_facets` from its
            # own governed policy rule (`gate_c.py`'s own Stage 7 -- "never
            # `package.required_facets` as supplied by the caller"), so
            # this adapter deliberately leaves it empty rather than
            # duplicating `policy/real_corpus_gate_c_policy.v1.json`'s own
            # value here.
            required_facets=(),
            items=tuple(items),
        )
        return package, REQUEST_KIND
