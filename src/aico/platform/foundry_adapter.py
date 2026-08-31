"""
Foundry adapter: the one file in the repository allowed to speak HTTP to
the Microsoft Foundry endpoint. Everything else - retrieval code, evals,
CLIs - depends on aico.platform.model_gateway's typed contract instead,
never on this module directly and never on the HTTP client it uses.

Calls Foundry's unified Model Inference API (provider-agnostic; the model/
deployment alias goes in the request body, not the URL path) - the same
route Day 2's AzureEmbeddingProvider used for embeddings, extended here
with the chat completions endpoint.

Authentication (Task 2): the approved identity flow, not a key. A
`TokenCredential` (`azure.identity.DefaultAzureCredential` by default -
managed identity when running in Azure, `az login`/environment-variable
service-principal credentials locally, whatever the credential chain finds
first - or any other TokenCredential a caller injects) is asked for a
bearer token per call, cached until shortly before it expires. No API key,
bearer token, password or client secret is ever a literal in this file or
in config/model-routing.yaml - identity is a chain of *how to obtain* a
credential, not a value stored anywhere in the repository. Constructing
`DefaultAzureCredential()` itself makes no network call - only actually
requesting a token does, so importing/constructing this adapter never
requires cloud access; only a real `embed()`/`chat()` call does.

Never log: the request body, the response body, headers, or the token.
"""
from __future__ import annotations

import os
import time
from urllib.parse import urlencode

import requests
from azure.core.credentials import AccessToken, TokenCredential
from azure.core.exceptions import ClientAuthenticationError

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

# Azure AI Foundry / Cognitive Services resources are authorized under this
# resource scope regardless of which model provider sits behind a given
# deployment. Overridable for a differently-scoped resource without a code
# change.
DEFAULT_TOKEN_SCOPE_ENV = "AICO_FOUNDRY_TOKEN_SCOPE"
DEFAULT_TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"

# A user-assigned managed identity's client ID, when the environment needs
# one specified explicitly (DefaultAzureCredential picks a system-assigned
# identity, or the only user-assigned one, automatically otherwise).
MANAGED_IDENTITY_CLIENT_ID_ENV = "AICO_FOUNDRY_MANAGED_IDENTITY_CLIENT_ID"

# Refresh the cached token a little before its real expiry, so a call
# in flight never starts with a token that expires mid-request.
TOKEN_REFRESH_MARGIN_SECONDS = 120


class FoundryAdapter:
    """Real Transport implementation (see model_gateway.Transport)."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        credential: TokenCredential | None = None,
        token_scope: str | None = None,
    ):
        self._config = config
        # Not constructed until first needed (see _get_credential) unless a
        # caller injects one directly - tests do exactly that with a fake
        # TokenCredential, so no test ever needs a real identity or network
        # access to exercise header construction, caching or the
        # authentication-failure path.
        self._credential: TokenCredential | None = credential
        self._token_scope = token_scope or os.environ.get(DEFAULT_TOKEN_SCOPE_ENV, DEFAULT_TOKEN_SCOPE)
        self._cached_token: AccessToken | None = None

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

    def _get_credential(self) -> TokenCredential:
        if self._credential is None:
            # Local import: a reviewer running only the deterministic unit
            # tests (which always inject a credential or never construct a
            # FoundryAdapter at all) never needs azure-identity imported,
            # let alone a real credential chain resolved.
            from azure.identity import DefaultAzureCredential

            client_id = os.environ.get(MANAGED_IDENTITY_CLIENT_ID_ENV)
            self._credential = DefaultAzureCredential(
                managed_identity_client_id=client_id
            ) if client_id else DefaultAzureCredential()
        return self._credential

    def _bearer_token(self) -> str:
        now = time.time()
        token = self._cached_token
        if token is None or token.expires_on - TOKEN_REFRESH_MARGIN_SECONDS <= now:
            try:
                token = self._get_credential().get_token(self._token_scope)
            except ClientAuthenticationError as exc:
                raise GatewayAuthenticationError(
                    "identity authentication failed - no credential in the approved chain "
                    "(managed identity / az login / environment) could obtain a token for "
                    f"scope {self._token_scope!r}",
                    cause=exc,
                ) from exc
            self._cached_token = token
        return token.token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._bearer_token()}", "Content-Type": "application/json"}

    def _post(self, url: str, body: dict, timeout_seconds: float) -> dict:
        headers = self._headers()
        try:
            response = requests.post(url, headers=headers, json=body, timeout=timeout_seconds)
        except requests.Timeout as exc:
            raise GatewayTimeoutError(f"provider call exceeded {timeout_seconds}s", cause=exc) from exc
        except requests.RequestException as exc:
            raise GatewayServerError(f"provider request failed: {exc.__class__.__name__}", cause=exc) from exc

        # Never log headers, the request body or the response - the bearer
        # token and the raw prompt/completion/vector content must both
        # stay out of normal application logs.
        if response.status_code == 429:
            raise GatewayRateLimitError("provider rate limit exceeded")
        if response.status_code in (401, 403):
            raise GatewayAuthenticationError("provider rejected the identity's token")
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
