"""
Day 7 Task 8 — deliberate baseline update workflow.

Run (dry run — prints the computed metrics and a diff against the current
`evals/baseline_v1.json`, writes nothing):
    uv run python scripts/day07_update_baseline.py --reviewer "you@example.com" --notes "why"

Run for real (writes `evals/baseline_v1.json`):
    uv run python scripts/day07_update_baseline.py --reviewer "you@example.com" --notes "why" --confirm

A thin wrapper around Task 9's `python -m aico.evals.day07 --update-baseline`
— the exact command the brief's own example names — with a smaller,
friendlier flag set (no `--dataset`/`--index`/`--top-k` to think about for
the common case). Delegates to `aico.evals.day07.main()` directly rather
than a second implementation, so this script and the `--update-baseline`
flag can never drift apart from each other.

Deliberate, concretely (enforced by `aico.evals.day07`, not repeated here):
- `--reviewer` and `--notes` are required, not defaulted.
- Without `--confirm`, this is a dry run.
- The metrics locked in come from the same honest, non-per-case-scripted
  pipeline the gate itself uses (`aico.evals.day07.well_behaved_response`/
  `well_behaved_verdict`) — no live model, but not scripted per case
  toward a chosen number either.
- Never called from CI (Task 13's workflow must never invoke this script
  or the `--update-baseline` flag — see evals/README.md).
"""
from __future__ import annotations

import argparse
import sys

from aico.evals.day07 import main as day07_main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reviewer", required=True, help="who is approving this baseline (e.g. an email)")
    parser.add_argument("--notes", required=True, help="why this baseline is being set/updated now")
    parser.add_argument("--confirm", action="store_true", help="actually write evals/baseline_v1.json (default: dry run)")
    args = parser.parse_args()

    argv = ["--update-baseline", "--reviewer", args.reviewer, "--notes", args.notes]
    if args.confirm:
        argv.append("--confirm")
    return day07_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
