"""
Reciprocal-rank fusion: combines independently-ranked result lists (BM25 and
vector) into one ranking, using RANKS only - never raw scores.

A BM25 score is unbounded and corpus-dependent; a cosine score sits in
[-1, 1]. Averaging the two numbers just means whichever one happens to be
numerically larger wins - that's an accident of scale, not a retrieval
decision. RRF sidesteps the problem entirely by discarding the scores and
fusing on each chunk's rank position within each mode:

    score(chunk) = sum over modes of 1 / (k + rank_in_that_mode)

RRF_K = 60 (the standard starting point, from Cormack et al. 2009).
Raising k flattens the curve - the gap between rank 1 and rank 50 shrinks,
so fusion behaves more like a broad rank-sum vote across modes. Lowering k
sharpens it - a top rank in either mode dominates the fused score, so
fusion behaves closer to "trust whichever single mode ranked it highest".
"""
from __future__ import annotations

from dataclasses import dataclass, field

RRF_K = 60


@dataclass
class FusedResult:
    chunk: dict
    score: float
    per_mode_rank: dict[str, int] = field(default_factory=dict)


def reciprocal_rank_fusion(
    ranked_lists: dict[str, list[dict]],
    k: int = RRF_K,
) -> list[FusedResult]:
    """Fuse named ranked lists of chunk records into one RRF ranking.

    `ranked_lists` maps a mode name (e.g. "bm25", "vector") to a list of
    chunk dicts already sorted best-first (index 0 = rank 1). A chunk absent
    from a given mode's list contributes nothing to that mode's term of the
    sum - it is not assigned a worst-case rank.

    Ties broken by chunk_id ascending, matching the tie-break used by both
    individual modes, so the fused ranking is stable and reproducible too.
    """
    scores: dict[str, float] = {}
    chunks: dict[str, dict] = {}
    ranks: dict[str, dict[str, int]] = {}

    for mode, results in ranked_lists.items():
        for idx, chunk in enumerate(results):
            rank = idx + 1
            chunk_id = chunk["chunk_id"]
            chunks[chunk_id] = chunk
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
            ranks.setdefault(chunk_id, {})[mode] = rank

    fused = [
        FusedResult(chunk=chunks[cid], score=scores[cid], per_mode_rank=ranks[cid])
        for cid in scores
    ]
    fused.sort(key=lambda r: (-r.score, r.chunk["chunk_id"]))
    return fused
