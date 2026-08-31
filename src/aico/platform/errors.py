"""
Typed errors for the Model Gateway boundary.

Application code (retrieval, evals, CLIs) should only ever catch this
hierarchy - never a raw provider/SDK exception. A raw provider exception
must never cross out of aico.platform.foundry_adapter; every failure that
reaches a caller comes back as one of the classes below, each carrying a
stable `category` string and a `retryable` flag so the gateway's retry
policy (Task 3) can decide what to do without knowing anything about the
provider's own exception types.

Names are prefixed with `Gateway` rather than reusing builtin names like
`TimeoutError` - that avoids silently shadowing the builtin for any module
that does `from aico.platform.errors import *` or a bare `except TimeoutError`.
"""
from __future__ import annotations


class ModelGatewayError(Exception):
    """Base class for every error the gateway raises."""

    category: str = "unknown"
    retryable: bool = False

    def __init__(self, message: str, *, cause: BaseException | None = None):
        super().__init__(message)
        self.cause = cause


class GatewayConfigurationError(ModelGatewayError):
    """Missing or invalid required routing configuration. Never retryable -
    retrying a bad config produces the same failure every time."""

    category = "configuration"
    retryable = False


class GatewayTimeoutError(ModelGatewayError):
    """The call did not complete before its deadline."""

    category = "timeout"
    retryable = True


class GatewayCancelledError(ModelGatewayError):
    """The caller's CancellationToken was set before or during the call."""

    category = "cancelled"
    retryable = False


class GatewayRateLimitError(ModelGatewayError):
    """The provider throttled the request (HTTP 429 or equivalent)."""

    category = "rate_limit"
    retryable = True


class GatewayAuthenticationError(ModelGatewayError):
    """The provider rejected the caller's credentials/identity (401/403).
    Never retryable - the same identity will fail again immediately."""

    category = "authentication"
    retryable = False


class GatewayBadRequestError(ModelGatewayError):
    """The provider rejected the request itself as malformed (400). Never
    retryable - the request needs to change, not be resent."""

    category = "bad_request"
    retryable = False


class GatewayServerError(ModelGatewayError):
    """The provider failed on its own side (5xx or equivalent)."""

    category = "server_error"
    retryable = True


class GatewayRetryCeilingExceededError(ModelGatewayError):
    """A retryable failure kept failing until the configured attempt
    ceiling was reached. Carries the last underlying error as `cause`."""

    category = "retry_ceiling_exceeded"
    retryable = False


class GatewayFallbackBlockedError(ModelGatewayError):
    """Primary call failed and fallback was not taken because an explicit
    policy compatibility check (provider/region/data boundary/risk/budget)
    did not pass. See Task 4 - fallback is never automatic."""

    category = "fallback_blocked"
    retryable = False


_CATEGORY_TO_ERROR: dict[str, type[ModelGatewayError]] = {
    "configuration": GatewayConfigurationError,
    "timeout": GatewayTimeoutError,
    "cancelled": GatewayCancelledError,
    "rate_limit": GatewayRateLimitError,
    "authentication": GatewayAuthenticationError,
    "bad_request": GatewayBadRequestError,
    "server_error": GatewayServerError,
    "retry_ceiling_exceeded": GatewayRetryCeilingExceededError,
    "fallback_blocked": GatewayFallbackBlockedError,
}


def error_for_category(
    category: str, message: str, *, cause: BaseException | None = None
) -> ModelGatewayError:
    """Build the typed error for a normalized failure category. Unknown
    categories fall back to the base `ModelGatewayError` rather than
    raising a KeyError - a provider surfacing a category this repository
    doesn't yet name should still come back as *a* typed error, not a
    crash in the normalization path itself."""
    error_cls = _CATEGORY_TO_ERROR.get(category, ModelGatewayError)
    return error_cls(message, cause=cause)
