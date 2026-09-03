"""
Day 6 Task 2 — trusted identity context.

Tenant and user identity are authorization context, not operational
convenience (Day 6 rule: "Correlation context may be generated.
Authorization context may not."). This module is the ONLY place in the
API that is allowed to decide what `tenant_id`/`user_id` a request is
acting as, and it decides that exclusively from verified authentication
claims - never from anything the caller wrote into the request body or an
arbitrary header.

Trust boundary implemented here:

    Authorization: Bearer <token>
            v  verify signature (PyJWT, HS256, server-held secret)
    verified claims payload
            v  require non-empty tenant_id + user_id
    TrustedIdentity

A request whose token is missing, malformed, unverifiable, or whose
verified claims lack a non-empty `tenant_id`/`user_id` is rejected before
it ever reaches `GroundedAnswerService` - `app.py` wires
`get_trusted_identity` as a required dependency on `POST /ask`, so the
Day 5 pipeline never runs for an untrusted caller.

The specific mechanism (a locally-verified HS256 JWT) is a lab stand-in -
`api_contract_guidance.md` / the assignment brief are explicit that "the
exact authentication middleware/provider is environment-dependent... the
Day 6 requirement is the trust boundary, not a specific external identity
product." A real deployment swaps `_decode_bearer_token` for whatever it
actually verifies against (Entra ID, an API gateway's injected verified
claims, etc.) without changing `build_trusted_identity` or the trust
boundary itself. Tests do not need a real token at all - they override
`get_trusted_identity` (or call `build_trusted_identity` directly against
`identity_claim_cases.json`'s fixtures) with `app.dependency_overrides`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

import jwt
from fastapi import Request

from aico.api.errors import ApiError

# Lab-only: names the environment variable holding the HS256 verification
# secret. Never a literal secret in source, never a default value here -
# an unset secret means every request is rejected (fail closed), not
# silently trusted.
AUTH_JWT_SECRET_ENV = "AICO_AUTH_JWT_SECRET"


@dataclass(frozen=True)
class TrustedIdentity:
    """Verified tenant/user context for one request. Never constructed
    from unverified input - see `build_trusted_identity`."""

    tenant_id: str
    user_id: str


class IdentityError(ApiError):
    """Raised when trusted identity cannot be established: missing/expired/
    unverifiable token, or verified claims that lack tenant_id/user_id.
    Subclasses `ApiError` (errors.py, Task 4) so `register_error_handlers`
    maps it onto the shared error envelope like every other typed API
    failure - no bespoke handler needed. Its message (`.message`, also
    kept as `.reason` for readability at call sites) is always safe to
    return to the caller - it never includes the token, the secret, or a
    raw library exception."""

    status_code = 401
    error_code = "trusted_identity_rejected"

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def build_trusted_identity(claims: Mapping[str, object]) -> TrustedIdentity:
    """The one place that turns a claims mapping into a `TrustedIdentity`.
    Deliberately independent of *how* `claims` was obtained/verified, so
    it is directly testable against `identity_claim_cases.json` without a
    real token, and reusable by any future claims provider."""

    tenant_id = claims.get("tenant_id")
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise IdentityError("trusted claims are missing a non-empty tenant_id")

    user_id = claims.get("user_id")
    if not isinstance(user_id, str) or not user_id.strip():
        raise IdentityError("trusted claims are missing a non-empty user_id")

    return TrustedIdentity(tenant_id=tenant_id, user_id=user_id)


def _decode_bearer_token(request: Request) -> Mapping[str, object]:
    """Extract and verify the bearer token's signature, returning the
    verified claims payload. Never trusts an unverified header value -
    a caller cannot forge claims without the server-held secret."""

    auth_header = request.headers.get("authorization", "")
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise IdentityError("missing or malformed Authorization bearer token")

    secret = os.environ.get(AUTH_JWT_SECRET_ENV)
    if not secret:
        # Fail closed: an unconfigured verification key is a server setup
        # error, never a reason to accept an unverified token.
        raise IdentityError("trusted identity verification is not configured on this server")

    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise IdentityError(f"trusted identity token failed verification: {exc.__class__.__name__}") from exc


def get_trusted_identity(request: Request) -> TrustedIdentity:
    """Default FastAPI dependency: verify the bearer token, then require
    non-empty tenant_id/user_id in its verified claims. Wired onto
    `POST /ask` as a required parameter (app.py) so an untrusted caller
    is rejected before `GroundedAnswerService.answer()` ever runs."""

    claims = _decode_bearer_token(request)
    return build_trusted_identity(claims)
