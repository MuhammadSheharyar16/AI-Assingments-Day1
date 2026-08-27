"""
- Hand-worked example: two small ranked lists fuse to the exact expected
  score/order via score(chunk) = sum over modes of 1/(k + rank) - RANK only,
  never a raw BM25 or cosine score
- A chunk retrieved by only one mode still contributes its one term
- Ties broken by chunk_id ascending, matching both single modes' tie-break
- Determinism: two identical fusion calls produce identical output
- Raising k flattens the gap between a high and low rank; lowering it
  sharpens the gap - directly exercises the k tuning knob
"""
import pytest

from aico.retrieval.hybrid import reciprocal_rank_fusion


def _chunk(chunk_id):
    return {"chunk_id": chunk_id, "text": chunk_id}


def test_hand_worked_fusion_example():
    # bm25 ranks:   A(1), B(2), C(3)
    # vector ranks: B(1), A(2), C(3)
    bm25 = [_chunk("A"), _chunk("B"), _chunk("C")]
    vector = [_chunk("B"), _chunk("A"), _chunk("C")]

    fused = reciprocal_rank_fusion({"bm25": bm25, "vector": vector}, k=60)
    scores = {r.chunk["chunk_id"]: r.score for r in fused}

    assert scores["A"] == pytest.approx(1 / 61 + 1 / 62)
    assert scores["B"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["C"] == pytest.approx(1 / 63 + 1 / 63)
    assert scores["A"] == pytest.approx(scores["B"])  # symmetric ranks -> tied score
    assert scores["C"] < scores["A"]

    # A and B are tied on score, so the tie-break (chunk_id ascending) decides order
    assert [r.chunk["chunk_id"] for r in fused] == ["A", "B", "C"]


def test_chunk_retrieved_by_only_one_mode_still_gets_scored():
    bm25 = [_chunk("A"), _chunk("B")]
    vector = [_chunk("A")]  # B was never retrieved by vector at all

    fused = reciprocal_rank_fusion({"bm25": bm25, "vector": vector}, k=60)
    scores = {r.chunk["chunk_id"]: r.score for r in fused}

    assert scores["A"] == pytest.approx(1 / 61 + 1 / 61)
    assert scores["B"] == pytest.approx(1 / 62)
    assert scores["A"] > scores["B"]


def test_fusion_is_deterministic():
    bm25 = [_chunk("A"), _chunk("B"), _chunk("C")]
    vector = [_chunk("C"), _chunk("B"), _chunk("A")]

    run1 = [(r.chunk["chunk_id"], r.score) for r in reciprocal_rank_fusion({"bm25": bm25, "vector": vector})]
    run2 = [(r.chunk["chunk_id"], r.score) for r in reciprocal_rank_fusion({"bm25": bm25, "vector": vector})]

    assert run1 == run2


def test_raising_k_flattens_the_gap_between_ranks():
    bm25 = [_chunk("A"), _chunk("B")]
    vector = [_chunk("A"), _chunk("B")]

    def gap(k):
        fused = reciprocal_rank_fusion({"bm25": bm25, "vector": vector}, k=k)
        scores = {r.chunk["chunk_id"]: r.score for r in fused}
        return scores["A"] - scores["B"]

    assert gap(k=1) > gap(k=1000) > 0
