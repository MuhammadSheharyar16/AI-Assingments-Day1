# AICO — Retrieval Engineering (Day 1: Lexical Baseline · Day 2: Embeddings & Hybrid · Day 3: Model Gateway · Day 4: Structured Contracts · Day 5: Grounded Answering · Day 6: API Surface & Observability · Day 7: Evaluation & Regression Gate · Day 8: Session State & Memory · Day 9: Ontology Registry, Gate-A & Lane Selection · Day 10: Gate-B Permissions, Tenant Isolation & Safe Disclosure)

Day 1 is a from-scratch chunker and BM25 lexical search baseline. Day 2 adds
semantic retrieval on top of it: a real embedding provider behind one
interface, a persistent content-hash-keyed vector cache, cosine similarity
search, and a hybrid mode that fuses BM25 and vector rankings with
reciprocal-rank fusion — measured against the same corpus and queries so the
two days are directly comparable. No vector database, no retrieval
framework, no LLM. Day 6 exposes the Day 5 grounded RAG pipeline as a typed
FastAPI service — `POST /ask`, trusted identity, request protection,
cancellation, health endpoints, structured logs/metrics/OpenTelemetry
tracing — see "Day 6 — API surface and observability" below.

All data in `data/` is synthetic. No production, MOD, customer, personal or
classified data is used.

## Setup

Dependencies are managed with [`uv`](https://docs.astral.sh/uv/) — a single
lockfile (`uv.lock`) pins every package (direct and transitive) with hashes,
and `uv` creates/manages the `.venv` for you. Install `uv` once
([instructions](https://docs.astral.sh/uv/getting-started/installation/)),
then from the repo root:

```bash
uv sync
```

This creates `.venv/` (Python version pinned by `.python-version`) and
installs everything from `pyproject.toml` + `uv.lock` — dependencies plus the
`dev` group (`pytest`). No manual activation, `pip install`, or
`PYTHONPATH` juggling needed:

```bash
uv run pytest -q               # run the test suite
uv run python -m aico.retrieval.search ...   # run any aico.* module
```

`uv sync` builds and installs `aico` itself into the `.venv` in editable mode
(via the `hatchling` backend declared in `pyproject.toml`), so `import aico`
and `python -m aico...` just work under `uv run` — no `$env:PYTHONPATH` /
`set PYTHONPATH` distinction between PowerShell and cmd.exe to worry about
(that was only ever needed for the old `venv`+`pip` setup).

If you'd rather activate the environment directly instead of prefixing every
command with `uv run`:
```powershell
.venv\Scripts\Activate.ps1   # PowerShell
.venv\Scripts\activate.bat   # cmd.exe
source .venv/bin/activate    # macOS/Linux/git-bash
```

Adding a new dependency: `uv add <package>` (or `uv add --dev <package>` for
dev-only tools) — updates `pyproject.toml` and `uv.lock` and installs it in
one step, instead of hand-editing a flat `requirements.txt`.

**Real embedding/chat calls only** (Day 2's `AzureEmbeddingProvider`, now
Day 3's Model Gateway) need two things — neither is a secret in a file:

1. **`config/model-routing.yaml`** — copy it from
   `day03_pack/config/model-routing.example.yaml` if it doesn't already
   exist, then replace the lead-provided placeholders (chat/embedding
   deployment alias, region, data boundary) through your own approved
   configuration process. Never commit real values over the placeholders
   in a shared example — this file has no secrets in it either way, but
   the aliases/region are still lead-provided setup input, not something
   to hardcode to make the example run.
2. **Identity, not a key.** The gateway authenticates with
   `azure.identity.DefaultAzureCredential` (see
   `src/aico/platform/foundry_adapter.py`) — it tries, in order, a
   managed identity (when running in Azure), then environment-variable
   service-principal credentials (`AZURE_CLIENT_ID` /
   `AZURE_TENANT_ID` / `AZURE_CLIENT_SECRET`), then your own `az login`
   session, among others. Whichever applies to your environment, set it
   up through the normal Azure CLI/identity mechanism — never in a `.env`
   file or committed config. Two things are still environment-driven, via
   plain (non-secret) environment variables:

   ```
   AICO_FOUNDRY_ENDPOINT=<team-shared Foundry endpoint>   # named by config/model-routing.yaml's foundry.endpoint_env
   AICO_FOUNDRY_MANAGED_IDENTITY_CLIENT_ID=<client id>    # only if using a *user-assigned* managed identity
   ```

`bm25`-mode search and every test use `FakeEmbeddingProvider` (or a fake
transport/credential injected directly into the gateway/adapter) instead
and need neither of the above.

**Day 6's `POST /ask` API needs one more thing**, distinct from the above:
trusted-identity verification (`src/aico/api/identity.py`) needs an HS256
signing secret, named by (never hardcoded as) an environment variable:

```
AICO_AUTH_JWT_SECRET=<any local signing secret>   # lab-only stand-in for a real identity provider - see identity.py
```

Unset, every `/ask` call is rejected 401 (fails closed, not open — see
"Day 6" below). Day 6 tests never need this or a real Foundry
endpoint/credential — every test overrides the trusted-identity and
gateway/retriever dependencies with fakes (Task 10, dependency injection).

## Run the tests

Two tests deliberately use the *real* Day 1 index rather than a fake, to
prove that boundary is still load-bearing:
`tests/test_day05_grounding.py::test_day2_retrieval_path_evidence_comes_from_the_real_bm25_index`
(the "Day 2/3/4 boundary integration proofs" section) and
`tests/test_day06_health.py::test_real_retrieval_health_check_runs_without_raising`.
`data/index/index.json` is committed (unlike `data/vectors/`, still a
gitignored build output — see Day 2 below) specifically so a fresh
checkout can run the suite immediately, with no build step first:

```
uv sync
uv run pytest -q
```

If you regenerate it (e.g. after editing `data/documents/`), re-run the
same deterministic command that produced the committed file and commit
the result — chunk IDs are content-hash based (Day 1), so an unchanged
corpus reproduces a byte-identical `index.json`:

```
uv run python -m aico.retrieval.ingest --input data/documents --out data/index --tokens 300 --overlap 50
```

Deleting `data/index/` (or reverting to an older commit that predates it
being tracked) and skipping that command before `pytest -q` fails exactly
the two tests named above with `FileNotFoundError: No index.json in
data\index` — everything else in the suite uses fakes and does not need
it.

490 tests pass, across thirty-five files (416 from Days 1-5, unchanged, plus
74 new Day 6 tests across eight `test_day06_*.py` files — see "Day 6" below).
Every test is deterministic and
offline — none makes a real network call; Day 2's tests use
`FakeEmbeddingProvider` exclusively, Day 3's inject a fake
transport/credential directly into the gateway/adapter (and, for retry
tests, a no-delay `sleep` and a fixed `random_factor` so bounded-retry
tests run instantly and assert exact backoff/jitter values instead of
just "eventually retries"), and Day 4's inject a fake `ModelGateway`
transport for the one bounded repair call it ever makes.

- `tests/test_chunker.py` (11) — offset reconstruction, overlap, unicode
  survival, empty input, invalid configuration, determinism
- `tests/test_bm25.py` (6) — tokenisation, ranking behaviour, IDF
  weighting, tie-breaking
- `tests/test_ingest.py` (4) — end-to-end field completeness + offsets,
  tokens/overlap actually change output, invalid config rejected,
  determinism
- `tests/test_day01_eval.py` (14) — anchor matching, hit/MRR scoring,
  category breakdown, no-match handling, phrase-support gate, report
  rendering
- `tests/test_embedding_provider.py` (7) — fake provider determinism
  (same text → same vector, across instances), distinct text → distinct
  vectors, batch order preserved, dimensions/alias
- `tests/test_vector_index.py` (10) — cosine correctness (identical,
  orthogonal, opposite, hand-worked, zero-vector), dimension mismatch
  raises (direct and via cache search), cache hit/miss rules, save/load
  round-trip
- `tests/test_embed.py` (6) — cache hit (zero calls on an unchanged run),
  cache invalidation (editing one chunk re-embeds only that chunk, proven
  content-hash-keyed not chunk-id-keyed), model-alias change invalidates,
  provider dimension mismatch is a hard error
- `tests/test_hybrid.py` (4) — hand-worked RRF example with exact expected
  scores, a chunk retrieved by only one mode, determinism, the `k` tuning
  knob
- `tests/test_search.py` (9) — identical record shape across all three
  modes, modes don't interfere with each other, determinism parametrized
  across bm25/vector/hybrid, dimension-mismatch and missing-cache errors
- `tests/test_model_gateway.py` (16) — Day 3 typed gateway contract:
  the required SDK-isolation repository check, typed chat/embed round-trips
  against a fake transport, sanitized metadata (never prompt/completion
  text), missing token usage represented as `None`/`"unknown"` rather than
  invented, cancellation before a call starts, non-retryable transport
  failures normalized not leaked, `AzureEmbeddingProvider` satisfying
  `EmbeddingProvider` via the gateway, and config validation (missing
  file, placeholder alias, fully-filled file)
- `tests/test_model_gateway_retry.py` (16) — Day 3 bounded exponential
  retry with jitter: required error categories map to the right
  retryable/non-retryable typed error, a retryable failure retries and
  reports an accurate `retry_count` on success, a retryable failure that
  keeps failing stops at the configured attempt ceiling
  (`GatewayRetryCeilingExceededError`, never an infinite loop), a
  non-retryable failure fails immediately with zero sleep calls,
  cancellation set during the backoff wait stops the loop before the next
  attempt, and backoff grows exponentially, caps at `max_delay_ms`, and
  jitter scales the delay by the configured random factor
- `tests/test_model_gateway_routing.py` (12) — Day 3 routing policy and
  safe fallback: an allowed route proceeds through a fully-compatible
  fallback after the primary fails; policy-disallowed
  (`routing.fallback.enabled=false`), region-mismatch,
  data-boundary-mismatch, risk-incompatible and budget-incompatible
  fallbacks are all blocked (`GatewayFallbackBlockedError`, chaining the
  primary failure); an axis not marked required is never a reason to
  block; no fallback transport configured lets the primary error
  propagate unchanged; cancellation is never treated as a fallback
  trigger; a successful primary call never touches the fallback transport
  at all
- `tests/test_model_gateway_logging.py` (10) — Day 3 sanitized logging:
  behavioral `caplog` proof that a successful call, a retry, hitting the
  retry ceiling, a non-retryable failure, an unnormalized exception, and a
  blocked/attempted fallback each log a sanitized structured line
  (operation, model_alias, category, counts) while a distinctive
  prompt/completion marker planted in the request/response never appears
  in `caplog.text` in any of those cases — including the adversarial case
  where the raised exception's own message happens to contain it, proving
  the gateway never logs `str(exc)`; plus a static check that
  `foundry_adapter.py` (the only file that ever builds an Authorization
  header) contains no logging call at all
- `tests/test_foundry_adapter_identity.py` (7) — Day 3 identity
  authentication: bearer token from an injected `TokenCredential` (never
  an API key), token caching across calls, refresh once close to expiry,
  a credential/provider auth failure normalizing to
  `GatewayAuthenticationError`, no API-key handling in source, no
  secret-shaped value in `config/model-routing.yaml`
- `tests/test_foundry_adapter_normalization.py` (11) — Task 6's required
  HTTP-layer normalization coverage for `FoundryAdapter._post()` itself
  (`requests.post` monkeypatched, never a real call): a real
  `requests.Timeout` and a connection failure normalize to
  `GatewayTimeoutError`/`GatewayServerError`; HTTP 429/401/403/400/5xx
  normalize to the matching typed error; an unhandled status code (402)
  still comes back as a `ModelGatewayError`, never a raw `requests`
  exception; a 2xx response returns the parsed payload
- `tests/test_day2_regression.py` (6) — Task 6's Day 2 regression proof:
  routing the same text through `AzureEmbeddingProvider` -> `ModelGateway`
  -> a fake transport backed by `FakeEmbeddingProvider` produces
  bit-identical vectors (and preserves batch order) to calling
  `FakeEmbeddingProvider` directly, and `vector_search`/`hybrid_search`
  rankings are identical either way — proving the Day 3 migration is a
  transparent pass-through, not a change in retrieval behavior; plus two
  reminders that bm25 mode and RRF fusion never depended on the embedding
  provider at all
- `tests/test_day04_contracts.py` (58) — Day 4 Tasks 1/2: required/
  optional fields, enums, constrained values, extra-field rejection and
  explicit `schema_version` on both `CitedAnswer`/`ResponseEnvelope`;
  committed schema under `contracts/schema/` matches a fresh
  `model_json_schema()` regeneration (no drift); the raw-string parse ->
  contract/schema validator (`parse_json`/`validate_contract`/
  `parse_and_validate`) against hand-built payloads and every relevant
  case in `data/day04_pack/fixtures/structured_output_cases.json`
- `tests/test_day04_semantic_validation.py` (20) — Day 4 Task 3: each of
  the five `semantic_rules.md` rules (S1–S5) rejects exactly the case it
  names and accepts everything else, deterministic S1..S5 evaluation
  order, semantic validation never mutates its input, is distinguishable
  from a contract/schema failure by `ValidationFailure.stage`, and the
  supplied D04-09/D04-10 fixtures prove the schema-valid-but-
  semantically-invalid split end to end
- `tests/test_day04_repair.py` (20) — Day 4 Task 4: the three required
  repair cases (invalid → repaired valid → success; invalid → repaired
  invalid → typed failure; non-repairable path → typed failure with zero
  Model Gateway calls) against a fake `ModelGateway` transport, repair
  capped at exactly one call structurally, the repaired response
  revalidated through the complete pipeline (contract *and* semantic), a
  Model Gateway failure during repair coming back as a typed
  `stage="repair"` failure rather than a raised exception, the D04-11/
  D04-12 repair fixtures end to end, and the gateway-boundary proof that
  only `repair.py` in the contract layer ever imports
  `aico.platform.model_gateway`
- `tests/test_day04_broken_output_suite.py` (17) — Day 4 Task 5: every
  one of the 12 cases in `structured_output_cases.json` run end to end
  and asserted against its documented final outcome (not just its
  per-stage behaviour), the documented bounded markdown-fence unwrap
  proven never to extend to surrounding prose, and a coverage check that
  every fixture case is actually exercised somewhere in the suite
- `tests/test_day04_compatibility.py` (10) — Day 4 Task 6: the required
  case (`existing_caller_v1.json`, which never sends `warning`, still
  validates against the current `ResponseEnvelope`, in both directions),
  `schema_version` present in output metadata on both contracts, and the
  four breaking-change examples documented in
  `docs/adr/ADR-004-day4-contract-versioning.md` each proven to fail
  validation
- `tests/test_day06_api.py` (5) — Task 1: OpenAPI documents `POST /ask`
  with its request/response models, a successful call returns the typed
  `AskResponse`, and the public contract is proven to be a real mapping
  from `AnswerResult`, not the internal dataclass reflected through
- `tests/test_day06_identity.py` (18) — Task 2: every
  `identity_claim_cases.json` fixture driven through the trusted-identity
  decision function, plus route-level proof that a rejecting identity
  dependency stops the request before the RAG pipeline runs and that a
  caller-supplied body identity can never override trusted claims
- `tests/test_day06_correlation.py` (7) — Task 3: missing request/
  correlation IDs are generated, a caller-supplied ID is echoed not
  replaced, and the same correlation ID set by the middleware is
  reachable from deep inside the pipeline via a contextvar (proving
  propagation, not just generation)
- `tests/test_day06_errors.py` (8) — Task 4: unsupported Content-Type and
  an oversize payload are both rejected 4xx **before the fake gateway is
  ever called** (call-count asserted), and every failure source
  (content-type, size, identity, body validation, an unhandled exception)
  uses the identical safe `ErrorResponse` envelope
- `tests/test_day06_cancellation.py` (4) — Task 5: a fake slow gateway
  that polls its cancellation token proves cancellation reaches
  mid-flight work (never a real model call), plus an HTTP-level test
  driving the app over a raw ASGI `scope`/`receive`/`send` to prove a
  real client disconnect propagates all the way to that same token
- `tests/test_day06_health.py` (9) — Task 6: liveness never depends on
  dependency state (even when both checks raise), all three
  `dependency_health_cases.json` fixtures driven through all three
  endpoints, and the real default health checks run cleanly against this
  repo's own `data/index`/`config/model-routing.yaml`
- `tests/test_day06_observability.py` (18) — Tasks 7-9 + the Task 12
  correlation cross-check: structured JSON log lines carry required
  operational fields and never raw question/evidence/answer/auth-header
  content; gateway/retrieval metrics are recorded from already-sanitized
  `CallMetadata`; a successful `/ask` produces one OpenTelemetry trace
  linking every named stage under one root span; and one correlation ID
  is proven identical across the response body, every log line, and
  every span in the trace
- `tests/test_day06_dependency_injection.py` (5) — Task 10: each of
  answer-service/gateway/retriever/policy-evaluator/dependency-health is
  independently replaceable with a deterministic fake - the
  gateway/retriever/policy tests deliberately do NOT override the whole
  answer service, proving the seam is load-bearing in the real FastAPI
  dependency graph

## Day 1 — Chunking and lexical retrieval

### Task 1 — Ingest and chunk

```
python -m aico.retrieval.ingest --input data/documents --out data/index --tokens 300 --overlap 50
```

Reads every `.md` file in `--input`, splits each into overlapping,
offset-exact chunks, and writes a single `index.json` (manifest + chunk
records) to `--out`. Token size and overlap are required arguments — no
hardcoded default. All four flags (`--input`, `--out`, `--tokens`,
`--overlap`) are mandatory. **This is the chunk index Day 2 also reads from
— do not re-ingest with different `--tokens`/`--overlap` unless you intend
to invalidate the whole vector cache.**

### Task 2 — Lexical retrieval

```
python -m aico.retrieval.search --query "termination notice period" --top-k 5 --index data/index
```

`--top-k` defaults to 5, `--index` defaults to `data/index`, so the
minimal form is:

```
python -m aico.retrieval.search --query "termination notice period"
```

Ranks every chunk in the index against the query using a from-scratch
BM25 implementation (`src/aico/retrieval/bm25.py` — no ranking library).
Prints rank, score, chunk ID, source file, character span, and a text
snippet for each result. Prints "No matching chunks" instead if the top
score is 0. (This is the same command Day 2 extended with `--mode` — see
below; omitting `--mode` still runs plain BM25, unchanged.)

### Task 3 — Measure and report

```
python -m aico.evals.day01 --queries data/evals/day01_queries.json --documents data/documents
```

There is deliberately no `--index` flag: this eval needs several
differently-sized chunk sets to compare configurations, so it always
rebuilds chunks itself from `--documents` for every entry in `--configs`
rather than reading one fixed pre-built index.

Runs the ten labelled queries in `data/evals/day01_queries.json` against
**both** required chunk configurations in one pass (`--configs` defaults
to `200:40,400:80`, overridable, e.g. `--configs 200:40,400:80,150:30`).
For each config it rebuilds the chunk set from `--documents` (default
`data/documents`), checks whether the retrieved chunks contain each
query's anchor phrase (after normalisation), and reports Hit@1, Hit@5,
MRR and a per-category breakdown for the eight scored queries
(`exact_term`, `synonym_poor`, `multi_chunk`). The two `no_match` queries
(Q09, Q10) are reported separately and inverted: correct behaviour is a
top score *below* the documented floor (`NO_MATCH_SCORE_FLOOR = 4.0` in
`src/aico/evals/day01.py`), not folded into the Hit@1/Hit@5/MRR average.

Writes `artifacts/day01/chunks_<tokens>_<overlap>.json` (the chunk set
used per config), `artifacts/day01/metrics.json` (full metrics for every
config) and `artifacts/day01/retrieval_report.md`. The report is fully
generated from the same evaluation pass as the metrics — the config and
metrics tables, the no_match verdicts, the winning-config pick and the
worst-scored-query diagnosis are all built in `render_report()` in
`src/aico/evals/day01.py`, not hand-transcribed — so re-running this one
command after any code, corpus or query-set change regenerates a report
that can't drift out of sync with the numbers behind it.

`correctly_abstained` is decided by `NO_MATCH_SCORE_FLOOR = 4.0` in
`src/aico/evals/day01.py` **alone** — a top score below the floor abstains,
at or above it does not, with no override, so the verdict is auditable
against that one constant. The scorer also checks whether the top chunk
contains **every one** of the query's adjacent content-word pairs verbatim
(a phrase-adjacency check — see `artifacts/day01/retrieval_report.md` for
why "every", not "any"), but that check is diagnostic only: it explains
*why* an above-floor score happened (coincidental single-word overlap vs a
genuinely topical near-miss) and is reported alongside every verdict — it
never changes the verdict itself.

#### Last verified run (2026-08-31)
```
config 200_40 (35 chunks): Hit@1=0.62  Hit@5=0.88  MRR=0.729
  exact_term:    Hit@1=0.75 Hit@5=1.00 MRR=0.875
  synonym_poor:  Hit@1=0.50 Hit@5=0.50 MRR=0.500
  multi_chunk:   Hit@1=0.50 Hit@5=1.00 MRR=0.667
  Q09 (no_match): top_score=10.801 phrase_support=False -> FALSE POSITIVE
  Q10 (no_match): top_score=7.558  phrase_support=False -> FALSE POSITIVE

config 400_80 (16 chunks): Hit@1=0.50  Hit@5=1.00  MRR=0.692
  exact_term:    Hit@1=0.75 Hit@5=1.00 MRR=0.875
  synonym_poor:  Hit@1=0.50 Hit@5=1.00 MRR=0.600
  multi_chunk:   Hit@1=0.00 Hit@5=1.00 MRR=0.417
  Q09 (no_match): top_score=12.159 phrase_support=False -> FALSE POSITIVE
  Q10 (no_match): top_score=5.928  phrase_support=False -> FALSE POSITIVE

pytest -q: 50 passed (day01 eval: 14 passed)
```

All four no_match cases score above the floor in both configs, and in
every case the missing-phrase diagnosis shows why: BM25 latches onto a
coincidentally shared, high-tf word (e.g. "rate" as in a day rate, not an
interest rate) rather than genuine topical relevance. This is now reported
honestly as four false positives rather than folded into "correct" by a
hidden override — see the BM25 failure diagnosis in
`artifacts/day01/retrieval_report.md` for the per-query detail.

### Evaluation queries

The 10 labelled queries in `data/evals/day01_queries.json`, in order. Each
is checked against a normalised chunk text (lowercase, punctuation
stripped, whitespace collapsed) for the listed anchor phrase(s); see
`matching_rule` in the JSON file for the exact rule.

1. **Q01** (`exact_term`) — *"What is the minimum public liability
   insurance a supplier must hold?"*
   Anchor: `"public liability cover of at least five million pounds"` (DOC-004)

2. **Q02** (`exact_term`) — *"What are the standard payment terms for a
   valid invoice?"*
   Anchor: `"net thirty days from the date of a valid invoice"` (DOC-003)

3. **Q03** (`exact_term`) — *"What weighting is given to price in
   supplier evaluation?"*
   Anchor: `"price and total cost carries a weighting of thirty five
   percent"` (DOC-001)

4. **Q04** (`exact_term`) — *"How long is the inspection window for
   delivered goods?"*
   Anchor: `"inspection window of ten working days"` (DOC-005)

5. **Q05** (`synonym_poor`) — *"Can a vendor hand the agreement over to
   another company?"*
   Anchor: `"may not assign or novate the agreement"` (DOC-002)

6. **Q06** (`synonym_poor`) — *"Is it acceptable to split an order into
   several separate shipments?"*
   Anchor: `"partial delivery will only be accepted"` (DOC-005)

7. **Q07** (`multi_chunk`) — *"How much notice is required before a
   supplier is removed, and does the contract override it?"*
   Anchors: `"ninety days written notice of withdrawal"` (DOC-001) **and**
   `"supersedes any notice period stated in the sourcing policy"` (DOC-002)

8. **Q08** (`multi_chunk`) — *"What must be in place before a supplier's
   first invoice can be paid?"*
   Anchors: `"bank verification must be completed before the first
   payment"` (DOC-004) **and** `"a valid purchase order number must
   appear on every invoice"` (DOC-003)

9. **Q09** (`no_match`) — *"What interest rate is charged on a late
   payment?"* — no anchor; correct behaviour is a top BM25 score below
   the floor (currently a FALSE POSITIVE — see Known gaps below).

10. **Q10** (`no_match`) — *"What penalty fee applies to a late
    delivery?"* — no anchor; correct behaviour is a top BM25 score below
    the floor (currently a FALSE POSITIVE — see Known gaps below).

Categories: `exact_term` (Q01–Q04) tests literal phrase overlap between
query and document, where BM25 is expected to do well; `synonym_poor`
(Q05–Q06) tests queries phrased with different words than the source
text, where pure lexical matching is expected to struggle; `multi_chunk`
(Q07–Q08) requires two separate chunks (often from two different
documents) to both surface in the top 5; `no_match` (Q09–Q10) has no
correct answer in the corpus at all, testing that the system doesn't
confidently return an irrelevant chunk.

## Day 2 — Embeddings and hybrid retrieval

Builds on Day 1 without touching it: same corpus, same chunker, same
`data/index`. Adds a real embedding provider, a persistent vector cache,
cosine-similarity search, and reciprocal-rank fusion — then measures all
three modes (bm25, vector, hybrid) against one query set so the report can
say plainly where semantic retrieval helped and where it didn't.

### Task 1 — Embed and cache

```
python -m aico.retrieval.embed --index data/index --out data/vectors
```

Reads the chunk records Day 1's `ingest` produced, embeds every chunk
through `AzureEmbeddingProvider` (`src/aico/retrieval/embedding_provider.py`),
which delegates to the Day 3 Model Gateway (`src/aico/platform/model_gateway.py`)
— the only file in the repo that imports the HTTP client used to call the
embedding API is `src/aico/platform/foundry_adapter.py`, reached through
that gateway. See "Day 3 — Microsoft Foundry Model Gateway" below. Writes/
updates the cache at `--out`. Reports how many chunks were embedded, how
many were served from cache, and how many provider calls were made:

```
Embedded 23 chunk(s), served 0 from cache, made 2 provider call(s) -> data\vectors   # first (cold) run
Embedded 0 chunk(s), served 23 from cache, made 0 provider call(s) -> data\vectors   # second (warm) run
Embedded 1 chunk(s), served 22 from cache, made 1 provider call(s) -> data\vectors   # after editing one chunk
```

Each cache entry (`data/vectors/vectors.json`, gitignored — a build output
regenerated by this command) stores the vector plus `chunk_id`,
`content_hash`, `model_alias`, `dimensions`,
`dataset_version` and `created_at`. **`content_hash`, not `chunk_id`, is
the invalidation key** — `chunk_id` is stable by design, so a cache keyed
on it alone would survive a text edit underneath it and every later search
would rank against a vector for text that no longer exists. A different
`model_alias` invalidates an entry too, even when `content_hash` still
matches — a vector from one model is never valid for another.

`FakeEmbeddingProvider` (same file) is a deterministic, offline stand-in
used by every test — a vector is derived from a SHA-256 hash of the input
text, so the same text always produces the same vector and no test ever
makes a network call.

*Known live-endpoint quirk:* the shared dev Foundry endpoint returns a
transient `404 DeploymentNotFound` on roughly 1 in 5–10 otherwise-valid
calls (confirmed by re-sending identical requests). This is server-side
flakiness, not a bug here — Day 2's rules explicitly defer
timeouts/retries/routing policy to Day 3, so `embed`/`search` don't retry;
if a command fails with `DeploymentNotFound`, just run it again. (The
`day02` eval command below *does* wrap calls in a small retry local to
that script only — seeded ~19 calls per run otherwise fails more often
than not by chance alone — see its section for why that's scoped
differently.)

### Task 2 — Three retrieval modes

```
python -m aico.retrieval.search --query "..." --mode bm25   --top-k 5
python -m aico.retrieval.search --query "..." --mode vector --top-k 5
python -m aico.retrieval.search --query "..." --mode hybrid --top-k 5
```

`--mode` defaults to `bm25`, so every Day 1 `search` invocation still works
unchanged. `--index` defaults to `data/index`, `--vectors` to
`data/vectors`, `--top-k` to 5. All three modes print the identical record
shape: rank, score, chunk ID, source file, character span, matched text.

- **`vector`** embeds the query through the same provider/model alias used
  for the chunks, then ranks every cached vector by cosine similarity
  (`src/aico/retrieval/vector_index.py`). A dimension mismatch between the
  query vector and a cached vector raises — never padded or truncated.
- **`hybrid`** fuses the bm25 and vector rankings with reciprocal-rank
  fusion only (`src/aico/retrieval/hybrid.py`) —
  `score(chunk) = Σ over modes of 1/(k + rank_in_that_mode)`, `k = 60`
  (`RRF_K`). Fusion operates on **ranks**, never raw scores: a BM25 score
  is unbounded and corpus-dependent, a cosine score sits in `[-1, 1]`, and
  averaging the two just lets whichever number is numerically larger win.
- Ranking is stable across runs; ties break on `chunk_id` ascending in
  every mode.
- `vector`/`hybrid` require `data/vectors` to already exist (run Task 1
  first) and, for the real provider, a working `.env`.

### Task 3 — Measure all three modes

```
python -m aico.evals.day02 --queries data/evals/day02_queries.json --mode all
```

Runs all sixteen queries (`data/evals/day02_queries.json` — Q01–Q10 carried
over unchanged from Day 1, Q11–Q15 new `semantic_only` queries, Q16 a new
`no_match` query) against all three modes in one pass. Bm25 and vector are
each computed once per query over the full ranking; hybrid is derived from
those two via RRF rather than independently re-embedding every query again
— halves the live embedding calls the command needs (~16 instead of ~32).
A small retry wrapper local to this script (not a change to
`embedding_provider.py`, not a Day 3 gateway) absorbs the live endpoint's
known transient failures — a full run makes ~19 calls, so a bare
1-in-5-to-10 failure rate would make a clean run unlikely by chance alone.

Reports Hit@1, Hit@5 and MRR per mode overall and per category. The three
`no_match` queries (Q09, Q10, Q16) are scored inverted and reported
separately, each mode against **its own** documented score floor
(`BM25_SCORE_FLOOR = 7.0`, `VECTOR_SCORE_FLOOR = 0.30`,
`HYBRID_SCORE_FLOOR = 0.0305` in `src/aico/evals/day02.py`) — a shared
floor across modes would be meaningless, since the three score scales
don't mean the same thing.

Also demonstrates the cache live (cold run → warm run → one-chunk-edit
run) against a throwaway in-memory cache, so the report's cache-evidence
section is real evidence from that run, not a hand-transcribed number —
the persisted `data/vectors` cache used for the mode evaluation itself is
never touched by this demonstration.

Writes `artifacts/day02/metrics.json` (full per-mode, per-query metrics)
and `artifacts/day02/mode_comparison.md`, both from the same evaluation
pass — the mode_comparison.md tables, the vector-beat-bm25 and
bm25-beat-vector examples, the hybrid-vs-both-modes verdict and the
no_match floor table are generated from the metrics, not hand-transcribed,
so neither file can drift from the other.

A single-mode form is also available for a quick check without a full
report: `--mode bm25|vector|hybrid` writes `metrics.json` for that mode
alone.

#### Last verified run (2026-08-27)
```
mode       Hit@1   Hit@5     MRR  no_match(abstained/3)
bm25       0.462   0.692   0.545  2/3
vector     0.769   0.923   0.833  0/3
hybrid     0.615   0.846   0.692  0/3

pytest -q: 71 passed
```

Full per-category breakdown, the cache-evidence table, the vector-beat-bm25
and bm25-vs-vector examples (both investigated, not asserted — see
`mode_comparison.md`), the hybrid-loses-on-`semantic_only` finding, and the
full no_match reasoning per mode are in
`artifacts/day02/mode_comparison.md`.

*Note on reproducing this exactly:* `FakeEmbeddingProvider` is bit-for-bit
deterministic; the live Azure endpoint is not — identical requests can
return vectors that differ in the 5th–6th decimal place between calls.
Hit@1/Hit@5/MRR won't change from that (the differences are far too small
to flip a ranking), but a byte-diff of `metrics.json`'s vector/hybrid
scores against a previous run may show tiny drift. This is normal.

## Day 4 — Structured AI contracts

Adds a typed contract boundary (`src/aico/contracts/`) in front of model
output: parse -> contract/schema validation -> semantic validation, with
one bounded repair attempt on failure. Nothing outside this package ever
deserializes model JSON directly, and it never calls a provider SDK or
`foundry_adapter` itself — the Day 3 `ModelGateway` stays the only
model-call boundary (`repair.py` is the sole file in the package that
imports it, for the one repair call it's allowed to make).

Requires no live endpoint or credentials at all — every Day 4 command and
test uses a fake `ModelGateway` transport.

### Regenerate the committed JSON Schema

```
python scripts/day04_generate_schemas.py
```

Writes `contracts/schema/cited_answer.v1.schema.json` and
`contracts/schema/response_envelope.v1.schema.json` straight from
`model_json_schema()` on the current `src/aico/contracts/models.py`
models — never hand-edited. Re-run this after any change to `models.py`
and commit the result; `tests/test_day04_contracts.py` fails if the
committed files drift from what the source models would regenerate.

### Regenerate the validation report

```
python scripts/day04_generate_validation_report.py
```

Runs the real contract-layer pipeline (`aico.contracts.repair.
validate_full`/`resolve`) against every case in
`data/day04_pack/fixtures/structured_output_cases.json` and the
compatibility check against `existing_caller_v1.json`, using a fake
`ModelGateway` transport for the two repair fixtures — no real network
call. Writes `artifacts/day04/validation_report.md`: contract/schema
version, generated schema paths, a full fixture-by-fixture outcome table,
the valid/contract-failure/semantic-failure/repair breakdowns, the
compatibility result, and one schema-valid-but-semantically-invalid
example described from sanitized structural facts (never raw fixture
text).

### Supplied resource pack and fixtures

`data/day04_pack/` (`contract_requirements.md`, `semantic_rules.md`,
`fixtures/structured_output_cases.json`,
`fixtures/existing_caller_v1.json`) is the fixed, unedited input every
Day 4 test and script runs against — see `data/day04_pack/README.md`.
`docs/adr/ADR-004-day4-contract-versioning.md` documents the
backward-compatibility rule and four breaking-change examples, each
proven by an executable test in `tests/test_day04_compatibility.py`.

## Day 6 — API surface and observability

Exposes the Day 5 grounded RAG pipeline (`GroundedAnswerService`,
unmodified in its logic) as a typed FastAPI service
(`src/aico/api/app.py`) that another application can call safely and
operate under production-like conditions — trusted identity, request
protection, cancellation propagation, health endpoints, structured
telemetry, dependency injection.

### Run the API

```
uv run uvicorn aico.api.app:app --reload
```

Needs `AICO_AUTH_JWT_SECRET` (Setup, above) to accept any request at all.
`GET /docs` serves the interactive OpenAPI UI once running. A real
`/ask` call that reaches the Model Gateway also needs `config/model-routing.yaml`
+ Azure identity, exactly as Day 3 — see Setup.

### Endpoints

- **`POST /ask`** — the grounded-answer endpoint. Request/response
  contracts (`src/aico/api/contracts.py`) are deliberately separate from
  `answer_service.py`'s internal `AnswerResult` union — `AskResponse`'s
  `status`/`category`/`message` are a mapped, stable public shape, never
  the internal dataclass reflected through.
- **`GET /health/live`** — always `alive`; calls no dependency check, so
  a Model Gateway/retrieval outage can never fail it.
- **`GET /health/ready`** — **documented degraded-mode policy**: ready
  (200) only when *every* monitored dependency (retrieval index, Model
  Gateway configuration) is healthy; otherwise `not_ready` (503), so this
  instance is removed from traffic until dependencies recover. The
  policy string itself is echoed in the response body
  (`src/aico/api/health.py`'s `READINESS_POLICY`) and enforced/tested in
  `tests/test_day06_health.py`.
- **`GET /health/dependencies`** — separate, always-200 detail per
  dependency (`retrieval`, `model_gateway`) — status + a safe, pre-written
  reason, never a credential, endpoint URL, or raw exception.

### Trusted identity, request protection, cancellation

- **Identity** (`src/aico/api/identity.py`): tenant/user context comes
  only from a verified `Authorization: Bearer` JWT (HS256, the
  `AICO_AUTH_JWT_SECRET` env var — a lab stand-in; the trust boundary,
  not this specific mechanism, is the Day 6 requirement). A body-supplied
  `tenant_id`/`user_id` can never override it — `AskRequest` forbids
  unknown fields outright, so the attempt itself is a 422, not something
  reconciled at runtime.
- **Content-Type / size** (`src/aico/api/request_protection.py`): a pure
  ASGI middleware rejects an unsupported Content-Type or a body over the
  documented 32 KiB ceiling (`MAX_REQUEST_BODY_BYTES`) before routing or
  `GroundedAnswerService` ever run. The size ceiling is enforced twice: a
  fast `Content-Length`-based rejection, then a streamed-byte-count guard
  that counts bytes actually delivered off the wire regardless of what
  (or whether) `Content-Length` claimed — so a request that omits the
  header, understates it, or arrives via chunked transfer-encoding still
  cannot get an oversize body past this middleware.
- **Errors** (`src/aico/api/errors.py`): one shared `ErrorResponse`
  envelope (`error_code`/`message`/`request_id`/`correlation_id`) for
  every 4xx/5xx source — identity rejection, content-type/size,
  `AskRequest` validation, an unexpected exception — never a stack trace
  or raw provider exception.
- **Cancellation** (`src/aico/api/request_cancellation.py`): a client
  disconnect is watched for while the (synchronous) pipeline call runs in
  the thread pool, and sets a `CancellationToken` threaded all the way to
  the Model Gateway call — proven with a fake slow gateway, never a real
  model call.

### Observability (`src/aico/observability/`)

- **Structured logs** (`logging.py`): one JSON line per event
  (`request_id`, `correlation_id`, `stage`, `outcome`, `latency_ms`,
  `error_category` where applicable). Never the raw question, retrieved
  evidence, model completion, or an `Authorization` value.
- **Metrics** (`metrics.py`): OpenTelemetry Metrics API, in-memory
  reader. Request/retrieval/gateway latency, token usage, retry count,
  request outcome; a retrieval cache hit/miss metric exists and is
  directly tested, though not wired into `BM25Retriever` (Day 5's
  default), which has no cache concept.
- **Tracing** (`telemetry.py`): OpenTelemetry spans, in-memory exporter.
  One successful `/ask` produces one trace linking `api.ask` (the HTTP
  layer) to `policy` / `retrieval` / `model_gateway` / `validation` /
  `response_composition` (`answer_service.py`) under one `trace_id` — the
  correlation context the assignment requires. A stage that never runs
  (e.g. a policy block) produces no span for it.
- **Sanitized trace artifact**: `artifacts/day06/trace_summary.md`,
  regenerated by `scripts/day06_generate_trace_artifact.py`, which
  asserts (not just eyeballs) that a deliberately distinctive fake
  question/evidence/answer/auth-header never appear in the rendered
  document before writing it.

### Dependency injection

`src/aico/api/dependencies.py` provides five independently-overridable
seams — `get_answer_service`, `get_gateway`, `get_retriever`,
`get_policy_evaluator`, and the two dependency-health checks.
`get_gateway`/`get_retriever`/`get_policy_evaluator` are wired as
`Depends(...)` parameters of `get_answer_service` itself, so a test can
override *one* of them (e.g. force a policy block) and still exercise the
real assembly logic for everything else — see
`tests/test_day06_dependency_injection.py`.

### Supplied resource pack and fixtures

`data/day06_pack/` (`README.md`, `uv_workflow.md`,
`api_contract_guidance.md`, `telemetry_requirements.md`,
`trace_summary_template.md`) is the fixed, unedited input Day 6 was built
against. Its synthetic validation fixtures (`api_cases.json`,
`identity_claim_cases.json`, `dependency_health_cases.json`) live under
`tests/fixtures/day06/` — the same "docs stay in the pack, fixtures move
to `tests/fixtures/`" convention Day 5's `attack_fixtures.json` already
established.

### A real gap closed, not assumed

The first version of `RequestProtectionMiddleware` enforced the 32 KiB
ceiling against the *declared* `Content-Length` header alone, with a
docstring acknowledging the obvious consequence as an accepted lab-scope
limitation: a client that omits `Content-Length`, understates it (e.g.
declares `1` byte while sending far more), or uses chunked
transfer-encoding (which carries no `Content-Length` at all) was not
caught by that check. Verified directly, not just reasoned about: a raw
ASGI client sending each of those three shapes with a genuinely oversize
body reached `AskRequest` parsing with the full body every time — the
size ceiling simply had nothing to check for any of them, an accepted gap
rather than a proven-safe one.

The fix is `_read_and_replay_within_limit` (`request_protection.py`): the
middleware itself now reads the body off the real ASGI `receive` channel,
counting bytes as they actually arrive, and stops the instant the running
total crosses the ceiling — never buffering more than one message past
the limit. A body that stays within it is replayed to the rest of the app
exactly as received (`_ReplayReceive`), so normal request handling is
unaffected.

The first attempt at this fix instead wrapped `receive` and raised an
exception on overflow from inside it, reusing the existing `ApiError`
machinery (`errors.py`) that already handles `IdentityError` the same
way. That seemed like the natural fit — until verified end to end: FastAPI's
own request-body parsing wraps `receive()`/`request.json()` in a bare
`except Exception` and converts *any* failure there into its own generic
`HTTPException(400, "There was an error parsing the body")`, silently
swallowing the specific `payload_too_large` 413 this middleware meant to
produce. Enforcing the ceiling entirely within the middleware — before
`self._app` is ever invoked, the same shape the existing
`Content-Length`-header check already uses — sidesteps depending on how a
downstream framework happens to handle a mid-read exception.

Verified after the fix: the same raw ASGI probe (missing, understated,
and chunked `Content-Length`, each with a genuinely oversize body) now
returns `413 payload_too_large` in every case, with `GroundedAnswerService`
never invoked — covered by `tests/test_day06_errors.py`'s
`API-006`/`API-007`/`API-008` cases (`tests/fixtures/day06/api_cases.json`),
alongside two direct unit tests of `_read_and_replay_within_limit` itself
(a within-limit multi-chunk body replayed untouched; an over-limit stream
stopped within a couple of messages of crossing the ceiling, never read
to exhaustion).

## Day 7 — Evaluation harness and regression gate

Turns the Day 1–6 RAG service into a measured engineering baseline: a
developer-created golden dataset (`evals/golden_v1.json`, 32 cases across
six required categories, split train/development/holdout), deterministic
evaluation (`aico.evals.metrics` — Hit@K, MRR, citation validity, refusal
accuracy, attack outcome), model-based groundedness evaluation
(`aico.evals.groundedness`, reported separately, never merged into one
score), repeated-run stability spot-checks, failure classification into a
fixed six-value taxonomy, explicit thresholds with a zero-tolerance safety
gate, a reviewed baseline with a separate deliberate update workflow, and
one complete regression-gate command:

```
uv run python -m aico.retrieval.ingest --input data/documents --out data/index --tokens 300 --overlap 50
uv run python -m aico.evals.day07
```

Exits `0` on pass, non-zero on fail, and writes
`artifacts/day07/evaluation_report.json`/`.md` and
`artifacts/day07/failure_classification.md`. Full design rationale, every
threshold/baseline number's justification, and the controlled-regression
proof (a deliberately weakened `--top-k` failing the gate, then a restored
configuration passing again) are documented in `evals/README.md` — the
Day 7 companion to this file, in the same spirit as `data/day05_pack/README.md`
and `data/day06_pack/README.md` for their own days.

### Docker

`Dockerfile` is a two-stage build: a `builder` stage installs the exact,
locked dependency set with `uv sync --frozen` (never re-resolving against
`pyproject.toml`, never touching the network beyond what `uv.lock` already
pinned) and installs the `aico` package itself from `src/`; a `runtime`
stage starts from a fresh `python:3.13-slim` and copies over only the
built `.venv` and `src/` — no `uv` binary, no dev dependencies (`pytest`/
`httpx`), no `.git`, no local `.venv` (that directory never enters the
build context at all — see `.dockerignore`), and no credentials or
environment-specific config (`config/model-routing.yaml`, `.env`,
`data/`, `evals/` are never copied in; they're supplied at `docker run`
time instead, exactly like the Model Gateway's own
`DefaultAzureCredential`-based identity — see "Setup" above).

`docker-entrypoint.sh` starts every container as root, `chown`s
`/app/data`/`/app/artifacts` when a bind-mounted host directory is
actually present at either path, then always execs the real command via
`gosu appuser` before it ever runs — so the application process itself
(the API server, or the eval harness) is fully unprivileged in every case,
the same guarantee a static `USER appuser` would give for a container with
no mounts, but without failing closed the moment a mounted host directory
happens to be owned by a uid the container doesn't recognize (a real,
verified failure mode of the plain `USER appuser` version of this
Dockerfile — see "A real fix, not a workaround" below). `docker exec`ing
into a running container still defaults to root — the same trade-off
official images like `postgres` make with this exact pattern — but that
never affects what the container's own actual process runs as.

Build:

```powershell
docker build -t aico:day7 .
```

Run the API service (the default `CMD`) — needs `AICO_AUTH_JWT_SECRET` to
accept any `/ask` request, and a mounted `config/model-routing.yaml` plus
a real Azure identity to actually reach a model (unset, `/health/live`
still responds and `/ask` still fails closed with 401, exactly as
un-containerized — see "Setup"). No `--user` flag needed — the entrypoint
already drops to `appuser` before `uvicorn` ever starts:

```powershell
docker run --rm -p 8000:8000 `
  -e AICO_AUTH_JWT_SECRET="a-local-signing-secret" `
  -v ${PWD}/config:/app/config:ro `
  aico:day7
```

Run the Day 7 regression gate inside the same image instead (mount the
golden dataset/thresholds/baseline, the document corpus, and an
artifacts/index directory the container can write to; build the index
once inside the container first since `data/index/` is a build output,
not something baked into the image). Also no `--user` flag needed — the
entrypoint fixes ownership of the mounted `data`/`artifacts` directories
before dropping to `appuser`:

```powershell
docker run --rm `
  -v ${PWD}/evals:/app/evals `
  -v ${PWD}/data:/app/data `
  -v ${PWD}/artifacts:/app/artifacts `
  aico:day7 sh -c "
    python -m aico.retrieval.ingest --input data/documents --out data/index --tokens 300 --overlap 50 &&
    python -m aico.evals.day07
  "
```

Verified end to end (`docker build` + both `docker run` forms above,
Docker Desktop): the image builds clean, `/health/live` responds
`{"status":"alive"}`, `/ask` fails closed 401 without
`AICO_AUTH_JWT_SECRET` exactly as un-containerized, `docker top` on the
running API container shows the actual `uvicorn` process at uid 1000 (not
root) with neither `docker run` command ever passing `--user`,
`pytest`/`httpx` (the dev dependency group) and the `uv` binary are both
confirmed absent from the runtime image, and the mounted-volume gate run
writes real files back onto the host and reproduces the exact host
result — `GATE: PASS`, exit `0`.

#### A real fix, not a workaround

The first version of this Dockerfile used a static `USER appuser` with no
entrypoint script. Verified directly against the exact `docker run`
command above: it failed closed with `PermissionError`, then
`FileNotFoundError`, the moment `/app/data`/`/app/artifacts` were bound to
host directories `appuser` (uid 1000) didn't own — a real, reproducible
bug, not a hypothetical one. The tempting quick fix was documenting
`--user root` on that one command; the actual fix is
`docker-entrypoint.sh` above, verified to restore the mounted-volume
workflow to working *and* keep every real application process
unprivileged — no `docker run` in this README needs `--user` for anything,
including the one that regressed first.

The container exits with the gate's own exit code either way — `0` pass,
non-zero fail — so this doubles as a containerized quality gate a CI
runner could invoke directly instead of (or alongside) `uv run` on the
runner itself.

### CI — `.github/workflows/day07-quality-gate.yml`

GitHub Actions (this repository's real platform — `git remote -v` points
at `github.com`; no second, fake CI config was added just to match the
brief's example tree). One job, the exact required pipeline in order:

```
uv sync --frozen
→ uv run ruff check .
→ uv run pytest -q
→ uv run python -m aico.evals.day07
```

(`data/index/` is rebuilt between the lint step and `pytest -q` — a
necessary precondition, not one of the four required commands: two
existing test files and the regression gate itself all need the real
index. `data/index/index.json` is committed (see "Run the tests" above),
but CI rebuilds it from `data/documents/` fresh here rather than trusting
the committed file — the same deterministic command a contributor would
run locally after editing the corpus, so CI never silently passes against
a `data/documents/`/`index.json` pair that drifted apart in a commit that
forgot to regenerate one of them.)

**None of the four required steps carries `continue-on-error` or an
`allow_failure` equivalent** — a failed `ruff check`, a failed test, or a
failed regression gate all fail the job and the workflow run, using
GitHub Actions' own ordinary default behavior (nothing here needed to
override it to get that guarantee). **No step ever passes
`--update-baseline`** — updating the reviewed baseline is a separate,
deliberate, human-run command (`scripts/day07_update_baseline.py` /
`python -m aico.evals.day07 --update-baseline`, both requiring
`--reviewer`/`--notes` and defaulting to a dry run), never something CI
performs on its own.

Verified as close to a real CI run as this environment allows: `.venv`,
`data/index/`, and `data/vectors/` were all deleted and every one of the
four required commands (plus the index-build precondition) was re-run in
the exact documented order against that clean state — `uv sync --frozen`
resolves and installs from the lockfile alone, `ruff check .` passes with
zero findings, all 716 tests pass, and the regression gate passes with
exit `0`, matching the committed `artifacts/day07/` reports exactly.

#### Adding `ruff` surfaced a real, first-time linting decision

No linter had run over this repository before Task 13 — six days of
already-accepted code had never been checked against one. Running
`ruff check .` with no configuration at all found 138 issues on the first
pass, entirely because ruff's own default rule selection had never been
reviewed or agreed to, not because this codebase was suddenly worse than
it was yesterday. `pyproject.toml`'s `[tool.ruff]` makes that rule
selection explicit and reviewed instead of implicit (`select = ["E", "F",
"I", "B", "UP"]`, with four narrow, individually-justified ignores — a
consistent long-form-prose comment style predates this rule, FastAPI's
own `Depends(...)` pattern, and two Python-version modernizations this
task deliberately didn't retrofit onto already-accepted `Enum`/generic
code). `ruff check .` now passes with zero findings and zero `# noqa`
suppressions anywhere in the repository. Full rationale — what was
autofixed, what was fixed by hand and why, and **a real regression the
autofix itself caused** (it silently deleted a re-exported import a test
still depended on, caught immediately by re-running the full suite) — is
in `docs/adr/ADR-005-ruff-adoption.md`.

## Day 8 — Session state and memory

Adds bounded, isolated session memory on top of the Day 1–7 grounded RAG
service, so a follow-up question can use prior conversational context
without that memory ever becoming evidence — the assignment's own rule:
"Memory helps interpret the conversation. Retrieved evidence still
determines what is true." `src/aico/memory/` is the whole boundary:
`models.py` (typed `SessionState`/`SessionTurn`/`MemorySummary`, no field
anywhere for a credential or a "trusted" flag), `store.py` (the
`SessionStore` abstraction — `SqliteSessionStore` the real local
implementation, `InMemorySessionStore` the deterministic test fake, one
shared contract proven by running every lifecycle test against both),
`service.py` (`MemorySessionService`, the identity-bound seam
application code resolves/mutates sessions through — no method accepts a
raw `tenant_id`/`user_id`, only a Day 6 `TrustedIdentity` — plus
`update_session`'s bounded lost-update retry), `context_builder.py`
(`build_memory_context` — the token/turn-budget-bounded selection over a
session's summary/recent turns), and `summarizer.py` (`Summarizer` +
`FakeSummarizer`/`ModelGatewaySummarizer` + `compact_session`).

`POST /ask` now accepts an optional `session_id` (creating a new session
when omitted) and always returns the active one. The resolved session's
bounded memory context is threaded into Day 5's still-unmodified pipeline
as one extra argument, rendered as its own separately-labelled `SESSION
MEMORY` prompt message (`rag/prompt_builder.py`) — distinct from
`RETRIEVED EVIDENCE`, never merged into it, and never something
`rag/citation_validator.py` is even aware exists: a model citing a
memory turn id is rejected as forged exactly like any other invented
chunk_id. Session ownership is always `tenant_id` + `user_id` (from the
trusted identity) + `session_id`; a wrong-owner request and a nonexistent
session id are indistinguishable by construction — every store lookup is
scoped by all three in one query, and `SessionNotFoundError`'s outward
message never varies by cause.

```
uv run pytest -q
uv run python -m aico.evals.day07
uv run python scripts/day08_generate_memory_artifacts.py
```

981 tests pass overall (up from 716 after Day 7) — 265 new for Day 8,
across `tests/test_day08_*.py` (session contract, store lifecycle,
isolation, context budget, compaction, concurrency, memory-vs-evidence,
memory safety, follow-up) plus targeted session/memory additions to the
existing Day 6 observability test file. The Day 7 regression gate is
unmodified and still runs, unchanged, as the last required step —
`evals/golden_v1.json`/`thresholds_v1.json`/`baseline_v1.json` and the
Day 5 holdout dataset were never touched by any Day 8 change (verified
directly: `git diff --stat` across every Day 8 commit against those
paths is empty).

`scripts/day08_generate_memory_artifacts.py` regenerates
`artifacts/day08/session_lifecycle.md`, `context_compaction.md` and
`isolation_report.md` from real `MemorySessionService`/`SessionStore`/
`build_memory_context`/`compact_session` calls (plus one real two-turn
`POST /ask` conversation) — the same "generate from a real run, assert
redaction before writing" discipline `day06_generate_trace_artifact.py`
already established; no value in any of the three files is raw turn or
summary content.

Local session data (`data/sessions/`, SQLite) is gitignored, the same as
`data/vectors/` — a fresh checkout needs no session database on disk to
run the test suite, since every test uses `InMemorySessionStore`.

## Day 9 — Ontology Registry, Gate-A & Lane Selection

Adds a governed control plane in front of the Day 5–8 pipeline: `src/aico/control/`
defines what a request is allowed to *mean* (Mode-A) and which route it is
allowed to take, strictly separate from Mode-B facts and from Gate-B
permission/tenant/PII checking (a later day). The assignment's own rule:
"the model may help interpret language, but allowed meaning and the
allowed route come from governed Mode-A definitions and deterministic
policy."

- `ontology.py` — typed `OntologyDocument`/`Domain`/`Concept`/`Intent`/
  `LaneId` models (Pydantic, `extra="forbid"`), self-validating: duplicate
  domain/concept/intent ids, dangling relationship targets, an intent
  referencing an undeclared domain, and a lane not enabled for the loaded
  ontology version are all rejected at parse time, not by ad hoc checks
  downstream.
- `ontology_registry.py` — loads and validates the committed
  `ontology/registry.v1.json` (byte-identical to the Day 9 resource pack's
  fixture) into those typed objects once, then exposes read-only lookups
  (`get_domain`/`get_concept`/`get_intent`/`resolve_concepts`) — every
  collection accessor returns a fresh `tuple`, and there is no mutator
  method anywhere, so nothing at runtime (request or model output) can
  create, widen, or mutate a registry entry.
- `gate_a.py` — deterministic domain/intent classification *before* lane
  selection: Day 5's own `evaluate_policy` first (a `block` outcome short-
  circuits to `GateAStatus.BLOCKED`), then an exact governed-phrase match,
  then governed content-term overlap scoring (synonyms folded in from the
  registry's own `Concept.synonyms`) — never free-form fuzzy matching, and
  never a Model Gateway call. Produces exactly one of `matched` /
  `ambiguous` / `unsupported` / `blocked`; an ambiguous result also gets a
  deterministically generated `clarification_question`, built only from
  governed `Intent.description`/`Domain.name` text.
- `lane_selector.py` — routes a `GateADecision` to one of the five
  governed lanes (`rag` / `mode_b` / `clarify` / `block` /
  `safe_fast_path`) using only the matched intent's own
  `Intent.allowed_lanes`; `mode_b` is *selected*, never executed (no
  database import anywhere in the module); an optional
  `config/control-plane.yaml`-driven `enabled_lanes` can only narrow which
  lanes a deployment allows, never widen what the ontology itself permits.
- `aico/rag/control_plane_answer_service.py` — `ControlPlaneAnswerService`
  wires Gate-A and the lane selector in front of the unmodified Day 5
  `GroundedAnswerService`: trusted identity → session resolution → Day 5
  input policy → (optional Task 8 reference resolution) → Gate-A →
  lane selector → selected-lane behavior. Only the `rag` branch ever calls
  the wrapped RAG service (retrieval/Model Gateway); `clarify`/`block`/
  `safe_fast_path` return a typed result directly. Each of the `gate_a`/
  `lane_selection` stages runs in its own OTel span carrying
  `ontology_version`/`domain`/`intent_id`/`status`-or-`lane`/`reason_code`/
  `latency_ms` — never the raw question or generated clarification text —
  and inherits `request_id`/`correlation_id` through Day 6's existing
  parent-span mechanism.

Session memory (Day 8) may resolve a dangling reference ("its invoice
policy" → "Supplier Alpha invoice policy") via `resolve_reference` before
Gate-A ever classifies the text, but the final intent/lane is still
decided independently by the unmodified Gate-A/lane selector — memory
cannot create a concept, widen an allowed intent, override an
`unsupported` result, or turn a remembered (possibly adversarial) subject
into trusted policy; every resolved request is still run through Day 5's
own input policy exactly like a first turn.

`ControlPlaneAnswerService` is not yet wired into `api/app.py`'s `/ask` —
the committed `ontology/registry.v1.json` is a deliberately small,
synthetic Mode-A registry covering three intents, and routing the real,
much broader `/ask` corpus (`data/documents/`, Day 7's golden eval set)
through it today would classify most of Day 7's permanent regression
questions `unsupported`, silently breaking the Day 7 gate. This module is
Day 9's complete, independently testable integration, ready for a future
day to route real traffic through once the governed ontology covers the
corpus it gates.

```
uv run pytest -q
uv run python -m aico.evals.day07
uv run python scripts/day09_generate_control_plane_artifacts.py
```

1208 tests pass overall (up from 981 after Day 8) — 227 new for Day 9,
across `tests/test_day09_*.py` (ontology/registry, Gate-A, lane selector,
ambiguity, memory interaction incl. the real-session `build_reference_context`
derivation, no-fall-through counters, observability, control-plane config,
control-plane integration, real-HTTP API integration incl. the
memory-assisted follow-up worked example, and a dedicated regression file
that re-runs the real Day 7 evaluation CLI and the Day 8 session-isolation
matrix in-process). No-fall-through is proven with
instrumented counting fakes (not lane labels alone): `clarify`/`block`/
`unsupported` all show zero retrieval and zero Model Gateway calls, a
`mode_b` selection still succeeds with `sqlite3.connect` patched to raise,
and the same fakes are shown reaching exactly one call each for the `rag`
lane, proving the counters are actually wired into the pipeline. The Day 7
regression gate and Day 8 isolation/memory tests are unmodified and still
pass — `evals/baseline_v1.json` is untouched by any Day 9 change.

`scripts/day09_generate_control_plane_artifacts.py` regenerates
`artifacts/day09/ontology_report.md`, `gate_a_decisions.md` and
`lane_selection_report.md` from real `OntologyRegistry.load()`/
`GateA.classify()`/`ControlPlaneAnswerService.answer()` calls (the lane
report's call counts come from the same counting-fake technique the
no-fall-through tests use) — including one real invalid-registry
rejection (a duplicate `concept_id`) captured as evidence, not described
hypothetically.

## Day 10 — Gate-B Permissions, Tenant Isolation, PII & Safe Disclosure

Adds the authorization/disclosure boundary Day 9's own module docstring
named as a later day: Gate-A/the lane selector decide what a request
*means* and which governed route it takes; Gate-B decides whether the
*trusted caller* may actually proceed, under which tenant/data scope, and
what may be disclosed. The assignment's standing rule: "authorization
scope comes from trusted identity and governed policy — the model,
memory, request body, and repair logic may never widen it."

- `policy_models.py` — typed, self-validating `GateBPolicyDocument`
  (`policy_version`/`status`/`roles`/`permissions`/`data_classifications`/
  `pii_categories`/`disclosure_profiles`/`rules`, Pydantic, `extra="forbid"`):
  duplicate role/permission/disclosure-profile/rule ids, an unknown
  role/permission/data-classification/PII-category/disclosure-profile
  reference, an unrecognized lane (typed against Day 9's own closed
  `LaneId`), and — when a governed `OntologyRegistry`'s intent ids are
  supplied as validation context — an unrecognized ontology intent are
  all rejected at parse time. Also defines the three shared, pure
  decision primitives every other Day 10 module builds on rather than
  re-implementing: `is_data_classification_permitted()`,
  `is_pii_category_permitted()`, `resolve_disclosure_action()` (fail-closed
  `DENY` for any field a disclosure profile does not declare).
- `policy_registry.py` — loads and cross-validates the committed
  `policy/gate_b_policy.v1.json` (byte-identical to the Day 10 resource
  pack's fixture) against the real committed `OntologyRegistry`, then
  exposes read-only lookups and an O(1) `find_rule(role_id, intent_id,
  lane)` index — rejecting a policy where more than one rule would govern
  the same combination, so rule matching is always unambiguous.
- `gate_b.py` — `GateB.authorize()`: ordered, fail-closed stages (trusted
  identity present → upstream Gate-A/lane actually resolved something →
  governed intent → trusted role → matching, allowed rule → the matched
  role was actually granted the rule's own `required_permission` →
  tenant scope → data classification), each denying immediately rather
  than falling through. No stage, and no code path in this module at all,
  can widen scope — `effective_tenant_scope`/`effective_data_classes`/
  `effective_pii_policy` are always `requested ∩ trusted ∩ policy rule`,
  never a union, and there is no "if no rule matched: allow" anywhere.
  `clarify` is reserved for exactly one safe case (a matched, allowed
  rule authorizing more than one data classification and none requested)
  — never a missing role/tenant/permission/clearance, which always
  denies instead of prompting the caller to self-assert one.
- `disclosure.py` / `redaction.py` — `apply_disclosure()` turns one
  `GateBDecision` plus typed candidate fields into a `SafeDisclosureView`:
  a disallowed/undeclared field is omitted, a redactable field is masked
  by `redaction.py`'s pure, deterministic string transforms (no model
  call, ever), a permitted field passes through unchanged, and the
  original candidate object is never mutated (every type involved is a
  frozen dataclass).
- `aico/rag/control_plane_answer_service.py` — extended (Day 9's own
  class, not a new one) with an optional `policy_registry:
  PolicyRegistry | None` constructor field: when set, Gate-B runs after
  the lane selector and before any `rag`/`mode_b` protected-lane
  behavior, in its own `"gate_b"` OTel span
  (`ontology_version`/`policy_version`/`rule_id`/`intent_id`/`lane`/
  `decision`/`reason_code`/`effective_scope_summary`/`disclosure_profile`/
  `latency_ms` — never the raw question or an identity field), and a
  `deny`/`clarify` decision returns a typed `GateBDenied`/
  `GateBAuthorizationClarify` result with zero retrieval or Model Gateway
  calls, proven with the same instrumented-counting-fake technique Day 9's
  no-fall-through tests use (`test_day10_no_fallthrough.py`,
  `test_day10_control_plane_integration.py`).
- `api/dependencies.py` / `api/control_plane.py` — the live `/ask/governed`
  route now forwards the already-resolved trusted `identity` (Day 6) into
  `service.answer()` unconditionally, and `get_control_plane_answer_service`
  resolves a real `get_policy_registry()` and activates it on the service
  whenever `config/control-plane.yaml`'s `gate_b.enabled` flag is `true`.
  **Committed default: `true`** — a shipped deployment authorizes through
  Gate-B by default; it does not ship ungoverned waiting for an operator
  to flip a flag. The Day 9 synthetic ontology's three intents (identities
  like `TENANT-SYN-001`, no governed role at all) and the Day 10 policy's
  governed roles/tenant (`TENANT-A` / `supplier_reader` /
  `sourcing_analyst` / `compliance_reviewer`) are deliberately separate
  synthetic spaces, so `test_day09_api_integration.py` explicitly
  overrides this flag back to `false` for its own requests rather than
  depending on the shipped default to stay ungoverned on its behalf —
  the one committed opt-out, not the default anyone else inherits.
- `api/control_plane_contracts.py` — `GovernedAskRequest` extends `AskRequest`
  with exactly one Gate-B-relevant field, `data_class`: an optional,
  caller-declared *preference* among the classifications the caller's own
  matched rule authorizes — never a grant (a value the matched rule does
  not itself authorize still denies, `data_classification_not_allowed`).
  Without it, every committed rule's 2+ allowed data classes made
  `clarify` the only HTTP-reachable non-deny Gate-B outcome; declaring it
  is what makes a genuine `allow` reachable over a real request.
  `test_day10_api_integration.py` proves the live route really does reach
  Gate-B (the committed `gate_b.enabled: true` default, against a real
  Day 10 governed identity) — `deny` (unknown role), `deny` (a declared
  `data_class` the matched rule does not authorize), `clarify` (no
  `data_class` declared, matched rule authorizes more than one), and a
  genuine `allow` (an authorized `data_class` declared — retrieval/the
  Model Gateway reached exactly once each) all proven over real HTTP
  requests, plus the identical request with `gate_b.enabled: false`
  reproducing Day 9's own `"answered"` outcome unchanged, side by side.

```
uv run pytest -q
uv run python -m aico.evals.day07
uv run python scripts/day10_generate_gate_b_artifacts.py
```

1471 tests pass overall (up from 1208 after Day 9) across `tests/test_day10_*.py`
(policy model/registry validation incl. every "Required validation"
rejection and the real committed policy, Gate-B's fail-closed stages incl.
the `permission_not_granted` defense-in-depth check, tenant-scope/effective-
scope intersection, PII/disclosure against every `pii_disclosure_cases.json`
case, no-fall-through counters at both the `GateB`-direct and
`ControlPlaneAnswerService` levels, decision-provenance/observability
spans, `/ask/governed` reached live over real HTTP with Gate-B activated,
and a dedicated regression file re-running Day 7's evaluation CLI plus the
Day 8/9 regression suites in-process), plus two `gate_b.enabled`
activation-toggle tests in `tests/test_day09_control_plane_config.py`. The
Day 7 regression gate and Day 8/9 tests are unmodified and still pass —
`evals/baseline_v1.json` is untouched by any Day 10 change.

`scripts/day10_generate_gate_b_artifacts.py` regenerates
`artifacts/day10/gate_b_decisions.md`, `tenant_isolation.md` and
`disclosure_report.md` from real `GateB.authorize()` /
`ControlPlaneAnswerService.answer()` / `apply_disclosure()` calls against
the real committed policy — including the cross-tenant/zero-protected-call
proof and a deterministic redaction example, no raw PII value anywhere in
the output.

## Key design decisions

**Day 1**
- **Two distinct notions of "token"**: the chunker sizes chunks by
  whitespace-separated words (regex `\S+` — a word-based approximation,
  not a real tokenizer/BPE count), tracked as `token_count` per chunk.
  BM25 separately tokenises with `[a-z0-9]+` on lowercased text for
  term matching. They're deliberately not unified — chunk sizing and
  ranking have different requirements.
- **Chunking boundary preference**: prefer the nearest sentence boundary
  at or before the token limit (sentence end punctuation or a blank
  line), fall back to the word boundary at the limit if no sentence
  boundary is found in range. Never breaks mid-word.
- **Stopwords**: not removed. BM25's IDF term already suppresses very
  common words; a separate stopword list would mostly duplicate that
  effect while risking accidental removal of a meaningful domain word.
- **k1 = 1.5, b = 0.75**: named constants (`BM25_K1`, `BM25_B`) in
  `bm25.py`, standard BM25 defaults, not tuned against this eval set.
- **Tie-break**: equal BM25 scores are ordered by `chunk_id` ascending,
  so ranking is stable and reproducible across runs.
- **Chunk ID**: `sha256(f"{source_file}:{char_start}:{char_end}")`,
  truncated to 16 hex characters — never a timestamp, UUID, or running
  index, so re-ingesting unchanged input reproduces identical IDs. A
  separate `content_hash` field (full SHA-256 of the chunk text) is
  also stored per chunk, but it isn't part of the chunk_id input.
- **Section tracking**: each chunk records the nearest preceding
  markdown heading as `section` (`null` if the chunk starts before the
  first heading in the document).

**Day 2**
- **Provider call surface**: one interface (`EmbeddingProvider`), one real
  implementation (`AzureEmbeddingProvider`, Day 3 — delegates to the
  Model Gateway instead of calling the provider directly), one fake
  (`FakeEmbeddingProvider`) — all in `embedding_provider.py`. Everything
  else (cache, search, eval) depends only on the interface.
- **API route**: the real provider calls Foundry's unified Model Inference
  API (`{endpoint}/models/embeddings`, `model` in the JSON body,
  `api-version=2024-05-01-preview`) rather than the older per-deployment
  REST path some Azure resources expose — provider-agnostic, and the more
  idiomatic route for a `*.services.ai.azure.com` Foundry resource
  specifically.
- **`data/vectors/` is gitignored** — it's a build output, fully
  reproducible by `embed`, and keeping it out of the repo is what makes
  the "second run makes zero calls" review demo meaningful on a fresh
  clone (a committed cache would start warm). `data/index/` is a build
  output too but, unlike this cache, is committed anyway (see "Run the
  tests" above) — a fresh clone needing the test suite to run
  immediately outweighs the (here, nonexistent) "start warm" concern,
  since `ingest` makes no external calls to warm a cache against in the
  first place.
- **RRF k = 60**: the standard starting point. Raising `k` flattens the
  fused-score curve (rank 1 and rank 50 end up closer together — fusion
  behaves more like a broad rank-sum vote); lowering it sharpens the curve
  (a top rank in either mode dominates — fusion behaves closer to "trust
  whichever mode ranked it highest").
- **No_match score floors are per-mode and independently justified**, not
  copied from Day 1's `NO_MATCH_SCORE_FLOOR`: a BM25 score, a cosine score
  and an RRF score don't live on the same scale, so one shared number
  would be meaningless. See `SCORE_FLOOR_NOTE` in `day02.py` and the
  no_match section of `mode_comparison.md` for the reasoning and honestly
  reported outcome per mode (bm25 abstains 2/3 in the last run; vector and
  hybrid abstain 0/3 — a real, investigated finding, not a bug: see the
  report for why).
- **Retry scope**: `AzureEmbeddingProvider` itself never retries — bounded
  retry is Task 3 of Day 3's Model Gateway, not this file. `day02.py`'s
  `_RetryingProvider` is a thin wrapper local to that one measurement
  script only, so a ~19-call evaluation run against a flaky shared dev
  endpoint can actually complete — it does not change the provider
  interface or any other caller's behaviour.

## Folder structure

Verified against `git ls-files` on 2026-09-10 — every path below exists in
the repo as shown; nothing here is aspirational.

```
aico-ai-engineer-lab/
  README.md                        this file
  pyproject.toml                    project metadata, deps, [tool.pytest.ini_options] (testpaths=tests) -
                                     `import aico` works under `uv run` because `uv sync` installs the
                                     package itself in editable mode (hatchling), not via a pythonpath setting
  uv.lock                           uv's resolved + hashed dependency lockfile
  .python-version                   Python version uv pins the .venv to
  .gitignore
  .dockerignore                     Day 7 Task 12 — keeps .venv/__pycache__/local data out of the build context
  .env                              endpoint + (legacy Day 2) provider values (gitignored, never committed)
  Dockerfile                        Day 7 Task 12 — multi-stage build: uv-installed deps -> clean runtime stage
  docker-entrypoint.sh              Day 7 Task 12 — container entrypoint (no baked-in credentials)
  .github/workflows/
    day07-quality-gate.yml          Day 7 Task 13 — CI: uv sync --frozen -> ruff -> pytest -> aico.evals.day07,
                                     evaluation failure fails the job, baseline never auto-updated
  config/
    model-routing.yaml              Day 3 — deployment aliases, resilience/budget/routing policy (no secrets)
    control-plane.yaml              Day 9 Task 12 — registry path, enabled lanes, clarification policy,
                                     model-assisted-interpretation setting (off); Day 10 Task 13 —
                                     gate_b.enabled/policy_path (on by default, see Day 10 section above);
                                     no ontology/policy data of its own, no secrets
  ontology/
    registry.v1.json                Day 9 Task 1/2 — committed, read-only governed Mode-A registry (byte-identical
                                     to data/day09_pack/fixtures/ontology_registry_v1.json)
    README.md                       Day 9 — what the registry is, why it's read-only, how a v2 would be added
  policy/
    gate_b_policy.v1.json           Day 10 Task 1/2 — committed, read-only governed Gate-B policy
                                     (byte-identical to data/day10_pack/fixtures/gate_b_policy_v1.json)
    README.md                       Day 10 — what the policy is, why it's read-only, how a v2 would be added
  contracts/schema/
    cited_answer.v1.schema.json           Day 4 — generated from CitedAnswer, never hand-edited
    response_envelope.v1.schema.json      Day 4 — generated from ResponseEnvelope, never hand-edited
  docs/adr/
    ADR-003-model-routing-and-fallback.md   Day 3 — gateway/routing/fallback design decision
    ADR-004-day4-contract-versioning.md     Day 4 — backward-compatibility rule + breaking-change examples
    ADR-005-ruff-adoption.md                Day 7 Task 13 — why ruff is the CI lint gate
  scripts/
    day03_gateway_demo.py           Day 3 — regenerates artifacts/day03/gateway_demo.md's scenarios
    day04_generate_schemas.py       Day 4 — regenerates contracts/schema/*.json from the source models
    day04_generate_validation_report.py  Day 4 — regenerates artifacts/day04/validation_report.md
    day05_generate_answer_artifacts.py   Day 5 Task 9 — regenerates artifacts/day05/supported_answer.md
                                          and insufficient_evidence.md from the real answer_service pipeline
    day05_generate_attack_report.py      Day 5 Task 8 — regenerates artifacts/day05/attack_results.md
                                          from the real normalization -> input_policy -> answer_service pipeline
    day06_generate_trace_artifact.py     Day 6 Task 11 — regenerates artifacts/day06/trace_summary.md from
                                          one real /ask call's spans + metrics (fake gateway, no network call)
    day07_generate_stability_report.py       Day 7 Task 5 — regenerates artifacts/day07/stability_report.md from
                                              real repeated runs (refusal + groundedness signals)
    day07_generate_failure_classification_report.py  Day 7 Task 6 — demonstration generator; superseded for
                                                       normal use by `uv run python -m aico.evals.day07` itself
    day07_update_baseline.py                 Day 7 Task 8 — the deliberate, separate baseline-update workflow
                                              (dry-run by default; never invoked by CI or normal evaluation)
    day07_controlled_regression_proof.py     Day 7 Task 10 — runs the real gate under approved/weakened/restored
                                              retrieval config, regenerates artifacts/day07/controlled_regression.md
    day08_generate_memory_artifacts.py       Day 8 Task 13 — regenerates artifacts/day08/*.md from real
                                              MemorySessionService/SessionStore/context-builder/compaction calls
    day09_generate_control_plane_artifacts.py  Day 9 Task 13 — regenerates artifacts/day09/*.md from real
                                                OntologyRegistry/GateA/ControlPlaneAnswerService calls
    day10_generate_gate_b_artifacts.py         Day 10 Task 15 — regenerates artifacts/day10/*.md from real
                                                GateB.authorize()/ControlPlaneAnswerService.answer()/
                                                apply_disclosure() calls against the real committed policy
  src/aico/
    api/                             Day 6 — the typed FastAPI service (Tasks 1-6, 10)
      app.py                         Task 1 — FastAPI app, POST /ask, middleware/router wiring
      contracts.py                   Task 1 — public AskRequest/AskResponse, separate from AnswerResult
      identity.py                    Task 2 — trusted-identity dependency (verified JWT, fails closed)
      correlation.py                 Task 3 — request/correlation ID middleware + contextvars
      errors.py                      Task 4 — shared ErrorResponse envelope + exception handlers
      request_protection.py          Task 4 — Content-Type/size-limit ASGI middleware
      request_cancellation.py        Task 5 — client-disconnect-to-CancellationToken plumbing
      health.py                      Task 6 — liveness/readiness/dependency-health endpoints + policy
      instrumentation.py             Task 8 — MetricsGateway/MetricsRetriever wrappers
      dependencies.py                Task 10 — every DI provider (answer service, gateway, retriever,
                                      policy evaluator, both dependency-health checks, Day 9's ontology
                                      registry/control-plane config, Day 10's policy registry)
      control_plane.py               Day 9 Task 9 — POST /ask/governed: the live HTTP boundary over
                                      ControlPlaneAnswerService; Day 10 Task 13 — forwards trusted
                                      identity into it unconditionally, activating Gate-B whenever
                                      config/control-plane.yaml's gate_b.enabled is true
      control_plane_contracts.py     Day 9 Task 9 — public GovernedAskResponse contract, all nine
                                      pipeline outcomes mapped to it; Day 10 Task 13 — the two
                                      Gate-B-native outcomes (gate_b_denied/gate_b_clarify) added, plus
                                      GovernedAskRequest(AskRequest) adding the one Gate-B-relevant
                                      request field (data_class — a narrowing preference, never a grant)
      session_flow.py                Day 8 Task 4/11 (extracted for Day 9) — resolve_session/record_turn,
                                      shared unmodified by POST /ask and POST /ask/governed so neither
                                      route drifts out of sync on isolation/logging/retry semantics
    observability/                   Day 6 — telemetry configuration (Tasks 7-9)
      logging.py                     Task 7 — structured JSON log_event() + stdout handler setup
      metrics.py                     Task 8 — OpenTelemetry Metrics API, in-memory reader
      telemetry.py                   Task 9 — OpenTelemetry TracerProvider, in-memory exporter
    platform/
      model_gateway.py              Day 3 — typed chat/embed boundary (ModelGateway)
      config.py                     Day 3 — validated config/model-routing.yaml loading
      errors.py                     Day 3 — normalized ModelGatewayError hierarchy
      foundry_adapter.py            Day 3 — the only file that calls the provider over HTTP
    contracts/
      models.py                     Day 4 — versioned Pydantic contracts (CitedAnswer, ResponseEnvelope)
      errors.py                     Day 4 — typed ValidationFailure (parse/contract/semantic/repair)
      validator.py                  Day 4 — raw string -> typed contract or typed failure
      semantic.py                   Day 4 — S1-S5 semantic rules, run only after contract validation
      repair.py                     Day 4 — one bounded repair attempt through the Day 3 ModelGateway
    rag/                            Day 5 — the grounded retrieve-to-answer path (Tasks 1-3)
      __init__.py
      answer_service.py             Day 5 Task 1 — GroundedAnswerService: normalize -> policy -> retrieve
                                     -> prompt -> gateway -> Day 4 validation -> citation validation
                                     (Day 6 Tasks 5/9/10 add cancellation, OTel spans per stage, and an
                                     injectable policy_evaluator - additive only, logic unchanged)
      prompt_builder.py             Day 5 Task 2 — explicit SYSTEM / USER / EVIDENCE message separation;
                                     evidence is always labelled untrusted data, never merged into system
      citation_validator.py         Day 5 Task 3 — cited_ids ⊆ retrieved_context_ids, fails closed
      support_validator.py          Day 5 (post-review hardening) — citation-ID membership does not prove
                                     answer content is supported; bounded lexical-overlap check, run after
                                     citation validation, catches fabrication and poisoned-directive claims
      control_plane_answer_service.py  Day 9 Task 9/11 — ControlPlaneAnswerService: wires Gate-A/lane selector
                                     in front of the unmodified GroundedAnswerService (identity -> session ->
                                     Day 5 policy -> Gate-A -> lane selector -> selected-lane behavior); only
                                     the rag branch reaches retrieval/Model Gateway; gate_a/lane_selection
                                     OTel spans. Not yet wired into api/app.py's /ask (see Day 9 section above)
    security/                       Day 5 — input-side defense (Tasks 5-6)
      __init__.py
      normalization.py              Day 5 Task 5 — bounded, deterministic obfuscation normalization
      input_policy.py               Day 5 Task 6 — deterministic allow / clarify / block classifier
    retrieval/
      chunker.py                    Day 1 — offset-exact chunking
      ingest.py                     Day 1 — CLI: documents -> index.json
      bm25.py                       Day 1 — from-scratch BM25 ranking
      search.py                     CLI: query -> ranked chunks (bm25 / vector / hybrid)
      embedding_provider.py         provider interface + real (Day 3: gateway-backed) + fake
      vector_index.py               Day 2 — vector cache, cosine similarity search
      embed.py                      Day 2 — CLI: chunks -> vector cache
      hybrid.py                     Day 2 — reciprocal-rank fusion
    evals/                           Day 7 — the evaluation harness (Tasks 2-7, 9)
      day01.py                      CLI: Hit@1 / Hit@5 / MRR scorer (bm25, two chunk configs)
      day02.py                      CLI: three-mode scorer (bm25 / vector / hybrid)
      dataset.py                    Day 7 Task 2 — typed GoldenCase loader + train/development/holdout split
      metrics.py                    Day 7 Task 3 — deterministic checks: Hit@K/MRR/citation validity/
                                     refusal-attack scoring, no model call
      groundedness.py               Day 7 Task 4 — separate model-based groundedness path through the
                                     Model Gateway, versioned grader prompt, reported apart from metrics.py
      stability.py                  Day 7 Task 5 — repeated-run core (run_repeated) + refusal/groundedness
                                     observation shapes, feeds artifacts/day07/stability_report.md
      failure_classifier.py         Day 7 Task 6 — one primary failure type per failed case (fixed 6-value
                                     taxonomy: chunking/retrieval/prompt/citation/refusal/evaluator)
      regression.py                 Day 7 Task 7 — typed Thresholds + the zero-tolerance safety gate applied
                                     to one run's aggregate metrics
      day07.py                      Day 7 Task 9 — the one complete `uv run python -m aico.evals.day07`
                                     command: validate -> evaluate -> compare vs baseline/thresholds ->
                                     classify -> JSON+Markdown reports -> exit 0/non-zero
    memory/                         Day 8 — the session-memory boundary (Tasks 1-10)
      models.py                     Task 1 — typed SessionState/SessionTurn/MemorySummary (no secrets,
                                     no trusted-identity field)
      store.py                      Task 2 — SessionStore abstraction: SqliteSessionStore (real) +
                                     InMemorySessionStore (deterministic test fake), one shared contract
      service.py                    Task 3/9 — MemorySessionService: identity-bound session resolution
                                     (tenant_id+user_id+session_id only), bounded lost-update retry
      context_builder.py            Task 5/8 — bounded memory-context window (token/turn budget) +
                                     SessionReferenceContext/resolve_reference (Day 9's own reference case)
      summarizer.py                 Task 6 — Summarizer abstraction: FakeSummarizer (tests) +
                                     ModelGatewaySummarizer (real, via the Day 3 gateway only)
      errors.py                     Task 2 — typed SessionError family (SessionNotFoundError, ...),
                                     cross-owner and nonexistent-session denials carry an identical reason
    control/                        Day 9/10 — the Mode-A control-plane + Gate-B authorization/
                                     disclosure boundary (Day 9 Tasks 1-3, 5, 12; Day 10 Tasks 1-9, 13)
      ontology.py                   Day 9 Task 1 — typed OntologyDocument/Domain/Concept/Intent/LaneId,
                                     self-validating (duplicate ids, dangling relationship/lane refs, enum/status)
      ontology_registry.py          Day 9 Task 2 — loads + validates ontology/registry.v1.json, read-only
                                     lookups (fresh tuples, no mutator methods), exposes the active version
      gate_a.py                     Day 9 Task 3/6 — GateA.classify(): Day 5 policy -> exact governed
                                     phrase -> governed content-term overlap; matched/ambiguous/
                                     unsupported/blocked, deterministic clarification-question
                                     generation, no Model Gateway call
      lane_selector.py              Day 9 Task 5 — LaneSelector.select(): routes a GateADecision to one
                                     of the 5 governed lanes from the matched intent's own allowed_lanes only
      policy_models.py              Day 10 Task 1 — typed, self-validating GateBPolicyDocument/Role/
                                     PermissionRule/DisclosureProfile, plus the shared pure decision
                                     primitives is_data_classification_permitted/is_pii_category_permitted/
                                     resolve_disclosure_action
      policy_registry.py            Day 10 Task 2 — loads + cross-validates policy/gate_b_policy.v1.json
                                     against the real OntologyRegistry, read-only lookups, O(1) find_rule
      gate_b.py                     Day 10 Task 3-7/10/12 — GateB.authorize(): ordered fail-closed
                                     stages, effective scope as intersection only, the one safe clarify
                                     case, no memory/model-widening parameter anywhere in its signature
      disclosure.py                 Day 10 Task 9 — apply_disclosure(): GateBDecision + typed candidate
                                     fields -> SafeDisclosureView, no fall-through, no source mutation
      redaction.py                  Day 10 Task 9 — pure, deterministic mask_value() (email/phone/
                                     identifier shapes), never a model call
      models.py                     Day 9/10 Task 3/5 — shared GateADecision/LaneDecision/GateBDecision
                                     typed result shapes
      config.py                     Day 9/10 Task 12/13 — validated config/control-plane.yaml loading
                                     (registry path, enabled lanes, clarification policy,
                                     model-assisted-interpretation, gate_b activation toggle)
      errors.py                     Tasks 2/5/12 (Day 9) + 2/3 (Day 10) — OntologyLoadError/
                                     OntologyLookupError/LaneSelectionError/ControlPlaneConfigurationError/
                                     PolicyLoadError/PolicyLookupError/GateBError
  data/
    documents/                      DOC-001 .. DOC-005 (synthetic, unchanged across all days)
    evals/
      day01_queries.json            10 labelled queries
      day02_queries.json            16 labelled queries (Q01-10 shared with Day 1, Q11-16 new)
    day04_pack/                     Day 4 — supplied requirements/rules/fixtures, used as-is
      README.md
      contract_requirements.md
      semantic_rules.md
      fixtures/
        structured_output_cases.json      12 broken-output cases (D04-01..D04-12)
        existing_caller_v1.json           frozen pre-`warning` caller snapshot
    day05_pack/                     Day 5 — supplied resource pack, used as-is, never edited to pass
      README.md
      grounding_rules.md            the retrieve-to-answer rules Tasks 1-3 implement
      citation_cases.json           supplied citation membership cases (Task 3)
      answer_cases.json             supplied supported / insufficient-evidence cases (Task 4, 9)
      expected_policy_outcomes.md   allow/clarify/block outcome per attack category (Task 6)
    day06_pack/                     Day 6 — supplied resource pack (docs only - fixtures moved to
                                     tests/fixtures/day06/, same convention as Day 5's attack_fixtures.json)
      README.md
      uv_workflow.md
      api_contract_guidance.md
      telemetry_requirements.md
      trace_summary_template.md
    day08_pack/                     Day 8 — supplied resource pack (deterministic lifecycle/isolation/
                                     compaction/safety scenarios; does not prescribe class names/schema)
      README.md
      memory_contract_guidance.md
      context_budget_guidance.md
      fixtures/
        session_lifecycle_cases.json
        isolation_cases.json
        context_compaction_cases.json
        memory_safety_cases.json
    day09_pack/                     Day 9 — supplied resource pack (fixed synthetic inputs, never edited
                                     to make the implementation pass)
      README.md
      ontology_requirements.md      Task 1/2's required validation bullets
      lane_policy.md                Task 5's required routing policy table
      fixtures/
        ontology_registry_v1.json   the governed v1 registry (copied verbatim to ontology/registry.v1.json)
        gate_a_cases.json           6 cases: exact/synonym/structured/unsupported/unknown/blocked
        lane_selection_cases.json   5 cases, one per governed lane
        ambiguity_cases.json        3 cases (AMB-001..003), including the memory-resolved AMB-003
    day10_pack/                     Day 10 — supplied resource pack (fixed synthetic inputs, never edited
                                     to make the implementation pass)
      README.md
      gate_b_policy_requirements.md  Task 1's required validation bullets
      disclosure_rules.md            Task 8/9's PII categories, disclosure actions, masking examples
      fixtures/
        gate_b_policy_v1.json        the governed v1 policy (copied verbatim to policy/gate_b_policy.v1.json)
        permission_cases.json        6 cases (PERM-001..006): allowed/denied doc + structured lookups,
                                      unknown role, unknown intent, lane mismatch
        tenant_scope_cases.json      5 cases (TEN-001..005): same/cross-tenant, mixed requested scope,
                                      request-body/memory override attempts
        pii_disclosure_cases.json    6 cases (PII-001..006) + synthetic_record/field_metadata: allow/
                                      redact/deny across profiles
    index/                         build output (gitignored) - python -m aico.retrieval.ingest
    vectors/                       build output (gitignored) - python -m aico.retrieval.embed
    sessions/                      Day 8 — local SqliteSessionStore data (gitignored; every test uses
                                     InMemorySessionStore instead, so a fresh checkout needs none of this)
  evals/                            Day 7 Tasks 1/7/8 — developer-authored (no Day 7 resource pack supplied)
    golden_v1.json                 Task 1 — >=25 labelled cases across all 6 required categories, split
                                    train/development/holdout
    thresholds_v1.json             Task 7 — explicit, machine-readable, applied release thresholds +
                                    zero-tolerance safety gate
    baseline_v1.json               Task 8 — reviewed baseline; never rewritten by normal evaluation, only
                                    by the separate `--update-baseline` workflow (scripts/day07_update_baseline.py)
    README.md                      design rationale for the dataset/splits/thresholds/baseline above
  artifacts/
    day01/
      chunks_200_40.json            committed chunk set, config A
      chunks_400_80.json            committed chunk set, config B
      metrics.json                  full metrics, both configs
      retrieval_report.md           auto-generated by day01.py
    day02/
      metrics.json                  full per-mode, per-query metrics
      mode_comparison.md            auto-generated three-way comparison report
    day03/
      gateway_demo.md               Day 3 — sanitized demonstration evidence (Task 7)
    day04/
      validation_report.md          Day 4 — auto-generated by day04_generate_validation_report.py
    day05/                          Day 5 Tasks 8-9 — auto-generated, never hand-transcribed
      supported_answer.md           Task 9 — question, retrieved IDs, typed answer, citations, validation result
      insufficient_evidence.md      Task 9 — question, retrieved IDs, insufficient-evidence result, no invented fact/citation
      attack_results.md             Task 8 — fixture ID / category / expected / actual / pass-fail per attack case
    day06/                          Day 6 Task 11 — auto-generated by day06_generate_trace_artifact.py
      trace_summary.md              request/correlation IDs, all 6 trace stages, latency/token/retry,
                                     programmatically-verified redaction check (not a hand-ticked box)
    day07/                          Day 7 — generated by `uv run python -m aico.evals.day07` + Task 5/10 scripts
      evaluation_report.json        Task 11 — full machine-readable run (counts, metrics, splits, safety
                                     gate, threshold/baseline comparison, stability, failures, verdict)
      evaluation_report.md          Task 11 — the same run, human-readable
      stability_report.md           Task 5 — repeated-run case IDs, observed scores, mean/range/variation
      stability_summary.json        Task 5 — machine-readable counterpart
      failure_classification.md     Task 6 — every failed case's primary type + evidence-based reason
      controlled_regression.md      Task 10 — approved -> weakened (FAIL, non-zero exit) -> restored (PASS)
    day08/                          Day 8 Task 13 — generated by day08_generate_memory_artifacts.py
      session_lifecycle.md          sanitized evidence: creation, two-turn follow-up, load, expiry, clear
      context_compaction.md         configured budget/limit, size before/after, compacted turn IDs,
                                     summary provenance, confirmation retrieved evidence stays separate
      isolation_report.md           same-owner / cross-user / cross-tenant / cross-session / nonexistent-
                                     session outcomes, no raw conversation content
    day09/                          Day 9 Task 13 — generated by day09_generate_control_plane_artifacts.py
      ontology_report.md            version, domains/concepts/intents/lanes summary, validation result,
                                     one real invalid-registry rejection (duplicate concept_id)
      gate_a_decisions.md           exact/synonym/ambiguous/unsupported/memory-assisted-follow-up cases,
                                     each with actual GateA.classify() output
      lane_selection_report.md      Gate-A result / selected lane / reason / retrieval-model-call counts
                                     per case, referencing the Task 10 counter evidence for clarify/block
    day10/                          Day 10 Task 15 — generated by day10_generate_gate_b_artifacts.py
      gate_b_decisions.md           allowed/denied/unknown-role/lane-mismatch/missing-rule/clarify cases,
                                     each with actual GateB.authorize() output (rule_id, reason_code,
                                     effective_scope_summary, policy_version)
      tenant_isolation.md           same-tenant allowed / cross-tenant denied cases, zero-protected-call
                                     proof on denial, effective scope for the allowed case
      disclosure_report.md          public/internal-allowed, deterministically-redacted, and denied-
                                     sensitive-field examples from a real apply_disclosure() call; no raw
                                     protected PII value anywhere in the report itself
  tests/
    __init__.py
    conftest.py                     Day 8 Task 4 — shared fixtures (get_session_store override so
                                     every /ask-exercising test uses InMemorySessionStore, never the
                                     real on-disk SqliteSessionStore)
    fixtures/
      day04/
        existing_caller_v1.json           mirrors data/day04_pack/fixtures/, read by test_day04_compatibility.py
        structured_output_cases.json      mirrors data/day04_pack/fixtures/, read by test_day04_broken_output_suite.py
      day05/attacks/
        attack_fixtures.json              the fixed >=8-case attack corpus (Task 8) — canonical copy, read
                                           directly by the Day 5 policy/attack tests and by
                                           scripts/day05_generate_attack_report.py
      day06/
        api_cases.json                    synthetic Content-Type/size/validation/correlation cases
        identity_claim_cases.json         synthetic trusted-principal claims cases (allow/reject)
        dependency_health_cases.json      synthetic dependency-outage combinations
      (Day 8/9 tests read their fixtures directly from data/day08_pack/fixtures/ and
      data/day09_pack/fixtures/ — no separate tests/fixtures/day08|day09/ copy is kept)
    test_chunker.py                 (11)
    test_bm25.py                    (6)
    test_ingest.py                  (4)
    test_day01_eval.py              (14)
    test_embedding_provider.py      (7)
    test_vector_index.py            (10)
    test_embed.py                   (6)
    test_hybrid.py                  (4)
    test_search.py                  (9)
    test_day2_regression.py         Day 3 — proves the gateway migration is behavior-preserving
    test_model_gateway.py           Day 3 — typed contract, SDK isolation, config validation (16)
    test_model_gateway_retry.py     Day 3 — timeout/cancellation/bounded retry with jitter (16)
    test_model_gateway_routing.py   Day 3 — routing policy and safe fallback (12)
    test_model_gateway_logging.py   Day 3 — sanitized logging, nothing sensitive logged (10)
    test_foundry_adapter_identity.py       Day 3 — identity-based auth, no credential in source (7)
    test_foundry_adapter_normalization.py  Day 3 — HTTP status -> typed ModelGatewayError
    test_day04_contracts.py         Day 4 — versioned contracts + contract/schema validation (58)
    test_day04_semantic_validation.py      Day 4 — semantic rules S1-S5 (20)
    test_day04_repair.py            Day 4 — bounded repair + gateway boundary (20)
    test_day04_broken_output_suite.py      Day 4 — all 12 fixture cases end to end (17)
    test_day04_compatibility.py     Day 4 — backward compatibility + versioning (10)
    test_day05_grounding.py         Day 5 Task 1/2/10 — prompt boundaries, full orchestration, gateway/contract/retrieval path proofs (25)
    test_day05_citations.py         Day 5 Task 3 — valid / forged / multiple-citation membership, fail-closed (11)
    test_day05_insufficient_evidence.py    Day 5 Task 4 — unsupported question invents no fact or citation (10)
    test_day05_normalization.py     Day 5 Task 5 — bounded obfuscation normalization, benign text untouched (12)
    test_day05_input_policy.py      Day 5 Task 6 — allow/clarify/block over all 9 supplied fixtures, determinism (18)
    test_day05_poisoned_documents.py       Day 5 Task 7 — malicious retrieved text cannot override system behavior (13)
    test_day05_answer_support.py    Day 5 (post-review hardening) — answer-support lexical-overlap validation,
                                     including the fabrication and poisoned-directive probes (15)
    test_day06_api.py               Day 6 Task 1 — OpenAPI, typed /ask success, API/domain separation (5)
    test_day06_identity.py          Day 6 Task 2 — trusted identity, all identity_claim_cases.json fixtures (18)
    test_day06_correlation.py       Day 6 Task 3 — ID generation, header echo, contextvar propagation (7)
    test_day06_errors.py            Day 6 Task 4 — Content-Type/size rejection, shared error envelope (8)
    test_day06_cancellation.py      Day 6 Task 5 — deterministic mid-flight cancellation + HTTP-level proof (4)
    test_day06_health.py            Day 6 Task 6 — liveness/readiness/dependency-health, all 3 fixtures (9)
    test_day06_observability.py     Day 6 Tasks 7-9 + 12 — structured logs, metrics, tracing, redaction (18)
    test_day06_dependency_injection.py     Day 6 Task 10 — every DI seam independently replaceable (5)
    test_day07_dataset.py           Day 7 Task 1/2 — schema, >=25 cases, all 6 categories, unique IDs,
                                     split correctness, holdout-not-used-for-tuning (23)
    test_day07_metrics.py           Day 7 Task 3 — Hit@K/MRR/citation-validity/refusal-attack scorers (36)
    test_day07_groundedness.py      Day 7 Task 4 — model-based groundedness path, versioned grader (15)
    test_day07_live_groundedness.py Day 7 Task 4 — same path against a real Model Gateway call (9)
    test_day07_stability.py         Day 7 Task 5 — repeated-run aggregation, mean/range/variation (20)
    test_day07_failure_classification.py   Day 7 Task 6 — every failed case gets exactly one primary type (22)
    test_day07_safety_gate.py       Day 7 Task 7 — zero-tolerance safety gate overrides aggregate score (21)
    test_day07_baseline_update.py   Day 7 Task 8 — baseline immutable under normal eval, separate deliberate
                                     update path only (29)
    test_day07_regression_gate.py   Day 7 Task 9 — the complete `aico.evals.day07` command, exit codes (19)
    test_day07_holdout.py           Day 7 Task 10 — weakened retrieval config fails the gate, non-zero exit (11)
    test_day07_uv_workflow.py       Day 7 — install/lint/test/eval all run through `uv run` (7)
    test_day08_session_contract.py  Day 8 Task 1 — typed SessionState/SessionTurn/MemorySummary shape (28)
    test_day08_session_lifecycle.py Day 8 Task 2 — create/get/save/clear/expire, same contract for both
                                     store implementations (61)
    test_day08_isolation.py         Day 8 Task 3 — same-owner allow; cross-user/cross-tenant/cross-session/
                                     guessed-ID deny, indistinguishable fail-closed reason (29)
    test_day08_followup.py          Day 8 Task 4 — two-turn follow-up demo; current retrieval/citations
                                     still drive the factual answer (12)
    test_day08_context_budget.py    Day 8 Task 5 — bounded context window never exceeds the configured
                                     budget, newest-turns-retained policy (22)
    test_day08_compaction.py        Day 8 Task 6 — older-turn summarization, provenance, no fact/permission
                                     invention, fake + Model-Gateway summarizer paths (27)
    test_day08_memory_not_evidence.py      Day 8 Task 7 — memory separately labelled, memory IDs rejected
                                     by the citation validator, remembered claims stay unsupported (17)
    test_day08_memory_safety.py     Day 8 Task 10 — injection/blocked/unsupported-fact/forged-citation
                                     turns in memory cannot become trusted policy or evidence (17)
    test_day08_concurrency.py       Day 8 Task 9 — optimistic-version lost-update protection, no unbounded
                                     retry (12)
    test_day09_ontology.py          Day 9 Task 1/2 — typed registry, every required rejection (duplicate/
                                     dangling-reference/invalid-enum/missing-version), read-only lookups (41)
    test_day09_gate_a.py            Day 9 Task 3/4/7 — all gate_a_cases.json outcomes, unsupported-fails-
                                     closed sweep, ontology version on every decision (51)
    test_day09_lane_selector.py     Day 9 Task 5 — all lane_selection_cases.json outcomes, mode_b selected
                                     without execution, config-driven enabled_lanes narrowing (25)
    test_day09_ambiguity.py         Day 9 Task 6 — all ambiguity_cases.json outcomes, clarification questions
                                     built only from governed intent/domain text (15)
    test_day09_memory_interaction.py       Day 9 Task 8/9 — AMB-003 + the assignment's worked example, the five
                                     "memory cannot ..." guarantees against the real GateA/LaneSelector, plus
                                     build_reference_context() deriving SessionReferenceContext from a real
                                     session's stored turns (27)
    test_day09_control_plane_integration.py  Day 9 Task 9 — full pipeline order, per-lane routing, no
                                     unnecessary retrieval/model calls (12)
    test_day09_api_integration.py   Day 9 Task 9 — the same pipeline order over a real HTTP request to
                                     POST /ask/governed (real FastAPI app, real registry/config), plus the
                                     memory-assisted follow-up worked example resolved over two real
                                     requests on one session (12)
    test_day09_no_fallthrough.py    Day 9 Task 10 — counting fakes: clarify/block/unsupported = 0 calls,
                                     mode_b never opens sqlite3.connect, rag lane proven to reach both (13)
    test_day09_observability.py     Day 9 Task 11 — gate_a/lane_selection spans, required fields, trace_id
                                     inherited from a parent span, no raw question/clarification text (10)
    test_day09_control_plane_config.py     Day 9 Task 12 — config/control-plane.yaml validated loading,
                                     unknown lane id rejected, no secrets; plus Day 10 Task 13's
                                     gate_b.enabled activation-toggle validation (19)
    test_day09_regression.py        Day 9 Task 14 — safe_fast_path-is-INT-HELP-only, plus the real Day 7
                                     evaluation CLI and Day 8 isolation matrix re-run in-process (4)
    test_day10_policy_registry.py   Day 10 Task 1/2 — typed GateBPolicyDocument, every "Required
                                     validation" rejection, PolicyRegistry load/lookup/find_rule,
                                     is_data_classification_permitted/is_pii_category_permitted/
                                     resolve_disclosure_action as units (89)
    test_day10_gate_b.py            Day 10 Task 3/4/5/6/7/10/12 — all permission_cases.json outcomes,
                                     every deny-by-default stage incl. permission_not_granted
                                     defense-in-depth, tenant-scope intersection, data-classification
                                     enforcement, the clarify boundary closed-set sweep, memory/model
                                     privilege-escalation boundaries (61)
    test_day10_tenant_scope.py      Day 10 Task 5 — all tenant_scope_cases.json outcomes, effective
                                     scope as narrowed intersection, never union/widened (20)
    test_day10_disclosure.py        Day 10 Task 8/9 — all pii_disclosure_cases.json outcomes,
                                     deterministic redaction, safe disclosure view never mutates the
                                     source object (34)
    test_day10_no_fallthrough.py    Day 10 Task 11 — counting fakes: GateB deny/clarify = 0 protected
                                     calls, allow reaches both exactly once, the documented too-late
                                     failure mode reproduced and contrasted (11)
    test_day10_observability.py     Day 10 Task 14 — gate_b/safe_disclosure spans, required
                                     provenance fields, trace_id inherited, no raw PII/question/token (12)
    test_day10_control_plane_integration.py  Day 10 Task 13 — full pipeline order through
                                     ControlPlaneAnswerService with a real policy_registry, rag/mode_b
                                     routing, no-fall-through at the service level (15)
    test_day10_api_integration.py   Day 10 Task 13 — the same pipeline order over a real HTTP request
                                     to POST /ask/governed with gate_b.enabled: true, incl. a genuine
                                     allow (GovernedAskRequest.data_class declared and authorized) and
                                     a deny for a declared-but-unauthorized data_class, plus the
                                     side-by-side gate_b.enabled: false confirmation (5)
    test_day10_regression.py        Day 10 Task 16 — the real Day 7 evaluation CLI and the Day 8/9
                                     regression suites re-run in-process (6)
```

1471 tests pass in total (`uv run pytest -q`, verified 2026-09-10, count
includes parametrized cases as pytest reports them — the per-file counts
in the tree above are the same pytest-collected counts, and do sum to
this number): 544 for Day 1-6, 212 for Day 7, 225 for Day 8, 227 for
Day 9, 263 new for Day 10 (253 across `test_day10_*.py`, 2 gate_b
activation-toggle cases folded into `test_day09_control_plane_config.py`
above, and 8 in `test_day06_identity.py` proving `TrustedIdentity`'s new
`roles` claim — Day 10 Task 3's extension to Day 6's own trust boundary —
parses/rejects correctly and defaults to no roles rather than rejecting
an otherwise-valid identity). `test_day05_answer_support.py`
is new (post-review hardening — see `support_validator.py` above); every
other Day 1-6 test still passes unchanged, satisfying the working-rule
regression requirement, and `uv run python -m aico.evals.day07` remains
green with `evals/baseline_v1.json` unchanged by any Day 8/9/10 commit.

Note: the task brief's "Required structure" names `requirements.txt`; this
repo uses `pyproject.toml` + `uv.lock` (via `uv`) instead, which is the
documented dependency-management choice from Day 1 onward — see Setup
above. Everything else in the brief's required tree (`src/aico/rag/`,
`src/aico/security/`, `tests/fixtures/day05/attacks/`, the required
`test_day05_*.py` files, and `artifacts/day05/*.md`) matches exactly;
`support_validator.py` and `test_day05_answer_support.py` are additive,
post-review hardening beyond the brief's required tree, not a replacement
for anything in it.

Day 6's own required tree (`src/aico/api/`, `src/aico/observability/`,
`test_day06_api.py` / `test_day06_identity.py` / `test_day06_cancellation.py`
/ `test_day06_health.py` / `test_day06_observability.py`, and
`artifacts/day06/trace_summary.md`) matches exactly too — three extra test
files (`test_day06_correlation.py`, `test_day06_errors.py`,
`test_day06_dependency_injection.py`) split Task 3/4/10 coverage out of the
minimum set, which the brief's "equivalent file splitting is acceptable
when responsibilities remain clear and independently testable" explicitly
allows.

Day 7's required tree (`src/aico/evals/{dataset,metrics,groundedness,
regression,failure_classifier}.py`, `evals/{golden,thresholds,baseline}_v1.json`,
`artifacts/day07/*`, the `test_day07_*.py` files, `Dockerfile`, and the CI
workflow) matches exactly; `stability.py` and `test_day07_uv_workflow.py`
are additive. No Day 7 resource pack was supplied by design (`evals/README.md`
documents and justifies every dataset/threshold/baseline decision).

Day 8's required tree (`src/aico/memory/*`, `data/day08_pack/*` used as
supplied, `artifacts/day08/*`, the `test_day08_*.py` files) matches
exactly; `test_day08_session_contract.py` and `test_day08_memory_not_evidence.py`
split Task 1/7 coverage out of the minimum set, the same file-splitting
allowance Day 6 already used.

Day 9's required tree (`src/aico/control/*`, `ontology/registry.v1.json`,
`config/control-plane.yaml`, `data/day09_pack/*` used as supplied,
`artifacts/day09/*`, the `test_day09_*.py` files) matches exactly;
`test_day09_control_plane_integration.py`, `test_day09_observability.py`
and `test_day09_control_plane_config.py` split Task 9/11/12 coverage out
of the minimum set. `gate_a.py` runs a fully deterministic two-tier
classifier and does not use a model-assisted interpreter — the Task 4/14
model-candidate-validation checks are satisfied structurally
(`OntologyRegistry.resolve_concepts()` rejects an unknown candidate id,
`test_day09_ontology.py::test_resolve_concepts_rejects_unknown_candidate_id`)
rather than by an interpreter that exists but is unused.
