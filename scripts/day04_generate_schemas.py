"""
Day 4 Task 1 — JSON Schema generation.

Run: python scripts/day04_generate_schemas.py
(needs PYTHONPATH=src - see README Setup)

Generates JSON Schema straight from the Pydantic source models in
`src/aico/contracts/models.py` (`model_json_schema()` - Pydantic's own
generator, not a hand-written schema) and writes it to the committed
files under `contracts/schema/`:

    contracts/schema/cited_answer.v1.schema.json
    contracts/schema/response_envelope.v1.schema.json

Committing the *generated* output (rather than hand-maintaining a
separate schema file) is the point: re-running this script after any
change to `models.py` regenerates schema that cannot drift out of sync
with the source model it describes. If a change to `models.py` changes
either output file, that diff is the breaking/non-breaking signal Task 6
asks for - review it before committing.
"""
from __future__ import annotations

import json
from pathlib import Path

from aico.contracts.models import CitedAnswer, ResponseEnvelope

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = REPO_ROOT / "contracts" / "schema"

TARGETS = {
    "cited_answer.v1.schema.json": CitedAnswer,
    "response_envelope.v1.schema.json": ResponseEnvelope,
}


def generate() -> list[Path]:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, model in TARGETS.items():
        schema = model.model_json_schema()
        path = SCHEMA_DIR / filename
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    for path in generate():
        print(f"wrote {path.relative_to(REPO_ROOT)}")
