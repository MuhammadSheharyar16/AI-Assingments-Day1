"""
Vector cache: persists embeddings to disk so a second run costs nothing, and
answers cosine-similarity search over the cached vectors.

Responsibilities:
- One cache entry per chunk, holding the vector plus chunk_id, content_hash,
  model_alias, dimensions, dataset_version and created_at.
- content_hash - not chunk_id - is the invalidation key. chunk_id is stable
  by design, so a cache keyed on chunk_id alone would survive an edit to the
  underlying text and every later search would rank against a vector for
  text that no longer exists. Keying on content_hash makes an edited chunk
  a guaranteed cache miss.
- model_alias is checked independently of content_hash. A vector produced by
  one model is never valid for another, even if the text is unchanged.
- Cosine similarity over the cached vectors, with a dimension mismatch
  raising rather than being padded or truncated around.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

VECTOR_DATASET_VERSION = "day2-vectors-1.0"  # bump if the cache file shape changes
CACHE_FILENAME = "vectors.json"


@dataclass
class VectorEntry:
    chunk_id: str
    content_hash: str
    model_alias: str
    dimensions: int
    dataset_version: str
    created_at: str
    vector: list[float]

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "content_hash": self.content_hash,
            "model_alias": self.model_alias,
            "dimensions": self.dimensions,
            "dataset_version": self.dataset_version,
            "created_at": self.created_at,
            "vector": self.vector,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "VectorEntry":
        return cls(
            chunk_id=d["chunk_id"],
            content_hash=d["content_hash"],
            model_alias=d["model_alias"],
            dimensions=d["dimensions"],
            dataset_version=d["dataset_version"],
            created_at=d["created_at"],
            vector=d["vector"],
        )


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [-1, 1]. Raises on a dimension mismatch instead
    of silently padding or truncating either vector."""
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch: {len(a)} vs {len(b)}")

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class VectorCache:
    """A content-hash-keyed embedding cache, persisted as one JSON file."""

    def __init__(self, entries: dict[str, VectorEntry] | None = None):
        # keyed by chunk_id so a lookup for a given chunk is O(1); validity
        # of the hit is then decided by content_hash + model_alias below
        self._entries: dict[str, VectorEntry] = entries or {}

    @classmethod
    def load(cls, path: Path) -> "VectorCache":
        cache_file = path / CACHE_FILENAME
        if not cache_file.exists():
            return cls()
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        entries = {e["chunk_id"]: VectorEntry.from_dict(e) for e in data.get("entries", [])}
        return cls(entries)

    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        cache_file = path / CACHE_FILENAME
        payload = {"entries": [e.to_dict() for e in self._entries.values()]}
        # Vectors are numbers, not credentials or raw provider responses -
        # safe to persist, but never printed to logs by any caller.
        cache_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def get_valid(self, chunk_id: str, content_hash: str, model_alias: str) -> VectorEntry | None:
        """Return the cached entry only if it is still valid for the given
        chunk's current text and the currently configured model. A missing
        entry, an edited chunk (content_hash differs) or a different model
        (model_alias differs) are all treated as a miss."""
        entry = self._entries.get(chunk_id)
        if entry is None:
            return None
        if entry.content_hash != content_hash:
            return None
        if entry.model_alias != model_alias:
            return None
        return entry

    def put(self, entry: VectorEntry) -> None:
        self._entries[entry.chunk_id] = entry

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, chunk_id: str) -> bool:
        return chunk_id in self._entries

    def search(self, query_vector: list[float], top_k: int = 5) -> list[tuple[VectorEntry, float]]:
        """Cosine-rank every cached vector against query_vector. Ties break
        on chunk_id so ranking is stable across runs."""
        scored = [(entry, cosine_similarity(query_vector, entry.vector))
                  for entry in self._entries.values()]
        scored.sort(key=lambda pair: (-pair[1], pair[0].chunk_id))
        return scored[:top_k]
