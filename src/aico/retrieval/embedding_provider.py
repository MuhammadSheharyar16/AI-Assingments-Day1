"""
Embedding provider interface: the one seam between retrieval code and the
embedding API. As of Day 3, no file in this module (or anywhere outside
aico.platform) imports the HTTP client used to call the embedding API -
that lives solely in aico.platform.foundry_adapter, reached through the
Model Gateway (aico.platform.model_gateway).

Responsibilities:
- EmbeddingProvider: the interface every caller (embed CLI, vector_index,
  search) depends on - never the concrete provider class.
- AzureEmbeddingProvider: the one real implementation. Delegates every
  embed() call to a aico.platform.model_gateway.ModelGateway instead of
  calling the provider directly - see ADR-003 for why chat and embedding
  traffic share one platform boundary.
- FakeEmbeddingProvider: deterministic, offline, no network. Every test uses
  this - a vector is derived purely from the input text, so the same text
  always produces the same vector and no test ever calls out.

A vector is only valid for the model that produced it. `model_alias` on the
provider is what the vector cache checks against - it is not a display name.
"""
from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aico.platform.model_gateway import ModelGateway

DEFAULT_FOUNDRY_EMBEDDING_DIMENSIONS = 1536  # text-embedding-3-small's native dimensionality

FAKE_MODEL_ALIAS = "fake-embed-v1"
FAKE_DIMENSIONS = 32


class EmbeddingProvider(ABC):
    """Interface for turning text into vectors. Implementations must be
    consistent: the same model_alias and dimensions for every call, for the
    lifetime of the instance."""

    @property
    @abstractmethod
    def model_alias(self) -> str:
        """Identifies the model that produced (or will produce) a vector.
        A vector cached under one alias is never valid for another."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Vector length this provider produces. Callers compare this
        against a stored vector's length before ever computing similarity."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text, in the same order."""


class AzureEmbeddingProvider(EmbeddingProvider):
    """Real provider. Delegates to a aico.platform.model_gateway.ModelGateway
    instead of calling the provider API directly - this file has no HTTP
    client import at all. `ModelGateway.from_config()` (used when no
    gateway is passed in) reads config/model-routing.yaml and the
    provider-credential environment variable; see aico.platform.config and
    aico.platform.foundry_adapter.
    """

    def __init__(self, gateway: "ModelGateway | None" = None, dimensions: int | None = None):
        if gateway is None:
            from aico.platform.model_gateway import ModelGateway  # local: avoid import at module load time

            gateway = ModelGateway.from_config()
        self._gateway = gateway
        self._model_alias = gateway.config.models.embedding
        self._dimensions = dimensions or DEFAULT_FOUNDRY_EMBEDDING_DIMENSIONS

    @property
    def model_alias(self) -> str:
        return self._model_alias

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        from aico.platform.model_gateway import EmbedRequest

        result = self._gateway.embed(EmbedRequest(texts=texts, model_alias=self._model_alias))
        return result.vectors


class FakeEmbeddingProvider(EmbeddingProvider):
    """Deterministic, offline stand-in for tests and local development.

    The vector for a given text is derived from a SHA-256 hash of that text,
    so it is stable across processes and runs without ever touching the
    network. Different text (almost always) produces a different vector.
    """

    def __init__(self, dimensions: int = FAKE_DIMENSIONS, model_alias: str = FAKE_MODEL_ALIAS):
        self._dimensions = dimensions
        self._model_alias = model_alias

    @property
    def model_alias(self) -> str:
        return self._model_alias

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        seed = text.encode("utf-8")
        raw: list[float] = []
        for i in range(self._dimensions):
            digest = hashlib.sha256(seed + i.to_bytes(4, "big")).digest()
            value = int.from_bytes(digest[:8], "big")
            raw.append((value / 2**63) - 1.0)  # spread into [-1, 1)

        norm = math.sqrt(sum(v * v for v in raw)) or 1.0
        return [v / norm for v in raw]
