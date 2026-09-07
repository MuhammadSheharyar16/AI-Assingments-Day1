# Day 7 evaluation resources

No Day 7 resource pack was supplied — this directory, starting with
`golden_v1.json`, is the developer-created evaluation resource the brief
requires ("Important resource rule"). This file documents and justifies the
design decisions behind it.

## `golden_v1.json` — Task 1: the golden dataset

**32 cases** (minimum required: 25) over the existing five-document
synthetic procurement corpus (`data/documents/DOC-001`..`DOC-005`, unchanged
from Days 1–6 — no new documents were added so retrieval scores stay
comparable to the Day 1/2 baselines).

### Why 32, not exactly 25

25 is a floor, not a target. Splitting it evenly across all six required
categories only gives ~4 cases per category, too thin to catch a pattern
regression in any one category reliably. 32 cases lets `answerable` (the
largest, most heterogeneous category — pulled from all five documents, no
two testing the same fact) get 8 cases while every other category still
gets at least 4, and keeps every split (train/development/holdout) non-empty
for every category.

### Category coverage

| category | count | what it tests |
|---|---|---|
| `answerable` | 8 | single fact, single chunk, exact-term style — one new fact per document, distinct from every day01/day02 anchor already in use |
| `ambiguous` | 5 | question omits a qualifier the corpus needs to answer safely (which supplier, which category, which notice type, whether a shutdown applies, which role's rate) — correct behavior is to ask, not guess |
| `multi_chunk` | 5 | requires combining two chunks (cross-document twice, same-document-different-section three times) |
| `synonym_heavy` | 5 | colloquial phrasing with **no literal word overlap** with the target sentence — tests semantic, not lexical, retrieval |
| `unanswerable` | 4 | no evidence anywhere in the corpus — tests that the system says so instead of inventing a plausible-sounding number |
| `adversarial` | 5 | one case per required Day 5 attack category still open at this layer (instruction override / system-prompt extraction, role escalation, poisoned retrieved text, citation forgery, tool coercion) — zero tolerance |

Deliberately **not** 25 near-duplicates of one easy pattern (working rule):
every `answerable` case comes from a different section of a different
document, and no two `synonym_heavy`/`multi_chunk` cases share a target
fact.

### Fields per case

`case_id`, `category`, `split`, `question`, `expected_sources`,
`answerability`, `critical_facts`, `prohibited_claims` — the required
conceptual fields, plus two documented additions:

- `notes` — one line of design rationale per case (which document
  section, why this category, what the trap is). Not required by the
  brief; kept because "must create and justify" applies per-case, not
  only at the dataset level.
- `attack_category` — present only on `adversarial` cases; names which
  Day 5 attack-fixture category (`instruction_override`/
  `system_prompt_extraction`, `role_escalation`,
  `poisoned_document_injection`, `citation_forgery`, `tool_coercion`)
  the case exercises at the evaluation-harness layer, so Task 6's
  failure-classification report can cross-reference it.

`expected_sources` is `[{"doc_id": ..., "anchor": ...}]`, not a raw
`chunk_id` — chunk IDs in this repo are content-hashed
(`sha256(source_file:char_start:char_end)`, see `search.py`/README "Day
1 design decisions"), so a hand-authored file cannot commit to one
without first running ingestion. Anchor-substring matching against
`doc_id` is the same approach `data/evals/day01_queries.json` and
`data/evals/day02_queries.json` already use, and Task 3's
`matching_rule` field (copied verbatim into `golden_v1.json`) reuses
their exact normalisation rule, per the working rule "reuse the already
approved project definitions for Hit@K and MRR". Every anchor in this
file was verified as a real substring of its named document
(case-insensitive, punctuation-normalised) before being committed —
see `labeling_policy`.

`answerability` takes four values, not two:
`answerable | ambiguous | unanswerable | must_refuse`. `category` and
`answerability` are deliberately separate fields: `category` says what
*kind* of retrieval/reasoning challenge the case poses, `answerability`
says what the *correct output shape* is. This matters because
`multi_chunk` and `synonym_heavy` cases are still ultimately answerable
(their `answerability` is `answerable`) — they are harder to retrieve
for, not harder to answer once retrieved. `ambiguous` cases carry empty
`critical_facts`: no single fact should be asserted confidently when the
question itself is underspecified, so there is nothing to score as
"present" — refusal/clarification behavior is what the evaluator checks
instead. `adversarial` cases use `must_refuse` and also carry empty
`expected_sources`/`critical_facts`: the correct behavior is a policy
block before retrieval ever runs (mirrored from Day 5's
`attack_results.md`, where 6 of 9 fixtures never reach the Model
Gateway), so grading retrieval quality on them would be meaningless.

### Split assignment (Task 2 preview — full split documentation lives with Task 2)

Every category has at least one `train`, one `development`, and one
`holdout` case, target ratio ~60/20/20:

| split | count |
|---|---|
| `train` | 19 |
| `development` | 6 |
| `holdout` | 7 |

Holdout deliberately includes `adversarial` (GC-032) and `unanswerable`
(GC-027) cases, not just easy `answerable` ones — the working rule's
zero-tolerance safety gate has to be proven on data the system was never
tuned against, not just on cases the developer already knows it passes.

### Labeling discipline

Every label in this file — category, split, expected source, critical
fact, prohibited claim — was written from the source documents *before*
any candidate system output was generated for this dataset version (see
`labeling_policy` in the JSON). None of it was adjusted after seeing a
run, and none of it was tuned against holdout results, per the working
rule.

## Task 2 — train / development / holdout

The loader is `src/aico/evals/dataset.py`; the split policy and its
enforcement live there, not just in this document:

- **Loading and structural validation** — `load_dataset()` (file) /
  `parse_dataset()` (dict, used directly by tests) parse `golden_v1.json`
  into typed `GoldenCase` records and raise `DatasetValidationError`
  listing *every* problem found — missing/invalid field, an unrecognised
  `category`/`split`/`answerability`, a duplicate `case_id`, fewer than
  `MIN_CASES` (25) cases, a required category with zero cases, an empty
  split, or a question that leaks across two splits. One exception with
  every problem, not one exception per re-run.
- **The holdout boundary is a function, not a convention** —
  `tunable_cases(dataset)` returns only `train` + `development` cases and
  can never return a `holdout` case by construction. Any later Day 7 code
  that tunes anything (prompts, retrieval settings, thresholds, labels)
  is expected to read the dataset only through this function, not
  `dataset.cases` directly. `holdout_cases(dataset)` is the
  measure-only counterpart. `tests/test_day07_holdout.py` proves the two
  partition the dataset with no overlap and that `tunable_cases()` never
  yields a holdout `case_id`.
- **Leakage beyond duplicate IDs** — `cross_split_question_leakage()`
  catches the case unique `case_id`s can't: the same underlying question
  assigned to two different splits under two different IDs, which would
  make "holdout" not actually unseen data. Checked against the real
  dataset (currently clean) and, separately, proven to actually detect a
  synthetic leak (`tests/test_day07_holdout.py`).
- **Split counts** (from `golden_v1.json`'s own `counts` block, guarded
  against drifting from the real case list by
  `test_declared_counts_match_computed_counts`):

  | split | count | tuning allowed | measured |
  |---|---|---|---|
  | `train` | 19 | yes | yes |
  | `development` | 6 | yes | yes |
  | `holdout` | 7 | **no** | yes, reported separately |

### Tests

- `tests/test_day07_dataset.py` — schema tests against the real
  `golden_v1.json` (min case count, category coverage, unique IDs, valid
  split/answerability values, no leakage, declared counts match
  computed) plus loader-rejection tests against hand-built dicts, one
  failure mode at a time, proving `DatasetValidationError` actually fires
  for each documented problem rather than only passing by construction.
- `tests/test_day07_holdout.py` — the holdout-separation guarantees
  above: `tunable_cases`/`holdout_cases` partition the dataset with no
  overlap, holdout covers every required category (including
  `adversarial` and `unanswerable`), and the leakage detector is proven
  against a synthetic dataset built specifically to leak (not just shown
  clean on data that was never going to trigger it).

Run just these: `uv run pytest -q tests/test_day07_dataset.py
tests/test_day07_holdout.py`. Quick manual sanity check outside pytest:
`uv run python -m aico.evals.dataset` (loads `evals/golden_v1.json`,
prints split/category counts, exits non-zero with every problem listed if
validation fails).
