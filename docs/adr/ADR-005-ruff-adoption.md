# ADR-005 — Adopting ruff as the CI Lint Gate (Day 7 Task 13)

## Status

Accepted

## Context

Day 7 Task 13 requires `uv run ruff check .` as a real, blocking step in
CI (`uv sync --frozen` → `ruff check .` → `pytest -q` →
`python -m aico.evals.day07`, none of them `allow_failure`/
`continue-on-error`). No linter had ever run over this repository before -
six days of accepted, tested, working code had never been checked against
one. Turning `ruff check .` on for the first time as a CI gate meant
deciding, deliberately and up front, what it should actually enforce -
not just accepting whatever a default configuration happens to flag today
and letting that silently become the bar.

Running `ruff check .` with no `[tool.ruff]` configuration at all surfaced
138 findings on the first pass, across categories most of this codebase
had no prior opinion on (`pyupgrade`, `flake8-bugbear`,
`flake8-datetimez`, `flake8-blind-except`, `refurb`, `perflint`,
`pylint`-convention, `flake8-implicit-str-concat`, ruff's own rules) -
ruff's own default rule selection is itself version-dependent and not
something this repository had reviewed or agreed to. The working rule
this project has followed since Day 1 - explicit over implicit, reviewed
over assumed - applies here exactly as it does to a threshold value or a
baseline: **the rule selection itself needs to be an explicit, reviewed
choice** (`[tool.ruff]` in `pyproject.toml`), not whatever ruff ships by
default this month.

## Decision

**`select = ["E", "F", "I", "B", "UP"]`** (pycodestyle, pyflakes, import
sorting, `flake8-bugbear`, `pyupgrade`) - the common, widely-adopted
"catches a real bug or a genuine readability problem" set, not every rule
ruff can run. `line-length = 120`, `target-version = "py313"`.

Four rules are explicitly disabled, each with its own reason recorded
directly in `pyproject.toml` (not only here, so the reasoning can't drift
from the config a CI run actually reads):

- **`E501` (line-too-long)** - this codebase's docstrings and comments are
  deliberately long-form narrative prose, consistently, across all six
  prior days (every file in `src/aico/` and `tests/` follows this style).
  That is an already-reviewed, already-accepted stylistic choice, not
  something a linter adopted on Day 7 should force a mechanical rewrap of
  - and no formatter (`black`/`ruff format`) is configured to do that
  rewrapping automatically either. Enforcing it would have meant editing
  the *prose* of hundreds of comments across Days 1-6 as a side effect of
  adding a lint gate, which is out of proportion to what Task 13 asks for.
- **`B008` (function-call-in-default-argument)** - FastAPI's own
  `Depends(...)` pattern (Day 6, `src/aico/api/*.py`) is exactly this
  shape by design: the framework calls the function once per request via
  its own dependency-injection machinery, never once at import time the
  way a "mutable default argument" bug would. Flagging it here would be a
  false positive on idiomatic FastAPI, not a real defect.
- **`UP042` (replace-str-enum)** and **`UP047` (non-pep695-generic-function)**
  - both are Python version *modernizations* (`class X(str, Enum)` →
  `StrEnum`; `TypeVar`-based generics → PEP 695 `def foo[T]` syntax), not
  bug reports. Every enum in this codebase is deliberately `(str, Enum)`,
  already exercised by six days of accepted Pydantic (de)serialization
  and `isinstance`/equality tests - changing the base class is a real
  behavioral surface to touch, not a mechanical rename, and doing so as a
  side effect of turning on a linter (rather than as its own reviewed
  change) is exactly the kind of scope creep this ADR exists to rule out.

## What was actually fixed, and how

Of the 84 findings remaining under the selection above, 73 were ruff's
own safe, mechanical autofixes (`ruff check . --fix`, no `--unsafe-fixes`)
- unsorted imports, unused imports, redundant f-strings with no
placeholder, and a handful of `pyupgrade` syntax modernizations
(`Optional[X]` → `X | None`, deprecated `typing` imports, `datetime.timezone.utc`
→ `datetime.UTC`). **This surfaced one real regression from the autofix
itself**, caught by re-running the test suite immediately afterward (the
working rule this whole project follows: verify behaviorally, don't trust
a mechanical fix on faith) - see "A real bug this caught" below.

The remaining 11 were fixed by hand, one at a time, each on its own
merits:

- **4× `B905` (`zip()` without `strict=`)** - `src/aico/retrieval/embed.py`,
  `src/aico/retrieval/vector_index.py`, and two tests
  (`test_day2_regression.py`, `test_embedding_provider.py`). All four zip
  a chunk/text list against a same-length vector list by construction
  (batch order preservation is already a tested, documented guarantee -
  see the Day 2 README section). Added `strict=True` to all four - this
  is not just a lint fix, it's a genuine correctness improvement: without
  it, a future provider bug that returned a wrong-length vector list
  would silently misalign a chunk with the wrong embedding rather than
  raising loudly.
- **2× `E741`** (`src/aico/evals/day01.py`) - `l` renamed to `label` in
  two list-comprehension/lambda variables. Pure rename, zero behavior
  change.
- **2× `F841`** (unused variables in `scripts/day07_generate_stability_report.py`
  and `tests/test_day05_grounding.py`) - both were genuinely dead
  vestigial code (an unused `grader_plans` placeholder dict; an
  `answer_text` computed and never read because the line below it
  recomputes the same value independently). Deleted; nothing in either
  file referenced them.
- **1× `B007`** (`tests/test_day01_eval.py`) - unused loop variable `qid`
  renamed to `_qid`. Pure rename.
- **1× `B017`** (`tests/test_day07_groundedness.py`) - `pytest.raises(Exception)`
  narrowed to `pytest.raises(ValidationError)` (`pydantic.ValidationError`
  - the test's own comment already named it before this fix). A real test
  quality improvement: it now verifies the *specific* exception the test
  is actually about, not "some exception, anything."
- **1× `UP007`** (`src/aico/rag/answer_service.py`) - `Union[GroundedAnswer,
  ...]` converted to `GroundedAnswer | ...`. A type-alias assignment, not
  an annotation gated by `from __future__ import annotations` - verified
  the file still imports and the full suite still passes after the
  change.

## A real bug this caught

`ruff check . --fix` silently deleted three re-exported imports from
`scripts/day07_generate_failure_classification_report.py`
(`run_case as _run_case`, `well_behaved_response as _well_behaved_response`,
and `ScriptedGateway as _ScriptedGateway`) because, from ruff's
purely-local, per-file view, they were "unused" - nothing *inside that
file* read `_run_case` directly. What ruff couldn't see is that
`tests/test_day07_baseline_update.py` reached into this script via
`sys.path.insert` + `from day07_generate_failure_classification_report
import TOP_K, _run_case`, specifically to get at logic that had, by that
point, already been promoted into `aico.evals.day07` (Task 9) - the
re-export existed only to keep that one test working during the
refactor, and ruff's autofix broke it silently, with `uv run pytest -q`
catching it immediately as an `ImportError`.

The fix was not to restore the fragile re-export: `aico.evals.day07`
already exposes `run_case`/`DEFAULT_TOP_K`/`evaluate_all_cases` as its own
first-class public API (exactly why Task 9 promoted them there in the
first place). `test_normal_evaluation_never_writes_the_baseline_file` was
rewritten to import directly from `aico.evals.day07` - shorter, no
`sys.path` manipulation, and no longer dependent on a demonstration
script's internal re-exports at all. This is the working rule in
miniature: a tool's autofix is not proof of correctness on its own: the
full test suite re-run right after it is.

## Consequences

- `ruff check .` is a real, currently-passing, zero-suppressed-count gate
  - `# noqa` appears nowhere in this repository; every exception is a
    `pyproject.toml` rule-level ignore with its own written justification,
    not a per-line escape hatch.
- Adding `ruff` as a dev dependency (`uv add --dev ruff`) is the only
  dependency change this task made; no production dependency changed.
- The four rule-level ignores are reviewable in one place
  (`pyproject.toml`'s `[tool.ruff.lint]`) and each names exactly which
  file/pattern it protects, so a future contributor can tell at a glance
  whether a given ignore still applies to new code or needs revisiting.
