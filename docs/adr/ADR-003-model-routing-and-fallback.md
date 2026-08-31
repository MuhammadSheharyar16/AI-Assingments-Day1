# ADR-003 — Model Routing and Fallback

## Status

Accepted

## Context

Day 2's `AzureEmbeddingProvider` called the Foundry REST endpoint directly
(`requests`, an API key from an environment variable), with no timeout
policy, no bounded retry, no routing/fallback safety and no structured
metadata. Day 3 adds the chat side of the same traffic on top of that same
ad-hoc pattern. Left alone, every future caller (retrieval code today,
whatever calls chat next) would grow its own copy of "how do I call the
model" - its own auth, its own retry-or-not decision, its own idea of what
counts as a timeout. A single **Model Gateway boundary**
(`src/aico/platform/model_gateway.py`) puts all of that in one place: chat
and embed share one typed contract, one identity flow, one retry policy,
one routing/fallback policy and one metadata/logging shape. Application
and retrieval code depends on that contract - never on the provider SDK,
never on `aico.platform.foundry_adapter` directly (enforced by a
repository-wide search for the HTTP client import - see Task 1's required
check, reproduced below).

## Decision

`ModelGateway.embed()` / `.chat()` take typed requests (`EmbedRequest`,
`ChatRequest`) and return typed results (`EmbedResult`, `ChatResult`) with
sanitized `CallMetadata`. Internally, a `Transport` protocol
(`embed`/`chat` in, `TransportResult` out) is the only thing the gateway
calls - `FoundryAdapter` (`foundry_adapter.py`) is the one real
implementation and the only file in the repository that imports `requests`;
every test injects a fake transport instead.

### Deployment aliases

`config/model-routing.yaml` names a `chat` alias and an `embedding` alias
(`models.chat.alias` / `models.embedding.alias`); `GatewayConfig` (Task 2)
loads and validates them. Callers never hardcode a deployment name - they
either omit `model_alias` on the request (the gateway fills in the
configured alias) or override it explicitly per call, but the *default*
routing decision lives in one file, not scattered across every call site.
That means swapping a deployment (a new model version, a region move) is a
one-line config change, not a grep-and-replace across the codebase - and a
reviewer can answer "what model actually serves this request" by reading
one file.

### Authentication

`azure.identity.DefaultAzureCredential` (managed identity in Azure, local
`az login` or environment-variable service-principal credentials
otherwise), via `FoundryAdapter`. A bearer token is requested per configured
scope (`AICO_FOUNDRY_TOKEN_SCOPE`, default
`https://cognitiveservices.azure.com/.default`), cached, and refreshed 120s
before expiry. No API key, bearer token or client secret is ever a literal
in source or in `config/model-routing.yaml` - constructing the adapter
makes no network call, so importing/building it needs no cloud access;
only an actual `embed()`/`chat()` call does. Every gateway/adapter test
instead injects a fake `TokenCredential` or a fake `Transport`, so the full
deterministic suite needs neither real cloud access nor a real identity.

### Timeout and cancellation

Every `EmbedRequest`/`ChatRequest` carries `timeout_seconds` (defaulting to
`config.resilience.timeout_seconds`), passed straight to
`requests.post(..., timeout=...)` in the adapter - so a call has a hard
deadline even without cancellation. `CancellationToken` is a cooperative
`threading.Event` wrapper a caller can `.cancel()` from another thread or
its own deadline; the gateway checks it before dispatching a call and again
before every retry attempt (including the moment a backoff wait returns),
so cancellation stops the operation from ever starting or from retrying
again - it cannot interrupt one HTTP call already in flight against a
transport that doesn't support that itself, but it does guarantee no
further attempt follows.

### Retry policy

Bounded exponential backoff with full jitter, entirely config-driven
(`resilience.retry` in `config/model-routing.yaml`):
`delay = min(base_delay_ms * 2**attempt, max_delay_ms)`, scaled by a random
factor in `[0, 1)` when `jitter: true` (spreads out concurrent retries
instead of every caller waking at the same instant). `max_attempts` is a
hard ceiling - the retry loop (`ModelGateway._call_with_retry`) always
terminates, either by success, by a non-retryable error, by cancellation,
or by raising `GatewayRetryCeilingExceededError` (chaining the last
failure) once the ceiling is hit. There is no unbounded loop.

**Retryable**: `timeout`, `rate_limit`, `server_error` - transient,
provider-side or network-side conditions where the same request might
succeed on a later attempt.
**Non-retryable**: `authentication`, `bad_request` - the request or the
credential is wrong; resending it unchanged only reproduces the same
failure, so both fail on the first attempt with zero retries.

### Error normalization

`aico.platform.errors.ModelGatewayError` and five typed subclasses
(`GatewayTimeoutError`, `GatewayRateLimitError`, `GatewayAuthenticationError`,
`GatewayBadRequestError`, `GatewayServerError`), each with a stable
`category` string and `retryable` flag. `FoundryAdapter._post()` maps HTTP
429 → rate_limit, 401/403 → authentication, 400 → bad_request, 5xx →
server_error, a `requests.Timeout` → timeout, any other network exception →
server_error, and any other non-2xx status still becomes a base
`ModelGatewayError` via `raise_for_status()` - never a raw `requests`
exception. Application code only ever branches on these categories, never
on a provider-specific exception class.

### Routing and fallback

Fallback to a second `Transport` (`fallback_transport`) happens **only**
when all of: (a) a fallback transport is actually configured - policy alone
never conjures one up, (b) `routing.fallback.enabled` is `true`, and (c)
every axis `routing.fallback.require_compatibility` marks as required
(`provider`, `region`, `data_boundary`, `risk`, `budget`) is actually
compatible between `routing.primary` and `routing.fallback`'s declared
route. `budget` compatibility is evaluated pre-flight from the request
itself (item count vs. `budgets.embedding.max_items_per_call`, or requested
`max_output_tokens` vs. `budgets.chat.max_output_tokens`) - not silently
retried on a request already known to exceed the limit. Any missing
condition raises `GatewayFallbackBlockedError`, chaining the primary
failure as `cause` - the caller always gets a deterministic, explainable
result. Cancellation is never treated as a trigger for fallback; it always
propagates as itself.

**Why silent cross-provider/data-boundary fallback is prohibited**: a
data-residency or risk-classification requirement that holds for the
primary route (e.g. "stays in the UK region", "microsoft-foundry only")
does not automatically hold for whatever else happens to be configured as
a fallback. A fallback silently taken on failure could send the same
request to a different region, a different data boundary or a different
risk tier without anyone deciding that was acceptable for this specific
workload. Requiring an *explicit*, per-axis compatibility check - one that
blocks by default and only proceeds when every required axis actually
matches - turns "did we just cross a boundary we shouldn't have" from a
runtime accident into a config-time decision someone had to make on
purpose.

### Metadata

Every successful call returns `CallMetadata`: `operation`, `model_alias`,
`latency_ms`, `retry_count`, `token_usage` (`None` if the provider didn't
report one - never invented), `budget_status`
(`within_budget`/`exceeded`/`unknown`), `used_fallback`. Never prompt or
completion text - by construction, `CallMetadata` has no field that could
hold either.

### Logging

`model_gateway.py`'s logger emits one structured line per event (call
succeeded, a retry, ceiling exceeded, a non-retryable failure, an
unnormalized failure, fallback blocked/attempted) built only from the same
already-sanitized fields as `CallMetadata` - operation, model_alias,
category, counts. No log call anywhere in the file is ever given a
request's texts/messages, a result's content, or `str(exc)` (an exception's
free-text message) - so a prompt, a completion, or anything an error
message might someday contain can't reach a log line through this path.
`foundry_adapter.py` - the only file that ever builds an Authorization
header/bearer token - has no logging call in it at all, so the token is
never a candidate for a log line either.

## Consequences

**Simpler/safer**: one place to change auth, retry policy, or fallback
rules instead of N call sites; every failure a caller sees is one of five
typed categories, never a provider-specific exception; fallback requires an
explicit, auditable policy decision instead of "whatever happens on
failure"; the full test suite is deterministic and offline (149 tests, no
network call, no real cloud provider used to manufacture a failure case).

**Trade-offs/limitations**: `config/model-routing.yaml` has no second
endpoint/deployment for a real fallback resource today - `routing.fallback`
only describes compatibility *metadata*, not a second connection target -
so `ModelGateway.from_config()` never wires a real `fallback_transport` yet,
even when `routing.fallback.enabled` is `true`. Enabling a real fallback
path is a Day 4+ setup task (a second `FoundryAdapter` pointed at an
approved resource), not a code change. Retry/backoff timing is per-call,
not budget-aware across a whole request batch - a caller issuing many
calls in a retry storm each retries independently. `risk_class` and
`data_boundary` compatibility are exact-string-equality checks against
config, not a real risk-scoring engine.

## Day 2 regression evidence

This checkout has no lead-provided Microsoft Foundry endpoint/identity, so
Day 2's live Hit@1/Hit@5/MRR numbers can't be re-run against the real
endpoint here - that capture belongs in a future EOD run once access is
available. What's proven now, deterministically:

1. Every existing Day 1/Day 2 test still passes unchanged - none of
   `chunker.py`, `bm25.py`, `ingest.py`, `vector_index.py`, `hybrid.py` or
   the search/eval logic was touched by the Day 3 migration.
2. `tests/test_day2_regression.py` proves the migration itself is
   behavior-preserving: routing the same text through
   `AzureEmbeddingProvider → ModelGateway → a fake transport backed by
   FakeEmbeddingProvider` produces bit-identical vectors, preserved batch
   order, and identical `vector_search`/`hybrid_search` rankings to calling
   `FakeEmbeddingProvider` directly - the gateway is a transparent
   pass-through, so whatever a real provider returns flows through
   unchanged, the same way a fake one demonstrably does.

See `artifacts/day03/gateway_demo.md` for the actual command output.
