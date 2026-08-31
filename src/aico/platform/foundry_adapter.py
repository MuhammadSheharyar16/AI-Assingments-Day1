"""
Foundry adapter: the one file in the repository allowed to speak HTTP to
the Microsoft Foundry endpoint. Everything else - retrieval code, evals,
CLIs - depends on aico.platform.model_gateway's typed contract instead,
never on this module directly and never on the HTTP client it uses.

Calls Foundry's unified Model Inference API (provider-agnostic; the model/
deployment alias goes in the request body, not the URL path) - the same
route Day 2's AzureEmbeddingProvider used for embeddings, extended here
with the chat completions endpoint.

Authentication today: an API key read from an environment variable (never
committed - see .env / README), the same mechanism Day 2 used. Task 2
replaces this with the approved identity flow (DefaultAzureCredential /
managed identity); nothing outside this file changes when that happens,
because every other caller only ever sees the Transport protocol's
request/response shape defined in model_gateway.py.

Never log: the request body, the response body, headers, or the API key.
"""
from __future__ import annotations

import os
from urllib.parse import urlencode

import requests

from aico.platform.config import GatewayConfig
from aico.platform.errors import (
    GatewayAuthenticationError,
    GatewayBadRequestError,
    GatewayRateLimitError,
    GatewayServerError,
    GatewayTimeoutError,
    ModelGatewayError,
)
from aico.platform.model_gateway import TransportResult

DEFAULT_API_VERSION = "2024-05-01-preview"
DEFAULT_API_KEY_ENV = "AICO_EMBEDDING_API_KEY"


class FoundryAdapter:
    """Real Transport implementation (see model_gateway.Transport)."""

    def __init__(self, config: GatewayConfig, *, api_key_env: str = DEFAULT_API_KEY_ENV):
        self._config = config
        self._api_key_env = api_key_env

    def embed(self, *, model_alias: str, texts: list[str], timeout_seconds: float) -> TransportResult:
        if not texts:
            return TransportResult(content=[], dimensions=0, token_usage=None)

        url = f"{self._config.endpoint}/models/embeddings?{urlencode({'api-version': DEFAULT_API_VERSION})}"
        body = {"input": texts, "model": model_alias}
        payload = self._post(url, body, timeout_seconds)

        # Foundry is not guaranteed to return items in input order - restore it.
        items = sorted(payload["data"], key=lambda item: item["index"])
        vectors = [item["embedding"] for item in items]
        dimensions = len(vectors[0]) if vectors else 0
        return TransportResult(content=vectors, dimensions=dimensions, token_usage=self._extract_usage(payload))

    def chat(
        self,
        *,
        model_alias: str,
        messages: list[dict],
        max_output_tokens: int | None,
        timeout_seconds: float,
    ) -> TransportResult:
        url = f"{self._config.endpoint}/models/chat/completions?{urlencode({'api-version': DEFAULT_API_VERSION})}"
        body: dict = {"messages": messages, "model": model_alias}
        if max_output_tokens is not None:
            body["max_tokens"] = max_output_tokens
        payload = self._post(url, body, timeout_seconds)

        content = payload["choices"][0]["message"]["content"]
        return TransportResult(content=content, dimensions=None, token_usage=self._extract_usage(payload))

    # ── internals ────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        api_key = os.environ.get(self._api_key_env, "")
        if not api_key:
            raise GatewayAuthenticationError(
                f"{self._api_key_env} is not set (see .env, gitignored, and the README setup "
                "section) - Task 2 replaces this with identity-based auth"
            )
        return {"api-key": api_key, "Content-Type": "application/json"}

    def _post(self, url: str, body: dict, timeout_seconds: float) -> dict:
        headers = self._headers()
        try:
            response = requests.post(url, headers=headers, json=body, timeout=timeout_seconds)
        except requests.Timeout as exc:
            raise GatewayTimeoutError(f"provider call exceeded {timeout_seconds}s", cause=exc) from exc
        except requests.RequestException as exc:
            raise GatewayServerError(f"provider request failed: {exc.__class__.__name__}", cause=exc) from exc

        # Never log headers, the request body or the response - both the
        # API key and the raw prompt/completion/vector content must stay
        # out of normal application logs.
        if response.status_code == 429:
            raise GatewayRateLimitError("provider rate limit exceeded")
        if response.status_code in (401, 403):
            raise GatewayAuthenticationError("provider rejected credentials")
        if response.status_code == 400:
            raise GatewayBadRequestError("provider rejected the request as malformed")
        if response.status_code >= 500:
            raise GatewayServerError(f"provider server error ({response.status_code})")
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise ModelGatewayError(f"unexpected provider response ({response.status_code})", cause=exc) from exc

        return response.json()

    @staticmethod
    def _extract_usage(payload: dict) -> dict[str, int] | None:
        usage = payload.get("usage")
        if not usage:
            return None
        # Only the numeric token-count fields - never any other part of
        # the response - become operational metadata.
        return {k: v for k, v in usage.items() if isinstance(v, int) and not isinstance(v, bool)}
