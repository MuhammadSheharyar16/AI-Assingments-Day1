"""
Day 5 Task 3 — citation validation.

Model-produced citations are untrusted output, not fact (grounding_rules.md
#4-5). A citation is valid only if its chunk_id is a member of the chunk
IDs actually retrieved and supplied to the model for this turn:

    cited_ids ⊆ retrieved_context_ids

This is a *membership* check against real retrieved IDs - not a format/
regex check that a string merely looks like a chunk ID (working rule /
common cause of failure: "checking citation format but not actual
retrieved membership"). Any citation outside that set fails the whole
validation closed: `valid=False` on the result, never a silently trimmed
list of "the citations that happened to be real" with the rest of the
answer still trusted (working rule / common cause of failure: "deleting a
forged citation and still returning the answer as trusted").
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceChunk:
    """One piece of retrieved evidence supplied to the model. `text` is
    untrusted data (see prompt_builder.py) - never instruction."""

    chunk_id: str
    source_file: str
    text: str


@dataclass(frozen=True)
class CitationValidationResult:
    valid: bool
    forged_citation_ids: tuple[str, ...]
    retrieved_ids: tuple[str, ...]
    cited_ids: tuple[str, ...]


def validate_citations(cited_ids: list[str], retrieved: list[EvidenceChunk]) -> CitationValidationResult:
    """Validate every entry in `cited_ids` against the chunk IDs actually
    present in `retrieved` - the same list that was supplied to the model
    for this turn, never the full corpus. Fails closed: even one forged
    citation makes the whole result invalid."""
    retrieved_ids = tuple(c.chunk_id for c in retrieved)
    retrieved_set = set(retrieved_ids)
    forged = tuple(cid for cid in cited_ids if cid not in retrieved_set)
    return CitationValidationResult(
        valid=len(forged) == 0,
        forged_citation_ids=forged,
        retrieved_ids=retrieved_ids,
        cited_ids=tuple(cited_ids),
    )
