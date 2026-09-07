# Day 3 Gateway Demonstration

All output below is copy/pasted from an actual run in this checkout on
2026-09-07, not hand-transcribed. Regenerate scenarios 1-7 with:

```
python scripts/day03_gateway_demo.py
```

**Correction (2026-09-07)**: a validation pass found that a non-retryable
primary failure (`authentication`/`bad_request`) could still trigger a
policy-compatible fallback - the fallback gate checked the five
compatibility axes but never whether the primary failure was retryable in
the first place, so an identity/credential problem at the primary could
silently succeed through a different route. `ModelGateway._call_with_fallback`
now only ever considers fallback when the primary failure is
`GatewayRetryCeilingExceededError` (a retryable category that exhausted its
own retry ceiling) - never for a non-retryable category. The same pass
found `retry_count` undercounted: a successful fallback reported only the
fallback leg's own retry count, discarding however many attempts the
primary spent before giving up. `GatewayRetryCeilingExceededError` now
carries `attempts_made`, and a successful fallback's `retry_count` is
`primary_attempts_made + fallback_retry_count`. Scenario 7 below and
`tests/test_model_gateway_routing.py`'s three new tests
(`test_authentication_failure_at_primary_never_triggers_fallback`,
`test_bad_request_failure_at_primary_never_triggers_fallback`,
`test_fallback_retry_count_folds_in_the_primarys_spent_attempts`) cover
both fixes.

**Environment note**: this checkout has no lead-provided Microsoft Foundry
endpoint or identity (`config/model-routing.yaml` still carries the pack's
placeholder aliases - see Task 2's config validation, which rejects them by
design rather than silently accepting them to make a demo run). Scenarios
1-6 below therefore run against a fake transport, exercising the exact same
`ModelGateway`/`CallMetadata`/retry/fallback code path a real
`FoundryAdapter` response would - substituting only the network call
itself, which is also how every automated test in this repository proves
this behavior (see `tests/test_model_gateway*.py`,
`tests/test_foundry_adapter_normalization.py`). Nothing about the gateway
code changes to go from this to a live capture - only which `Transport` is
passed to `ModelGateway` (a real `FoundryAdapter` instead of a fake).
`artifacts/day03/gateway_demo.md` should be re-captured against the real
endpoint once that access is available.

No prompt, completion, credential or authorization header appears anywhere
below, by construction - the demo script never even builds a real one (see
its docstring).

## 1. Successful embed call through the gateway

```
== 1. Successful embed call through the gateway ==
    vectors returned: 2 (dimensions=3)
[embed] success - sanitized metadata:
    operation = 'embed'
    model_alias = 'demo-embed-alias'
    latency_ms = 0.01
    retry_count = 0
    token_usage = None
    budget_status = 'within_budget'
    used_fallback = False
```

## 2. Successful chat call through the gateway

Per Task 7 ("proving the gateway call path and sanitized metadata is
enough - do not turn this artifact into a prompt-quality exercise"), the
completion content itself is never printed.

```
== 2. Successful chat call through the gateway ==
    (completion content intentionally not printed - gateway call path and metadata only, per Task 7)
[chat] success - sanitized metadata:
    operation = 'chat'
    model_alias = 'demo-chat-alias'
    latency_ms = 0.008
    retry_count = 0
    token_usage = {'prompt_tokens': 42, 'completion_tokens': 17}
    budget_status = 'within_budget'
    used_fallback = False
```

## 3. Retryable failure that later succeeds

`rate_limit` on the first attempt, `success` on the second - `retry_count`
in the returned metadata reports exactly one retry, matching the two
transport calls actually made.

```
== 3. Retryable failure that later succeeds (rate_limit -> success) ==
    transport calls made: 2 (1 retryable failure + 1 success)
[embed] success - sanitized metadata:
    operation = 'embed'
    model_alias = 'demo-embed-alias'
    latency_ms = 228.192
    retry_count = 1
    token_usage = None
    budget_status = 'within_budget'
    used_fallback = False
```

## 4. Timeout

Three scripted timeouts against `resilience.retry.max_attempts = 3` -
normalized to `GatewayTimeoutError` on each attempt, and the retry ceiling
raises `GatewayRetryCeilingExceededError` chaining it, rather than looping
forever.

```
== 4. Timeout, normalized and retried to the configured ceiling ==
gateway.retry_ceiling_exceeded operation=embed model_alias=demo-embed-alias category=timeout max_attempts=3
[embed] failed - GatewayRetryCeilingExceededError (category=retry_ceiling_exceeded, retryable=False)
    caused by: GatewayTimeoutError (category=timeout)
    max_attempts (config) = 3
```

## 5. Non-retryable failure

`bad_request` fails on the first attempt - one transport call, no retry.

```
== 5. Non-retryable failure (bad_request), fails immediately ==
gateway.call_failed operation=chat model_alias=demo-chat-alias category=bad_request retryable=False
[chat] failed - GatewayBadRequestError (category=bad_request, retryable=False)
    transport calls made: 1 (no retry attempted)
```

## 6. Blocked fallback

Primary fails with `server_error` and exhausts its own retry ceiling first
(three attempts, per config); fallback is configured and enabled, but its
declared route's region (`us-east`) doesn't match the primary's
(`uk-south`), and `region` is a required compatibility axis - so fallback
is blocked and the (never-invoked) fallback transport's call count stays
at zero.

```
== 6. Blocked fallback (region mismatch) ==
gateway.retry_ceiling_exceeded operation=chat model_alias=demo-chat-alias category=server_error max_attempts=3
gateway.fallback_blocked operation=chat model_alias=demo-chat-alias blocked_axes=region
[chat] failed - GatewayFallbackBlockedError (category=fallback_blocked, retryable=False)
    caused by: GatewayRetryCeilingExceededError (category=retry_ceiling_exceeded)
    primary.region='uk-south' fallback.region='us-east'
    fallback transport call count: 0 (never invoked)
```

## 7. Non-retryable primary failure is never a fallback candidate

Fallback is fully **compatible and enabled** here - the only thing that
would previously have let this succeed via fallback. `authentication` is a
non-retryable category, so `GatewayAuthenticationError` propagates
unchanged and the (never-invoked) fallback transport's call count stays at
zero, exactly like scenario 5's `bad_request` case (immediate failure, no
retry) but now also proven never to reach the fallback gate at all.

```
== 7. Non-retryable primary failure is never a fallback candidate ==
gateway.call_failed operation=chat model_alias=demo-chat-alias category=authentication retryable=False
gateway.fallback_not_applicable operation=chat model_alias=demo-chat-alias reason=primary_failure_not_retryable category=authentication
[chat] failed - GatewayAuthenticationError (category=authentication, retryable=False)
    primary transport calls: 1 (non-retryable - no retry either)
    fallback transport call count: 0 (never even considered - fallback is compatible AND enabled, but the primary failure category is non-retryable)
```

## 8. Repository SDK-import check

```
$ grep -rn "^\s*import requests\|^\s*from requests" src | grep -v platform
(no matches outside src/aico/platform - PASS)

$ grep -rln "^\s*import requests\|^\s*from requests" src
src/aico/platform/foundry_adapter.py
```

`requests` (the HTTP client used to reach the provider) is imported in
exactly one file in the repository, and it is inside the platform package -
matching the acceptance target "No model SDK import outside the platform
package."

## 9. Day 2 regression result

Live Hit@1/Hit@5/MRR numbers against the real Foundry endpoint aren't
reproducible in this checkout (no lead-provided access - see the
environment note above); what's captured here is everything provable
without it: every existing Day 1/Day 2 test still passes unchanged, plus
the dedicated Day 3 migration-is-behavior-preserving proof
(`tests/test_day2_regression.py` - see ADR-003's Day 2 regression evidence
section for what each half proves).

```
$ pytest -q tests/test_day2_regression.py tests/test_chunker.py tests/test_bm25.py \
    tests/test_ingest.py tests/test_day01_eval.py tests/test_embedding_provider.py \
    tests/test_vector_index.py tests/test_embed.py tests/test_hybrid.py tests/test_search.py -v

tests\test_day2_regression.py ......                                     [  7%]
tests\test_chunker.py ...........                                        [ 22%]
tests\test_bm25.py ......                                                [ 29%]
tests\test_ingest.py ....                                                [ 35%]
tests\test_day01_eval.py ..............                                  [ 53%]
tests\test_embedding_provider.py .......                                 [ 62%]
tests\test_vector_index.py ...........                                   [ 76%]
tests\test_embed.py ......                                               [ 84%]
tests\test_hybrid.py ....                                                [ 89%]
tests\test_search.py ........                                            [100%]

77 passed in 0.39s
```

Day 3 gateway/adapter suite, for completeness (includes every Day 3 test
from Tasks 1-6, plus the three added by the fallback-eligibility/retry-count
correction above):

```
$ pytest -q tests/test_model_gateway.py tests/test_model_gateway_retry.py \
    tests/test_model_gateway_routing.py tests/test_model_gateway_logging.py \
    tests/test_foundry_adapter_identity.py tests/test_foundry_adapter_normalization.py \
    tests/test_day2_regression.py tests/test_embed.py tests/test_embedding_provider.py \
    tests/test_chunker.py tests/test_bm25.py tests/test_ingest.py tests/test_day01_eval.py \
    tests/test_vector_index.py tests/test_hybrid.py tests/test_search.py
152 passed in 1.13s
```

Note: this checkout has since grown past Day 3 (Days 4-6 are also present),
so a bare `pytest -q` here now runs the whole repository's suite, not just
Day 3's - `493 passed` at time of writing. The `149`/`152` counts above are
the Day-3-scoped subset specifically, which is what this artifact is about.
