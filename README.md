# AICO — Retrieval Engineering (Day 1: Lexical Baseline · Day 2: Embeddings & Hybrid)

Day 1 is a from-scratch chunker and BM25 lexical search baseline. Day 2 adds
semantic retrieval on top of it: a real embedding provider behind one
interface, a persistent content-hash-keyed vector cache, cosine similarity
search, and a hybrid mode that fuses BM25 and vector rankings with
reciprocal-rank fusion — measured against the same corpus and queries so the
two days are directly comparable. No vector database, no retrieval
framework, no LLM.

All data in `data/` is synthetic. No production, MOD, customer, personal or
classified data is used.

## Setup

**PowerShell** (`(.venv) PS ...>` prompt — this is the default on Windows
when you activate via `.venv\Scripts\Activate.ps1` or the VS Code/Windows
Terminal PowerShell profile):
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = "src"
```

**cmd.exe** (`(.venv) C:\...>` prompt, no `PS`):
```cmd
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
set PYTHONPATH=src
```

**macOS/Linux/git-bash**:
```bash
python3 -m venv .venv
source .venv/bin/activate
export PYTHONPATH=src
```

⚠️ **PowerShell vs cmd.exe `set` is a common trap**: in PowerShell, `set` is
an alias for `Set-Variable`, which creates a PowerShell variable, not an
environment variable — running the cmd.exe-style `set PYTHONPATH=src` in a
PowerShell prompt silently does nothing useful, and every `python -m aico...`
command then fails with `ModuleNotFoundError: No module named 'aico'`. Check
your prompt: `(.venv) PS C:\...>` needs `$env:PYTHONPATH = "src"`;
`(.venv) C:\...>` (no `PS`) needs `set PYTHONPATH=src`.

`PYTHONPATH=src` is only needed to invoke the `aico.*` modules directly with
`python -m`. `pytest` doesn't need it.

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

## Run the tests

```
pytest -q
```

274 tests pass, across twenty-one files. Every test is deterministic and
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

Each cache entry (`data/vectors/vectors.json`, gitignored — a build output,
regenerated by this command, same treatment as `data/index/`) stores the
vector plus `chunk_id`, `content_hash`, `model_alias`, `dimensions`,
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
- **`data/vectors/` is gitignored**, the same treatment as `data/index/` —
  it's a build output, fully reproducible by `embed`, and keeping it out
  of the repo is what makes the "second run makes zero calls" review demo
  meaningful on a fresh clone (a committed cache would start warm).
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

```
AI-Assignments-Day4/
  README.md                        this file
  requirements.txt
  pytest.ini                        pythonpath=src, so `pytest` runs standalone
  .gitignore
  .env                              endpoint + (legacy Day 2) provider values (gitignored, never committed)
  config/
    model-routing.yaml              Day 3 — deployment aliases, resilience/budget/routing policy (no secrets)
  contracts/schema/
    cited_answer.v1.schema.json           Day 4 — generated from CitedAnswer, never hand-edited
    response_envelope.v1.schema.json      Day 4 — generated from ResponseEnvelope, never hand-edited
  docs/adr/
    ADR-003-model-routing-and-fallback.md   Day 3 — gateway/routing/fallback design decision
    ADR-004-day4-contract-versioning.md     Day 4 — backward-compatibility rule + breaking-change examples
  scripts/
    day03_gateway_demo.py           Day 3 — regenerates artifacts/day03/gateway_demo.md's scenarios
    day04_generate_schemas.py       Day 4 — regenerates contracts/schema/*.json from the source models
    day04_generate_validation_report.py  Day 4 — regenerates artifacts/day04/validation_report.md
  src/aico/
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
    retrieval/
      chunker.py                    Day 1 — offset-exact chunking
      ingest.py                     Day 1 — CLI: documents -> index.json
      bm25.py                       Day 1 — from-scratch BM25 ranking
      search.py                     CLI: query -> ranked chunks (bm25 / vector / hybrid)
      embedding_provider.py         provider interface + real (Day 3: gateway-backed) + fake
      vector_index.py               Day 2 — vector cache, cosine similarity search
      embed.py                      Day 2 — CLI: chunks -> vector cache
      hybrid.py                     Day 2 — reciprocal-rank fusion
    evals/
      day01.py                      CLI: Hit@1 / Hit@5 / MRR scorer (bm25, two chunk configs)
      day02.py                      CLI: three-mode scorer (bm25 / vector / hybrid)
  data/
    documents/                      DOC-001 .. DOC-005 (synthetic, unchanged both days)
    evals/
      day01_queries.json            10 labelled queries
      day02_queries.json            16 labelled queries (Q01-10 shared with Day 1, Q11-16 new)
    day04_pack/                     Day 4 — supplied requirements/rules/fixtures, used as-is
      contract_requirements.md
      semantic_rules.md
      fixtures/
        structured_output_cases.json      12 broken-output cases (D04-01..D04-12)
        existing_caller_v1.json           frozen pre-`warning` caller snapshot
    index/                         build output (gitignored) - python -m aico.retrieval.ingest
    vectors/                       build output (gitignored) - python -m aico.retrieval.embed
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
  tests/
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
```
