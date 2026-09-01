"""
Day 4 — typed contract-layer failures.

Introduced for Task 2 (contract/schema validation), and shared by every
later stage so the whole pipeline speaks one typed-failure shape. `stage`
is what lets a caller distinguish a parse/contract/schema failure from a
semantic failure (Task 3) with one `if result.stage == ...`, instead of
catching different exception types per stage:
    "parse"    - Task 2, raw text was not (or did not decode to) a JSON object
    "contract" - Task 2, parsed JSON did not match the Pydantic model
    "semantic" - Task 3, a well-typed contract broke an application rule
    "repair"   - Task 4, the one bounded repair *call itself* failed
                  (e.g. the Model Gateway raised) - distinct from a
                  repaired response that still fails validation, which
                  simply comes back with stage "contract"/"semantic"
                  again, from the same second validation pass as the
                  first attempt.

`ValidationFailure` is a frozen, JSON-safe *value*, not an exception - the
validator (`validator.py`) returns it as an ordinary function result
(`Model | ValidationFailure`), it never raises across the contract-layer
boundary. It deliberately carries only sanitized fields (stage, category,
an optional field path, a short safe message) and never the raw model
response text: the message names what was wrong ("missing required
field", "value out of range") without echoing model output back into an
object that may end up in an ordinary log line - see the working rule "do
not log full invalid model responses by default."

Every contract-stage category below maps 1:1 onto a required-rejection
case from the assignment brief / `data/day04_pack/contract_requirements.md`:
    malformed_json  -> "malformed JSON"
    missing_field   -> "missing required field"
    extra_field     -> "extra field"
    wrong_type      -> "wrong field type"
    invalid_enum    -> "invalid enum"
    out_of_range    -> "out-of-range constrained value"
    other_contract  -> anything else Pydantic flags that doesn't map onto
                        one of the six named categories above (kept so
                        `validate_contract()` never has to drop
                        information rather than type it)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Stage = Literal["parse", "contract", "semantic", "repair"]

CONTRACT_CATEGORIES = (
    "malformed_json",
    "missing_field",
    "extra_field",
    "wrong_type",
    "invalid_enum",
    "out_of_range",
    "other_contract",
)


@dataclass(frozen=True)
class ValidationFailure:
    """A typed, safe-to-log failure from any pipeline stage. `stage` is
    what a caller switches on to distinguish a parse/contract/schema
    failure (Task 2) from a semantic failure (Task 3, `stage="semantic"`).
    `field_path` is a dotted Pydantic loc (e.g. `"citations.0.chunk_id"`),
    `None` when the failure isn't about one specific field."""

    stage: Stage
    category: str
    message: str
    field_path: str | None = None

    def __str__(self) -> str:  # safe by construction - see module docstring
        where = f" at {self.field_path}" if self.field_path else ""
        return f"[{self.stage}:{self.category}]{where} {self.message}"
