"""
Embedding provider interface: the one seam between the codebase and the
embedding API. Nothing outside this module talks to the provider SDK/API
directly - a repository search for the request import must return this
file only.

Responsibilities:
- EmbeddingProvider: the interface every caller (embed CLI, vector_index,
  search) depends on - never the concrete provider class.
- AzureEmbeddingProvider: the one real implementation, calling the approved
  Azure AI Foundry embeddings endpoint over the model/endpoint/key supplied
  by the team. Configuration comes from environment variables (see .env),
  never hardcoded, and is never logged.
- FakeEmbeddingProvider: deterministic, offline, no network. Every test uses
  this - a vector is derived purely from the input text, so the same text
  always produces the same vector and no test ever calls out.

A vector is only valid for the model that produced it. `model_alias` on the
provider is what the vector cache checks against - it is not a display name.
"""
from __future__ import annotations

import hashlib
import math
import os
from abc import ABC, abstractmethod
from urllib.parse import urlencode

import requests

DEFAULT_API_VERSION = "2024-05-01-preview"
DEFAULT_AZURE_DIMENSIONS = 1536  # text-embedding-3-small's native dimensionality

# Observed against the shared dev Foundry endpoint: roughly 1 in 10 calls
# comes back 404 DeploymentNotFound for no client-side reason - identical
# requests, immediately retried by hand, succeed. Timeouts, retries and
# routing policy are explicitly Day 3 scope, so this is not retried here;
# a failure here just means "run the embed command again."

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
    """Real provider. Calls the Azure AI Foundry embeddings REST endpoint.

    This is the only file in the repository allowed to make that call -
    everything else goes through the EmbeddingProvider interface.
    """

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        api_version: str | None = None,
        dimensions: int = DEFAULT_AZURE_DIMENSIONS,
    ):
        self._endpoint = (endpoint or os.environ.get("AICO_EMBEDDING_ENDPOINT", "")).rstrip("/")
        self._api_key = api_key or os.environ.get("AICO_EMBEDDING_API_KEY", "")
        self._model = model or os.environ.get("AICO_EMBEDDING_MODEL", "text-embedding-3-small")
        self._api_version = api_version or os.environ.get(
            "AICO_EMBEDDING_API_VERSION", DEFAULT_API_VERSION
        )
        self._dimensions = dimensions

        if not self._endpoint or not self._api_key:
            raise RuntimeError(
                "AICO_EMBEDDING_ENDPOINT and AICO_EMBEDDING_API_KEY must be set "
                "(see .env, gitignored) to use the real embedding provider."
            )

    @property
    def model_alias(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        # Foundry's unified Model Inference API (provider-agnostic, works the
        # same way regardless of which vendor's model is behind the
        # deployment) - the model goes in the request body, not the URL path.
        query = urlencode({"api-version": self._api_version})
        url = f"{self._endpoint}/models/embeddings?{query}"
        headers = {"api-key": self._api_key, "Content-Type": "application/json"}
        body = {"input": texts, "model": self._model}

        # Never log headers, the request body or the response - both the key
        # and the vectors themselves must stay out of normal application logs.
        response = requests.post(url, headers=headers, json=body, timeout=30)
        response.raise_for_status()
        payload = response.json()

        # Azure is not guaranteed to return items in input order - restore it.
        items = sorted(payload["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in items]


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
