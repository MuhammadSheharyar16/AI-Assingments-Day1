"""
Day 7 Task 2 — golden dataset loader and train/development/holdout split.

Loads `evals/golden_v1.json` (Task 1's developer-authored resource — no
resource pack is supplied for Day 7, see `evals/README.md`) into typed,
validated `GoldenCase` records, and gives every later Day 7 task
(deterministic eval, model-based eval, thresholds/baseline, regression
gate) one place to ask "which cases am I allowed to look at" instead of
each reimplementing the split rule.

Two responsibilities live here, and only here:

1. **Structural validation** (`parse_dataset`/`load_dataset`) — every
   required field is present and well-typed, `category`/`split`/
   `answerability` are one of the documented enum values, case IDs are
   unique, the minimum case count is met, and all six required
   categories are represented. A broken dataset file fails loudly with
   every problem found at once (`DatasetValidationError.problems`), not
   one exception per re-run.
2. **The holdout boundary** (`tunable_cases`/`holdout_cases`) — the
   *only* sanctioned way later code should read this dataset when tuning
   anything (prompts, retrieval settings, thresholds, labels) is
   `tunable_cases()`, which structurally excludes every `holdout` case.
   `holdout_cases()` exists for measuring and reporting only. This
   doesn't stop a determined caller from reading `dataset.cases`
   directly, but it does mean any future tuning code that imports
   `tunable_cases` instead of `dataset.cases` gets the exclusion for
   free, and `tests/test_day07_holdout.py` proves the boundary is real
   (no holdout `case_id` ever appears in `tunable_cases()`'s output).

Cross-split question leakage (`cross_split_question_leakage`) is a
second, independent guard: unique `case_id`s alone don't prove holdout is
actually unseen data — two cases with the same underlying question in
different splits would still leak. Detecting that requires no schema
change to the file, so it's a standalone check, not baked into every
`load_dataset` call.
"""
from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass, field

REQUIRED_CATEGORIES: frozenset[str] = frozenset({
    "answerable",
    "ambiguous",
    "multi_chunk",
    "synonym_heavy",
    "unanswerable",
    "adversarial",
})

VALID_SPLITS: frozenset[str] = frozenset({"train", "development", "holdout"})

# Not the same enum as `category`: `category` names the retrieval/reasoning
# challenge, `answerability` names the correct output shape. See
# evals/README.md "Fields per case" for why they're kept separate.
VALID_ANSWERABILITY: frozenset[str] = frozenset({
    "answerable", "ambiguous", "unanswerable", "must_refuse",
})

MIN_CASES = 25

REQUIRED_CASE_FIELDS = (
    "case_id", "category", "split", "question", "expected_sources",
    "answerability", "critical_facts", "prohibited_claims",
)

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")


class DatasetValidationError(ValueError):
    """Raised by `parse_dataset`/`load_dataset` when `golden_v1.json` fails
    validation. Carries every problem found, not just the first, so a
    broken dataset can be fixed in one pass — this is meant to run inside
    CI (Task 9/13), where "run it again to find the next error" is
    exactly the loop a regression gate should not impose."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        summary = "\n".join(f"- {p}" for p in self.problems)
        super().__init__(f"{len(self.problems)} golden dataset problem(s):\n{summary}")


@dataclass(frozen=True)
class ExpectedSource:
    doc_id: str
    anchor: str


@dataclass(frozen=True)
class GoldenCase:
    case_id: str
    category: str
    split: str
    question: str
    expected_sources: tuple[ExpectedSource, ...]
    answerability: str
    critical_facts: tuple[str, ...]
    prohibited_claims: tuple[str, ...]
    notes: str = ""
    attack_category: str | None = None


@dataclass(frozen=True)
class GoldenDataset:
    version: str
    dataset_id: str
    corpus: tuple[str, ...]
    matching_rule: str
    cases: tuple[GoldenCase, ...]
    # Full parsed JSON, kept for later tasks that need metadata this loader
    # doesn't model explicitly (category_definitions, split_definitions,
    # labeling_policy, ...) without re-reading the file from disk.
    raw: dict = field(repr=False)

    def __len__(self) -> int:
        return len(self.cases)


def _normalize_question(text: str) -> str:
    """Same normalisation family as the anchor-matching rule (see
    `matching_rule` in golden_v1.json / day01.normalise): lowercase, strip
    punctuation, collapse whitespace. Used only for the leakage check
    below — never for retrieval scoring."""
    text = text.lower()
    text = _NON_ALNUM_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _parse_case(case_dict: dict, index: int, problems: list[str]) -> GoldenCase | None:
    missing = [f for f in REQUIRED_CASE_FIELDS if f not in case_dict]
    if missing:
        label = case_dict.get("case_id", f"index {index}")
        problems.append(f"{label}: missing required field(s) {missing}")
        return None

    case_id = case_dict["case_id"]
    if not isinstance(case_id, str) or not case_id.strip():
        problems.append(f"case at index {index}: case_id must be a non-empty string, got {case_id!r}")
        return None

    ok = True

    category = case_dict["category"]
    if category not in REQUIRED_CATEGORIES:
        problems.append(f"{case_id}: category {category!r} is not one of {sorted(REQUIRED_CATEGORIES)}")
        ok = False

    split = case_dict["split"]
    if split not in VALID_SPLITS:
        problems.append(f"{case_id}: split {split!r} is not one of {sorted(VALID_SPLITS)}")
        ok = False

    answerability = case_dict["answerability"]
    if answerability not in VALID_ANSWERABILITY:
        problems.append(f"{case_id}: answerability {answerability!r} is not one of {sorted(VALID_ANSWERABILITY)}")
        ok = False

    question = case_dict["question"]
    if not isinstance(question, str) or not question.strip():
        problems.append(f"{case_id}: question must be a non-empty string")
        ok = False

    expected_sources_raw = case_dict["expected_sources"]
    expected_sources: list[ExpectedSource] = []
    if not isinstance(expected_sources_raw, list):
        problems.append(f"{case_id}: expected_sources must be a list")
        ok = False
    else:
        for i, src in enumerate(expected_sources_raw):
            if not isinstance(src, dict) or "doc_id" not in src or "anchor" not in src:
                problems.append(f"{case_id}: expected_sources[{i}] must be an object with doc_id and anchor")
                ok = False
                continue
            doc_id, anchor = src["doc_id"], src["anchor"]
            if not str(doc_id).strip() or not str(anchor).strip():
                problems.append(f"{case_id}: expected_sources[{i}] has an empty doc_id or anchor")
                ok = False
                continue
            expected_sources.append(ExpectedSource(doc_id=doc_id, anchor=anchor))

    critical_facts = case_dict["critical_facts"]
    if not isinstance(critical_facts, list):
        problems.append(f"{case_id}: critical_facts must be a list")
        ok = False
        critical_facts = []

    prohibited_claims = case_dict["prohibited_claims"]
    if not isinstance(prohibited_claims, list):
        problems.append(f"{case_id}: prohibited_claims must be a list")
        ok = False
        prohibited_claims = []

    if not ok:
        return None

    return GoldenCase(
        case_id=case_id,
        category=category,
        split=split,
        question=question,
        expected_sources=tuple(expected_sources),
        answerability=answerability,
        critical_facts=tuple(critical_facts),
        prohibited_claims=tuple(prohibited_claims),
        notes=case_dict.get("notes", ""),
        attack_category=case_dict.get("attack_category"),
    )


def duplicate_case_ids(cases: tuple[GoldenCase, ...]) -> list[str]:
    """case_ids that appear more than once, sorted for a stable error message."""
    seen: set[str] = set()
    dupes: set[str] = set()
    for c in cases:
        if c.case_id in seen:
            dupes.add(c.case_id)
        seen.add(c.case_id)
    return sorted(dupes)


def missing_categories(cases: tuple[GoldenCase, ...]) -> list[str]:
    """Required categories with zero cases, sorted."""
    present = {c.category for c in cases}
    return sorted(REQUIRED_CATEGORIES - present)


def cross_split_question_leakage(cases: tuple[GoldenCase, ...]) -> list[dict]:
    """Detect the same (normalised) question text assigned to more than one
    split. Unique case_ids alone don't prove holdout is genuinely unseen
    data — two differently-ID'd cases sharing the same underlying question
    across, say, train and holdout would still leak the holdout content
    into what a developer sees while iterating on train.

    Returns one entry per leaking question:
        {"question_norm": "...", "splits": {"train": ["GC-001"], "holdout": ["GC-099"]}}
    Empty list means no leakage.
    """
    by_question: dict[str, dict[str, list[str]]] = {}
    for c in cases:
        key = _normalize_question(c.question)
        by_question.setdefault(key, {}).setdefault(c.split, []).append(c.case_id)

    leaks = []
    for question_norm, by_split_ids in by_question.items():
        if len(by_split_ids) > 1:
            leaks.append({"question_norm": question_norm, "splits": by_split_ids})
    return leaks


def _cross_case_problems(dataset: GoldenDataset) -> list[str]:
    problems: list[str] = []

    if len(dataset.cases) < MIN_CASES:
        problems.append(f"dataset has {len(dataset.cases)} case(s), fewer than the required minimum {MIN_CASES}")

    dupes = duplicate_case_ids(dataset.cases)
    if dupes:
        problems.append(f"duplicate case_id(s): {dupes}")

    missing = missing_categories(dataset.cases)
    if missing:
        problems.append(f"missing required categor(y/ies): {missing}")

    for split in VALID_SPLITS:
        if not any(c.split == split for c in dataset.cases):
            problems.append(f"split {split!r} has zero cases")

    leaks = cross_split_question_leakage(dataset.cases)
    if leaks:
        for leak in leaks:
            problems.append(
                f"question leaks across splits {sorted(leak['splits'])}: {leak['splits']}"
            )

    return problems


def parse_dataset(raw: dict) -> GoldenDataset:
    """Validate an already-parsed golden dataset dict. Raises
    `DatasetValidationError` with every problem found. Split out from
    `load_dataset` so tests can exercise validation against hand-built
    dicts without touching the filesystem."""
    problems: list[str] = []

    cases_raw = raw.get("cases")
    if not isinstance(cases_raw, list):
        raise DatasetValidationError(["top-level 'cases' field is missing or not a list"])

    cases: list[GoldenCase] = []
    for i, case_dict in enumerate(cases_raw):
        parsed = _parse_case(case_dict, i, problems)
        if parsed is not None:
            cases.append(parsed)

    dataset = GoldenDataset(
        version=raw.get("version", ""),
        dataset_id=raw.get("dataset_id", ""),
        corpus=tuple(raw.get("corpus", [])),
        matching_rule=raw.get("matching_rule", ""),
        cases=tuple(cases),
        raw=raw,
    )

    problems.extend(_cross_case_problems(dataset))
    if problems:
        raise DatasetValidationError(problems)
    return dataset


def load_dataset(path: pathlib.Path) -> GoldenDataset:
    """Read + fully validate a golden dataset JSON file (default:
    `evals/golden_v1.json`)."""
    raw = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    return parse_dataset(raw)


def split_counts(dataset: GoldenDataset) -> dict[str, int]:
    counts = {split: 0 for split in sorted(VALID_SPLITS)}
    for c in dataset.cases:
        counts[c.split] += 1
    return counts


def category_counts(dataset: GoldenDataset) -> dict[str, int]:
    counts = {cat: 0 for cat in sorted(REQUIRED_CATEGORIES)}
    for c in dataset.cases:
        counts[c.category] += 1
    return counts


def cases_in_split(dataset: GoldenDataset, split: str) -> tuple[GoldenCase, ...]:
    if split not in VALID_SPLITS:
        raise ValueError(f"unknown split {split!r}, expected one of {sorted(VALID_SPLITS)}")
    return tuple(c for c in dataset.cases if c.split == split)


def cases_in_category(dataset: GoldenDataset, category: str) -> tuple[GoldenCase, ...]:
    if category not in REQUIRED_CATEGORIES:
        raise ValueError(f"unknown category {category!r}, expected one of {sorted(REQUIRED_CATEGORIES)}")
    return tuple(c for c in dataset.cases if c.category == category)


def tunable_cases(dataset: GoldenDataset) -> tuple[GoldenCase, ...]:
    """train + development only. This is the one function any future
    prompt/retrieval/threshold/label-tuning code should read the dataset
    through — by construction it can never return a holdout case, which
    is the working rule ("do not tune ... against holdout results") made
    structural rather than just documented. See `holdout_cases` for the
    measure-but-never-tune counterpart."""
    return tuple(c for c in dataset.cases if c.split != "holdout")


def holdout_cases(dataset: GoldenDataset) -> tuple[GoldenCase, ...]:
    """holdout only — measured and reported (Task 11 shows it as its own
    section), never read by anything that adjusts prompts, retrieval
    settings, thresholds, labels, expected sources, answerability,
    critical facts, or prohibited claims."""
    return cases_in_split(dataset, "holdout")


def group_by_split(dataset: GoldenDataset) -> dict[str, tuple[GoldenCase, ...]]:
    """All three splits, for a report that must show holdout separately
    (working rule) without hand-filtering three times."""
    return {split: cases_in_split(dataset, split) for split in sorted(VALID_SPLITS)}


DEFAULT_DATASET_PATH = pathlib.Path("evals/golden_v1.json")


def main() -> int:
    """`python -m aico.evals.dataset` — quick manual sanity check: load
    evals/golden_v1.json, print split/category counts, exit non-zero with
    every problem listed if validation fails. Not the Day 7 regression
    gate itself (Task 9's `aico.evals.day07` is) — this is a narrower
    Task 2 tool scoped to dataset/split validation alone."""
    try:
        dataset = load_dataset(DEFAULT_DATASET_PATH)
    except DatasetValidationError as exc:
        print(f"FAILED — {DEFAULT_DATASET_PATH}:")
        for problem in exc.problems:
            print(f"  - {problem}")
        return 1

    print(f"OK - {len(dataset)} case(s) loaded from {DEFAULT_DATASET_PATH}")
    print(f"  by split:    {split_counts(dataset)}")
    print(f"  by category: {category_counts(dataset)}")
    print(f"  holdout:     {[c.case_id for c in holdout_cases(dataset)]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
