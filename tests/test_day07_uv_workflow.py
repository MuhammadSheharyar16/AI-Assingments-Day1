"""
Day 7 Task 14 — the two checklist rows no other Day 7 test file owns:
"uv workflow" (install/test/eval actually work *through* `uv run`, not
just as direct Python calls the rest of this suite makes) and
"Day 1-6 regression" (the pre-existing Day 1-6 test files are still
present and still pass, checked as its own named thing, not inferred from
"the full suite's total didn't shrink"). Every other Task 14 checklist row
already has a home in an existing Day 7 test file - see evals/README.md's
Task 14 section for the full, cited coverage map.

Every test here shells out to a real `uv ...` subprocess - the one place
in the whole suite that does. That's deliberate: it's the only way to
prove the *documented* commands (`uv sync --frozen`, `uv run pytest -q`,
`uv run python -m aico.evals.day07`) work when actually invoked the way a
developer or CI runs them, as opposed to calling the same underlying
Python functions in-process the way every other test in this repository
does (and should keep doing - that's what makes the rest of the suite
fast). Bounded with a generous timeout each, since a `uv` subprocess
invocation is slower than an in-process call by nature.
"""
from __future__ import annotations

import pathlib
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"


def _run_uv(*args: str, timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(["uv", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout)


# ── uv workflow: install ──────────────────────────────────────────────

def test_uv_lock_is_in_sync_with_pyproject_toml():
    """`uv.lock` must actually reflect `pyproject.toml` - a stale lockfile
    would mean `uv sync --frozen` (this project's and CI's own first step)
    silently installs a dependency set nobody reviewed against the current
    `pyproject.toml`. `uv lock --check` resolves against the committed
    lockfile without writing to it."""
    result = _run_uv("lock", "--check")
    assert result.returncode == 0, result.stdout + result.stderr


def test_uv_sync_frozen_succeeds():
    """The literal documented/CI command - installs exactly what
    `uv.lock` records, never re-resolving (working rule: "CI and Docker
    use the committed lockfile")."""
    result = _run_uv("sync", "--frozen")
    assert result.returncode == 0, result.stdout + result.stderr


def test_uv_run_python_can_import_the_installed_package():
    """Proves the package `uv sync` installs is genuinely importable
    through the uv-managed environment - not just files sitting on disk,
    but an environment `uv run` actually resolves `import aico` against."""
    result = _run_uv("run", "python", "-c", "import aico; print('import-ok')")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "import-ok" in result.stdout


# ── uv workflow: test ──────────────────────────────────────────────────

def test_uv_run_pytest_can_collect_the_full_suite():
    """Collection only - deliberately never a recursive full *run* of the
    suite from inside one of its own tests. This proves the suite is
    discoverable and importable through the literal `uv run pytest`
    command without the cost (and recursion risk) of re-executing all
    ~700 tests from within one of them; `test_day1_to_6_tests_still_pass`
    below is the one place this file *does* pay that cost, scoped to a
    named subset for a specific reason."""
    result = _run_uv("run", "pytest", "--collect-only", "-q", timeout=60)
    # returncode alone is the real signal (pytest exits non-zero on a
    # collection error); a substring scan for "error" anywhere in stdout
    # is unreliable on its own - plenty of real, passing test names in
    # this suite legitimately contain "error" (e.g. a
    # ..._validation_error test), so that would flag a healthy collection
    # as if it had failed.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "collected" in result.stdout.lower(), result.stdout + result.stderr


# ── uv workflow: eval ────────────────────────────────────────────────

def test_uv_run_eval_harness_succeeds(tmp_path):
    """The literal documented/CI command
    (`uv run python -m aico.evals.day07`), run as a real subprocess
    against an isolated --artifacts-dir (never the committed
    artifacts/day07/) - proves the regression gate is genuinely invocable
    exactly as documented through `uv run`, not only as a direct
    `day07.main(argv)` call the way every other Day 7 test exercises it."""
    result = _run_uv(
        "run", "python", "-m", "aico.evals.day07",
        "--artifacts-dir", str(tmp_path / "artifacts"),
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "artifacts" / "evaluation_report.json").exists()
    assert (tmp_path / "artifacts" / "evaluation_report.md").exists()
    assert (tmp_path / "artifacts" / "failure_classification.md").exists()


# ── Day 1-6 regression: the existing suite is still here, still green ────

# The exact Day 1-6 file list README.md documents test-file-by-test-file -
# kept here as its own named list (not re-derived from "everything that
# isn't a test_day07_*.py file") so a future rename shows up as a missing
# name below, not a silent scope change.
DAY_1_TO_6_TEST_FILES: tuple[str, ...] = (
    "test_chunker.py", "test_bm25.py", "test_ingest.py", "test_day01_eval.py",
    "test_embedding_provider.py", "test_vector_index.py", "test_embed.py", "test_hybrid.py", "test_search.py",
    "test_day2_regression.py",
    "test_model_gateway.py", "test_model_gateway_retry.py", "test_model_gateway_routing.py",
    "test_model_gateway_logging.py", "test_foundry_adapter_identity.py", "test_foundry_adapter_normalization.py",
    "test_day04_contracts.py", "test_day04_semantic_validation.py", "test_day04_repair.py",
    "test_day04_broken_output_suite.py", "test_day04_compatibility.py",
    "test_day05_grounding.py", "test_day05_citations.py", "test_day05_insufficient_evidence.py",
    "test_day05_normalization.py", "test_day05_input_policy.py", "test_day05_poisoned_documents.py",
    "test_day05_answer_support.py",
    "test_day06_api.py", "test_day06_identity.py", "test_day06_correlation.py", "test_day06_errors.py",
    "test_day06_cancellation.py", "test_day06_health.py", "test_day06_observability.py",
    "test_day06_dependency_injection.py",
)


def test_every_day1_to_6_test_file_is_still_present():
    """If a future change silently deleted or renamed one of these, this
    fails immediately naming which file went missing - "716 passed"
    quietly becoming a smaller, equally-green-looking number is exactly
    the failure mode a headline pass count can't catch on its own."""
    missing = [name for name in DAY_1_TO_6_TEST_FILES if not (TESTS_DIR / name).exists()]
    assert not missing, f"Day 1-6 test file(s) missing: {missing}"


@pytest.mark.timeout(180)
def test_day1_to_6_tests_still_pass():
    """Runs exactly the Day 1-6 test files - not the whole suite (that is
    what the outer `uv run pytest -q` invocation already does every time
    this file itself runs) - as their own isolated subprocess, so "Day 1-6
    regression" is a check with its own name and its own pass/fail, not an
    inference from the full suite's total happening to be unchanged."""
    args = ["run", "pytest", "-q"] + [str(TESTS_DIR / name) for name in DAY_1_TO_6_TEST_FILES]
    result = _run_uv(*args, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
