"""
CLI: python -m aico.retrieval.search --query "termination notice period" --mode bm25 --top-k 5
     python -m aico.retrieval.search --query "..." --mode vector --top-k 5
     python -m aico.retrieval.search --query "..." --mode hybrid --top-k 5

Responsibilities:
- Load the chunk index built by ingest.py (never raw documents), and, for
  vector/hybrid modes, the vector cache built by embed.py
- bm25 mode: BM25Index over the chunks, unchanged from Day 1
- vector mode: embed the query through the same provider and model alias
  used for the chunks, cosine-rank against every cached vector. A dimension
  mismatch is a hard error (see vector_index.cosine_similarity) - never
  padded or truncated around
- hybrid mode: reciprocal-rank fusion (hybrid.py) over the full bm25 and
  vector rankings - ranks only, never raw scores
- Print rank, score, chunk_id, source_file, character span, and matched text
  for each of the top-k results - identical shape across all three modes,
  so provenance does not weaken because the ranking method changed
"""

import argparse
import json
import pathlib

from dotenv import load_dotenv

from aico.retrieval.bm25 import BM25Index, ChunkScore
from aico.retrieval.embedding_provider import AzureEmbeddingProvider, EmbeddingProvider
from aico.retrieval.hybrid import RRF_K, reciprocal_rank_fusion
from aico.retrieval.vector_index import VectorCache

MODES = ("bm25", "vector", "hybrid")


def load_chunks(index_dir: pathlib.Path) -> list[dict]:
    chunks_path = index_dir / "index.json"

    if not chunks_path.exists():
        raise FileNotFoundError(
            f"No index.json in {index_dir} - run `python -m aico.retrieval.ingest` first"
        )

    data = json.loads(chunks_path.read_text(encoding="utf-8"))

    return data["chunks"]


def bm25_search(chunks: list[dict], query: str, top_k: int) -> list[ChunkScore]:
    index = BM25Index(chunks)
    return index.search(query, top_k=top_k)


def vector_search(
    chunks: list[dict],
    query: str,
    top_k: int,
    vectors_dir: pathlib.Path,
    provider: EmbeddingProvider,
) -> list[ChunkScore]:
    cache = VectorCache.load(vectors_dir)
    if len(cache) == 0:
        raise FileNotFoundError(
            f"No vectors in {vectors_dir} - run `python -m aico.retrieval.embed` first"
        )

    chunks_by_id = {c["chunk_id"]: c for c in chunks}
    query_vector = provider.embed([query])[0]

    scored = cache.search(query_vector, top_k=top_k)
    # A cache entry for a chunk_id no longer present in the current index
    # (e.g. the index was re-ingested with different parameters after the
    # cache was built) is skipped rather than surfaced as a phantom result.
    return [
        ChunkScore(chunk=chunks_by_id[entry.chunk_id], score=score)
        for entry, score in scored
        if entry.chunk_id in chunks_by_id
    ]


def hybrid_search(
    chunks: list[dict],
    query: str,
    top_k: int,
    vectors_dir: pathlib.Path,
    provider: EmbeddingProvider,
) -> list[ChunkScore]:
    # Fuse over each mode's FULL ranking, not just its top-k, then truncate.
    # Fusing only pre-truncated top-k lists would silently drop a chunk that
    # (say) vector ranked #1 but bm25 ranked outside its own top-k, before
    # fusion ever got a chance to weigh it against the other mode.
    full_k = len(chunks)
    bm25_results = bm25_search(chunks, query, top_k=full_k)
    vector_results = vector_search(
        chunks, query, top_k=full_k, vectors_dir=vectors_dir, provider=provider
    )

    ranked_lists = {
        "bm25": [r.chunk for r in bm25_results],
        "vector": [r.chunk for r in vector_results],
    }
    fused = reciprocal_rank_fusion(ranked_lists, k=RRF_K)
    return [ChunkScore(chunk=r.chunk, score=r.score) for r in fused[:top_k]]


def run_search(
    mode: str,
    chunks: list[dict],
    query: str,
    top_k: int,
    vectors_dir: pathlib.Path,
    provider: EmbeddingProvider | None = None,
) -> list[ChunkScore]:
    """Dispatch to the requested mode. `provider` is only needed for
    vector/hybrid - tests inject FakeEmbeddingProvider here directly rather
    than through the CLI, so no test ever needs a --fake flag or a network
    call."""
    if mode == "bm25":
        return bm25_search(chunks, query, top_k)
    if mode not in ("vector", "hybrid"):
        raise ValueError(f"unknown mode: {mode!r}")

    provider = provider or AzureEmbeddingProvider()
    if mode == "vector":
        return vector_search(chunks, query, top_k, vectors_dir, provider)
    return hybrid_search(chunks, query, top_k, vectors_dir, provider)


def print_results(results: list[ChunkScore], query: str, mode: str) -> None:
    # A zero BM25 score means literally no query term matched - that's the
    # Day 1 "no matching chunks" case. Vector/hybrid scores are similarity-
    # derived and near-never exactly zero, and a vector index always returns
    # its nearest neighbour however far away - deciding a floor for that is
    # Task 3's job (the eval scorer), not this raw search command's.
    no_bm25_match = mode == "bm25" and results and results[0].score == 0
    if not results or no_bm25_match:
        print(f"No matching chunks for: {query!r}")
        return

    for rank, r in enumerate(results, start=1):
        c = r.chunk
        preview = c["text"][:160].replace("\n", " ")
        print(f"[{rank}] score={r.score:.4f} chunk_id={c['chunk_id']} "
              f"source={c['source_file']} span=({c['char_start']},{c['char_end']})")
        print(f"    {preview}...")


def main():
    parser = argparse.ArgumentParser(description="Search a chunk index (bm25, vector, or hybrid).")
    parser.add_argument("--query", required=True)
    parser.add_argument("--mode", choices=MODES, default="bm25")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--index", type=pathlib.Path, default=pathlib.Path("data/index"))
    parser.add_argument("--vectors", type=pathlib.Path, default=pathlib.Path("data/vectors"))
    args = parser.parse_args()

    load_dotenv()  # only exercised for vector/hybrid; a harmless no-op for bm25

    chunks = load_chunks(args.index)
    results = run_search(args.mode, chunks, args.query, args.top_k, args.vectors)
    print_results(results, args.query, args.mode)


if __name__ == "__main__":
    main()
