# AICO Day 1 — Document Chunking and Lexical Retrieval

A from-scratch chunker and BM25 lexical search baseline, built against
Meridian Procurement's synthetic supplier policy documents. No
embeddings, vector store, LLM or retrieval framework is used — this is a
measurable baseline that Day 2's semantic/hybrid retrieval will be
compared against.

All data in `data/` is synthetic. No production, MOD, customer, personal
or classified data is used.

## Setup

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set PYTHONPATH=src
```

(macOS/Linux/git-bash: `python3 -m venv .venv`, `source .venv/bin/activate`,
`export PYTHONPATH=src`)

`PYTHONPATH=src` is only needed to invoke the `aico.*` modules directly with
`python -m`. `pytest` doesn't need it — `pytest.ini` sets `pythonpath = src`
for you.

## Run the tests

```
pytest -q
```

35 tests pass, across four files (consolidated from individual
single-assertion tests into one test per behaviour where several cases were
exercising the same code path — e.g. all five invalid-chunker-config cases
run as one test with a loop over bad inputs, rather than five near-identical
functions):
- `tests/test_chunker.py` (11) — offset reconstruction, overlap, unicode
  survival, empty input, invalid configuration (all 5 bad-input cases in
  one test), determinism
- `tests/test_bm25.py` (6) — tokenisation, ranking behaviour, IDF
  weighting, tie-breaking
- `tests/test_ingest.py` (4) — end-to-end field completeness + offsets
  against the real source files (one test), tokens/overlap actually
  change output, invalid config rejected, determinism across runs
- `tests/test_day01_eval.py` (14) — anchor normalisation/matching,
  hit/MRR scoring, category breakdown, no-match handling, phrase-support
  gate for no_match abstention (requiring every query phrase, not just
  one), the dominant-scoring-term vocabulary check, winning-config /
  worst-query selection, and that `render_report()` produces every
  required report section

## Task 1 — Ingest and chunk

```
python -m aico.retrieval.ingest --input data/documents --out data/index --tokens 300 --overlap 50
```

Reads every `.md` file in `--input`, splits each into overlapping,
offset-exact chunks, and writes a single `index.json` (manifest + chunk
records) to `--out`. Token size and overlap are required arguments — no
hardcoded default. All four flags (`--input`, `--out`, `--tokens`,
`--overlap`) are mandatory.

## Task 2 — Lexical retrieval

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
score is 0.

## Task 3 — Measure and report

```
python -m aico.evals.day01 --queries data/evals/day01_queries.json --index data/index
```

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

A no_match top score alone isn't trusted as evidence of a real match: the
scorer also requires the top chunk to contain **every one** of the query's
adjacent content-word pairs verbatim, not just one of them (a
phrase-support gate — see `artifacts/day01/retrieval_report.md` for why
"every", not "any", was needed), and not just an absolute score floor.
`NO_MATCH_SCORE_FLOOR = 4.0` in `src/aico/evals/day01.py` is still a
sufficient condition for abstention on its own, it's just no longer the
only one.

### Last verified run (2026-08-26)
```
config 200_40 (35 chunks): Hit@1=0.62  Hit@5=0.88  MRR=0.729
  exact_term:    Hit@1=0.75 Hit@5=1.00 MRR=0.875
  synonym_poor:  Hit@1=0.50 Hit@5=0.50 MRR=0.500
  multi_chunk:   Hit@1=0.50 Hit@5=1.00 MRR=0.667
  Q09 (no_match): top_score=10.801 phrase_support=False -> ok (abstained)
  Q10 (no_match): top_score=7.558  phrase_support=False -> ok (abstained)

config 400_80 (16 chunks): Hit@1=0.50  Hit@5=1.00  MRR=0.692
  exact_term:    Hit@1=0.75 Hit@5=1.00 MRR=0.875
  synonym_poor:  Hit@1=0.50 Hit@5=1.00 MRR=0.600
  multi_chunk:   Hit@1=0.00 Hit@5=1.00 MRR=0.417
  Q09 (no_match): top_score=12.159 phrase_support=False -> ok (abstained)
  Q10 (no_match): top_score=5.928  phrase_support=False -> ok (abstained)

pytest -q: 35 passed
```

## Evaluation queries

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

## Key design decisions

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

## Folder structure

```
aico-day01-assignment/
  README.md                       this file
  requirements.txt
  pytest.ini                       points pytest at src/ (src-layout, not pip-installed)
  src/aico/
    retrieval/
      chunker.py                   offset-exact chunking
      ingest.py                    CLI: documents -> index.json
      bm25.py                      from-scratch BM25 ranking
      search.py                    CLI: query -> ranked chunks
    evals/
      day01.py                     CLI: Hit@1 / Hit@5 / MRR scorer
  data/
    documents/                     DOC-001 .. DOC-005 (synthetic)
    evals/day01_queries.json       10 labelled queries + scoring rules
    index/                         build output (not yet git-ignored, see Known gaps)
  artifacts/day01/
    chunks_200_40.json             committed chunk set, config A
    chunks_400_80.json             committed chunk set, config B
    metrics.json                   full metrics, both configs
    retrieval_report.md            auto-generated by day01.py from the same eval pass as metrics.json
  tests/
    test_chunker.py                15 tests
    test_bm25.py                   6 tests
    test_ingest.py                 5 tests
    test_day01_eval.py             11 tests
```

