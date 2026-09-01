"""
Validated loading of config/model-routing.yaml - the single source of
deployment aliases, endpoint reference, resilience (timeout/retry) settings,
budgets and routing/fallback policy the gateway runs on.

Contains configuration only. No secrets: the actual Foundry endpoint is
read from the environment variable this file *names*
(`foundry.endpoint_env`), never a literal URL, and credentials never appear
here at all (see aico.platform.foundry_adapter / Task 2 identity flow).

Missing or invalid required configuration fails loudly with
GatewayConfigurationError - there is no silent fallback to an unsafe
default for chat alias, embedding alias, endpoint, timeout, retry ceiling
or budgets. A value still shaped like the supplied pack's placeholder
(`__LIKE_THIS__`) is treated as missing, not as a working alias - see
day03_pack/README.md: "Do not hardcode them just to make the example run."

No third-party YAML library is used here (PyYAML has no prebuilt wheel for
the interpreter this repository runs on and cannot be built without a C
toolchain in this environment). `_parse_simple_yaml` below handles the
small, list-free, indentation-nested subset of YAML the pack's example
actually uses - `key: value` pairs and nested mappings, comments, quoted
strings, booleans, null and numbers. It is not a general YAML parser and
does not need to be.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from aico.platform.errors import GatewayConfigurationError

DEFAULT_CONFIG_PATH = Path("config/model-routing.yaml")


# ── Minimal YAML-subset parser ──────────────────────────────────────────

def _parse_scalar(raw: str) -> object:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    lowered = raw.lower()
    if lowered in ("true", "yes"):
        return True
    if lowered in ("false", "no"):
        return False
    if lowered in ("null", "~", "none", ""):
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _parse_simple_yaml(text: str) -> dict:
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        stripped_raw = raw_line.strip()
        if not stripped_raw or stripped_raw.startswith("#"):
            continue
        if "\t" in raw_line:
            raise GatewayConfigurationError(f"line {lineno}: tabs are not supported in config/model-routing.yaml")

        # Strip a trailing inline comment (" # ..."). None of this file's
        # values ever contain a literal '#', so no quote-awareness needed.
        line = raw_line.split(" #", 1)[0].rstrip() if " #" in raw_line else raw_line
        stripped = line.strip()
        if not stripped:
            continue

        indent = len(line) - len(line.lstrip(" "))
        if ":" not in stripped:
            raise GatewayConfigurationError(f"line {lineno}: expected 'key: value', got {raw_line!r}")

        key, _, raw_value = stripped.partition(":")
        key = key.strip()
        raw_value = raw_value.strip()
        if not key:
            raise GatewayConfigurationError(f"line {lineno}: empty key in {raw_line!r}")

        while indent <= stack[-1][0]:
            stack.pop()
            if not stack:
                raise GatewayConfigurationError(f"line {lineno}: inconsistent indentation in {raw_line!r}")
        parent = stack[-1][1]

        if raw_value == "":
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_scalar(raw_value)

    return root


def _looks_like_placeholder(value: object) -> bool:
    return isinstance(value, str) and value.startswith("__") and value.endswith("__")


def _require(d: dict, key: str, path: str) -> object:
    if not isinstance(d, dict) or key not in d or d[key] in (None, ""):
        raise GatewayConfigurationError(f"missing required config key: {path}.{key}")
    value = d[key]
    if _looks_like_placeholder(value):
        raise GatewayConfigurationError(
            f"{path}.{key} is still the pack's placeholder value ({value!r}) - "
            "replace it through your approved configuration process before use"
        )
    return value


def _require_positive_number(d: dict, key: str, path: str) -> float:
    value = _require(d, key, path)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise GatewayConfigurationError(f"{path}.{key} must be a positive number, got {value!r}")
    return value


def _require_positive_int(d: dict, key: str, path: str) -> int:
    value = _require(d, key, path)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise GatewayConfigurationError(f"{path}.{key} must be a positive integer, got {value!r}")
    return value


def _require_bool(d: dict, key: str, path: str) -> bool:
    if not isinstance(d, dict) or key not in d:
        raise GatewayConfigurationError(f"missing required config key: {path}.{key}")
    value = d[key]
    if not isinstance(value, bool):
        raise GatewayConfigurationError(f"{path}.{key} must be true/false, got {value!r}")
    return value


def _require_str(d: dict, key: str, path: str) -> str:
    value = _require(d, key, path)
    if not isinstance(value, str):
        raise GatewayConfigurationError(f"{path}.{key} must be a string, got {value!r}")
    return value


def _resolve_alias(d: dict, path: str) -> str:
    """Resolve a models.chat/models.embedding alias from either a literal
    `alias` (the original shape) or - mirroring foundry.endpoint_env - an
    `alias_env` naming an environment variable to read the real alias
    from, so a deployment alias never has to be a literal in
    config/model-routing.yaml. Exactly one of the two may be given.

    Unlike `GatewayConfig.endpoint` (resolved lazily, on every access,
    specifically so building/validating a GatewayConfig never requires
    the endpoint's env var to be set), an alias is resolved once, here,
    during `load_gateway_config()` - the alias is load-time-required
    config, not a late-bound deployment detail."""
    has_alias = isinstance(d, dict) and d.get("alias") not in (None, "")
    has_alias_env = isinstance(d, dict) and d.get("alias_env") not in (None, "")

    if has_alias and has_alias_env:
        raise GatewayConfigurationError(f"{path}: specify either 'alias' or 'alias_env', not both")
    if not has_alias_env:
        return _require_str(d, "alias", path)

    env_var = _require_str(d, "alias_env", path)
    value = os.environ.get(env_var, "")
    if not value:
        raise GatewayConfigurationError(
            f"environment variable {env_var!r} (named by {path}.alias_env) is not set"
        )
    if _looks_like_placeholder(value):
        raise GatewayConfigurationError(
            f"{path}.alias_env={env_var!r} resolves to the pack's placeholder value ({value!r}) - "
            "replace it through your approved configuration process before use"
        )
    return value


# ── Typed configuration model ───────────────────────────────────────────

@dataclass(frozen=True)
class ModelAliases:
    chat: str
    embedding: str


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int
    base_delay_ms: int
    max_delay_ms: int
    jitter: bool


@dataclass(frozen=True)
class ResilienceConfig:
    timeout_seconds: float
    retry: RetryConfig


@dataclass(frozen=True)
class ChatBudget:
    max_input_tokens: int
    max_output_tokens: int


@dataclass(frozen=True)
class EmbeddingBudget:
    max_items_per_call: int


@dataclass(frozen=True)
class BudgetsConfig:
    chat: ChatBudget
    embedding: EmbeddingBudget


@dataclass(frozen=True)
class RouteEndpoint:
    provider: str
    region: str
    data_boundary: str
    risk_class: str


@dataclass(frozen=True)
class FallbackPolicy:
    enabled: bool
    route: RouteEndpoint | None
    require_compatibility: dict[str, bool]


@dataclass(frozen=True)
class RoutingPolicy:
    primary: RouteEndpoint
    fallback: FallbackPolicy


@dataclass(frozen=True)
class GatewayConfig:
    version: str
    endpoint_env: str
    models: ModelAliases
    resilience: ResilienceConfig
    budgets: BudgetsConfig
    routing: RoutingPolicy

    @property
    def endpoint(self) -> str:
        """The Foundry endpoint URL - read from the environment variable
        this config *names* (`foundry.endpoint_env`), never stored here.
        Evaluated lazily so constructing/validating a GatewayConfig never
        requires the environment variable to already be set."""
        value = os.environ.get(self.endpoint_env, "")
        if not value:
            raise GatewayConfigurationError(
                f"environment variable {self.endpoint_env!r} (named by "
                "config/model-routing.yaml's foundry.endpoint_env) is not set - "
                "the Foundry endpoint is an environment/setup input, never hardcoded"
            )
        return value


REQUIRED_COMPATIBILITY_KEYS = ("provider", "region", "data_boundary", "risk", "budget")


def _build_route(d: dict, path: str) -> RouteEndpoint:
    return RouteEndpoint(
        provider=_require_str(d, "provider", path),
        region=_require_str(d, "region", path),
        data_boundary=_require_str(d, "data_boundary", path),
        risk_class=_require_str(d, "risk_class", path),
    )


def _build_config(raw: dict, *, source: Path) -> GatewayConfig:
    def section(d: dict, key: str, path: str) -> dict:
        if key not in d or not isinstance(d[key], dict):
            raise GatewayConfigurationError(f"{source}: missing required config section: {path}.{key}")
        return d[key]

    version = _require_str(raw, "version", "$")

    foundry = section(raw, "foundry", "$")
    endpoint_env = _require_str(foundry, "endpoint_env", "foundry")

    models_raw = section(raw, "models", "$")
    chat_alias = _resolve_alias(section(models_raw, "chat", "models"), "models.chat")
    embedding_alias = _resolve_alias(section(models_raw, "embedding", "models"), "models.embedding")

    resilience_raw = section(raw, "resilience", "$")
    timeout_seconds = _require_positive_number(resilience_raw, "timeout_seconds", "resilience")
    retry_raw = section(resilience_raw, "retry", "resilience")
    retry = RetryConfig(
        max_attempts=_require_positive_int(retry_raw, "max_attempts", "resilience.retry"),
        base_delay_ms=_require_positive_int(retry_raw, "base_delay_ms", "resilience.retry"),
        max_delay_ms=_require_positive_int(retry_raw, "max_delay_ms", "resilience.retry"),
        jitter=_require_bool(retry_raw, "jitter", "resilience.retry"),
    )
    if retry.max_delay_ms < retry.base_delay_ms:
        raise GatewayConfigurationError(
            "resilience.retry.max_delay_ms must be >= base_delay_ms "
            f"(got max_delay_ms={retry.max_delay_ms}, base_delay_ms={retry.base_delay_ms})"
        )

    budgets_raw = section(raw, "budgets", "$")
    chat_budget_raw = section(budgets_raw, "chat", "budgets")
    embedding_budget_raw = section(budgets_raw, "embedding", "budgets")
    budgets = BudgetsConfig(
        chat=ChatBudget(
            max_input_tokens=_require_positive_int(chat_budget_raw, "max_input_tokens", "budgets.chat"),
            max_output_tokens=_require_positive_int(chat_budget_raw, "max_output_tokens", "budgets.chat"),
        ),
        embedding=EmbeddingBudget(
            max_items_per_call=_require_positive_int(
                embedding_budget_raw, "max_items_per_call", "budgets.embedding"
            ),
        ),
    )

    routing_raw = section(raw, "routing", "$")
    primary = _build_route(section(routing_raw, "primary", "routing"), "routing.primary")

    fallback_raw = section(routing_raw, "fallback", "routing")
    fallback_enabled = _require_bool(fallback_raw, "enabled", "routing.fallback")
    compat_raw = section(fallback_raw, "require_compatibility", "routing.fallback")
    require_compatibility = {
        key: _require_bool(compat_raw, key, "routing.fallback.require_compatibility")
        for key in REQUIRED_COMPATIBILITY_KEYS
    }

    fallback_route: RouteEndpoint | None = None
    if fallback_enabled:
        # Only validated (and required to be a real, non-placeholder value)
        # when fallback is actually turned on - an unused, still-placeholder
        # fallback route is not a configuration error by itself.
        fallback_route = _build_route(fallback_raw, "routing.fallback")

    routing = RoutingPolicy(
        primary=primary,
        fallback=FallbackPolicy(
            enabled=fallback_enabled,
            route=fallback_route,
            require_compatibility=require_compatibility,
        ),
    )

    return GatewayConfig(
        version=version,
        endpoint_env=endpoint_env,
        models=ModelAliases(chat=chat_alias, embedding=embedding_alias),
        resilience=ResilienceConfig(timeout_seconds=timeout_seconds, retry=retry),
        budgets=budgets,
        routing=routing,
    )


def load_gateway_config(path: str | Path = DEFAULT_CONFIG_PATH) -> GatewayConfig:
    """Load and validate config/model-routing.yaml. Raises
    GatewayConfigurationError with a specific, actionable message for any
    missing/invalid/placeholder required field - never substitutes a
    default for required routing data."""
    path = Path(path)
    if not path.exists():
        raise GatewayConfigurationError(
            f"routing configuration not found: {path} - copy day03_pack/config/"
            "model-routing.example.yaml to config/model-routing.yaml and fill it in"
        )
    text = path.read_text(encoding="utf-8")
    raw = _parse_simple_yaml(text)
    return _build_config(raw, source=path)
