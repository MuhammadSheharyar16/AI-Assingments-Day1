"""
CLI: python -m aico.retrieval.embed --index data/index --out data/vectors

Responsibilities:
- Load chunk records built by ingest.py
- Embed only chunks missing from the cache, or whose content_hash or
  model_alias no longer match what is cached (content hash is the
  invalidation key - chunk_id alone is never enough, see vector_index.py)
- Persist the updated cache to --out
- Report how many chunks were embedded, how many were served from cache and
  how many provider calls were made - never the vectors or credentials
  themselves, which must never appear in normal application logs
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from aico.retrieval.embedding_provider import AzureEmbeddingProvider, EmbeddingProvider
from aico.retrieval.search import load_chunks
from aico.retrieval.vector_index import VECTOR_DATASET_VERSION, VectorCache, VectorEntry

EMBED_BATCH_SIZE = 16  # chunks per provider call


def embed_chunks(
    chunks: list[dict],
    provider: EmbeddingProvider,
    cache: VectorCache,
) -> tuple[int, int, int]:
    """Fill `cache` in place with vectors for `chunks`.

    Returns (embedded_count, cached_count, call_count).
    """
    to_embed: list[dict] = []
    cached_count = 0

    for chunk in chunks:
        hit = cache.get_valid(chunk["chunk_id"], chunk["content_hash"], provider.model_alias)
        if hit is not None:
            cached_count += 1
        else:
            to_embed.append(chunk)

    call_count = 0
    for start in range(0, len(to_embed), EMBED_BATCH_SIZE):
        batch = to_embed[start:start + EMBED_BATCH_SIZE]
        vectors = provider.embed([c["text"] for c in batch])
        call_count += 1
        created_at = datetime.now(timezone.utc).isoformat()

        for chunk, vector in zip(batch, vectors):
            if len(vector) != provider.dimensions:
                raise ValueError(
                    f"provider {provider.model_alias!r} returned a "
                    f"{len(vector)}-dim vector for chunk {chunk['chunk_id']}, "
                    f"expected {provider.dimensions}"
                )
            cache.put(VectorEntry(
                chunk_id=chunk["chunk_id"],
                content_hash=chunk["content_hash"],
                model_alias=provider.model_alias,
                dimensions=provider.dimensions,
                dataset_version=VECTOR_DATASET_VERSION,
                created_at=created_at,
                vector=vector,
            ))

    return len(to_embed), cached_count, call_count


def main():
    parser = argparse.ArgumentParser(description="Embed chunks into a persistent vector cache.")
    parser.add_argument("--index", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    load_dotenv()  # reads .env for AICO_EMBEDDING_* config; never committed, never logged

    chunks = load_chunks(args.index)
    provider = AzureEmbeddingProvider()
    cache = VectorCache.load(args.out)

    embedded, cached, calls = embed_chunks(chunks, provider, cache)
    cache.save(args.out)

    print(
        f"Embedded {embedded} chunk(s), served {cached} from cache, "
        f"made {calls} provider call(s) -> {args.out}"
    )


if __name__ == "__main__":
    main()
