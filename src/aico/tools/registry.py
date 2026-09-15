"""
Day 13 Task 2 -- the Tool Registry service: loads the committed Tool
Registry document (Task 1's `ToolRegistryDocument`) and exposes it
read-only to the rest of the governed tool boundary.

Responsibilities (`tool_registry_requirements.md`; Day 13 Task 2):
    - load `tools/registry.v1.json`
    - validate once
    - exact-version lookup
    - active/disabled state
    - unknown tool/version failure
    - expose safe execution metadata
    - remain read-only at runtime

`ToolRegistry` is the only thing in `aico.tools` that ever reads
`tools/registry.v1.json` off disk -- the same "one place deserializes
this" convention `OntologyRegistry`/`PolicyRegistry` already establish for
their own committed files. Task 4's tool execution policy and Task 7's
controlled executor are built against this class's read-only surface,
never against a raw dict or the JSON file directly (Day 13 working rule:
"Every executable tool exists in the Tool Registry").

Read-only at runtime (Day 13 working rule: "Request body, model output and
session memory cannot self-assert tool permission" -- registering or
mutating a tool definition is the identical category of trust decision):
    - `ToolRegistry` defines no method that writes to the document it
      loaded -- there is no `register_tool()` / `set_status()` / etc.
    - `tools` returns a fresh, immutable `tuple` built from the loaded
      document, not a reference to a mutable list living inside it -- a
      caller cannot `.append()` its way around the missing mutator.
    - `ToolRegistryDocument` itself is an ordinary (not frozen) Pydantic
      model purely so Task 1's own tests can build/mutate throwaway
      documents in isolation; this module never mutates the one instance
      it loads, and nothing else under `aico.tools` is expected to call
      `ToolRegistryDocument.model_validate()` on the committed file
      directly.

"Exact-version lookup" / "unknown tool/version failure" -- `get_tool()`
below is the one place a `(tool_id, tool_version)` pair is ever resolved
to a `ToolDefinition`, and it fails closed with one of two distinct typed
errors (`errors.py`) rather than returning `None`/a synthesized default:
`ToolNotFoundError` when `tool_id` is not registered at all,
`ToolVersionNotFoundError` when it is registered but not at this exact
version (Day 13 working rule: "Tool version is explicit" -- there is no
latest/range resolution to fall back to).

"Expose safe execution metadata" -- `get_tool()` returns the full typed,
already-validated `ToolDefinition` (owner, risk level, side-effect/
idempotency flags, timeout, retry policy, required permissions, schemas):
"safe" here means *typed and validated*, the same reading `OntologyRegistry`
/ `PolicyRegistry` give their own governed records, not a hand-picked
subset of fields -- Task 4's policy and Task 7's executor both need the
complete definition to make their own decisions."""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from aico.tools.errors import ToolNotFoundError, ToolRegistryLoadError, ToolVersionNotFoundError
from aico.tools.models import ToolDefinition, ToolRegistryDocument, ToolStatus

# Relative to the process working directory, matching
# `OntologyRegistry.DEFAULT_REGISTRY_PATH`'s convention -- `uv run` always
# runs from the repository root.
DEFAULT_REGISTRY_PATH = Path("tools/registry.v1.json")


class ToolRegistry:
    """Read-only, typed access to one loaded Tool Registry document.

    Construct via `ToolRegistry.load(...)` in production code (loads and
    validates the committed file); tests may instead build a
    `ToolRegistryDocument` directly and pass it to the plain constructor --
    the constructor itself never touches disk, it only wraps an
    already-validated document and builds the exact-version lookup index
    over it.
    """

    def __init__(self, document: ToolRegistryDocument):
        self._document = document
        self._tools_by_id: dict[str, dict[str, ToolDefinition]] = {}
        for tool in document.tools:
            self._tools_by_id.setdefault(tool.tool_id, {})[tool.tool_version] = tool

    # ── Loading ──────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: str | Path = DEFAULT_REGISTRY_PATH) -> ToolRegistry:
        """Load and validate the committed registry file at `path`
        (default `tools/registry.v1.json` -- committed, read-only governed
        tool data; see `tools/README.md`). Raises `ToolRegistryLoadError`
        for anything wrong with the file itself: missing, unreadable, not
        valid JSON, or failing `ToolRegistryDocument`'s typed validation
        (Task 1). Never falls back to an empty/default registry -- a
        registry that fails to load simply does not become a
        `ToolRegistry`, and nothing downstream is permitted to construct
        one by hand from raw/partial data instead."""
        resolved = Path(path)
        if not resolved.exists():
            raise ToolRegistryLoadError(f"tool registry not found: {resolved}")
        try:
            raw_text = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            raise ToolRegistryLoadError(f"could not read tool registry {resolved}: {exc}") from exc

        try:
            raw_data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ToolRegistryLoadError(f"tool registry {resolved} is not valid JSON: {exc}") from exc

        try:
            document = ToolRegistryDocument.model_validate(raw_data)
        except ValidationError as exc:
            raise ToolRegistryLoadError(f"tool registry {resolved} failed validation: {exc}") from exc

        return cls(document)

    # ── Active version ───────────────────────────────────────────────

    @property
    def registry_version(self) -> str:
        """The active governed registry version this instance loaded."""
        return self._document.registry_version

    # ── Read-only collection access ──────────────────────────────────

    @property
    def tools(self) -> tuple[ToolDefinition, ...]:
        """Every registered tool/version, in registry order. A fresh
        tuple, not a reference into the loaded document's own list."""
        return tuple(self._document.tools)

    # ── Exact-version lookup / unknown tool-version failure ──────────

    def has_tool(self, tool_id: str) -> bool:
        """Whether `tool_id` is registered under any version."""
        return tool_id in self._tools_by_id

    def has_tool_version(self, tool_id: str, tool_version: str) -> bool:
        """Whether the exact `(tool_id, tool_version)` pair is
        registered."""
        return tool_version in self._tools_by_id.get(tool_id, {})

    def versions_for(self, tool_id: str) -> tuple[str, ...]:
        """Every registered version of `tool_id`, in registry order.
        Raises `ToolNotFoundError` for an unregistered `tool_id`."""
        if tool_id not in self._tools_by_id:
            raise ToolNotFoundError(tool_id)
        return tuple(tool.tool_version for tool in self._document.tools if tool.tool_id == tool_id)

    def get_tool(self, tool_id: str, tool_version: str) -> ToolDefinition:
        """Resolve the exact `(tool_id, tool_version)` pair to its typed
        `ToolDefinition` -- the one place a tool request is ever matched
        against the registry (Day 13 working rule: "Tool version is
        explicit"; no latest/range resolution). Raises `ToolNotFoundError`
        when `tool_id` is not registered at all, or
        `ToolVersionNotFoundError` when it is registered but not at this
        exact version -- never returns `None` or a synthesized default for
        either case (Day 13 working rule: "Missing, unknown or disabled
        tool fails closed"; disabled status is not itself a lookup
        failure -- see `is_active()`)."""
        versions = self._tools_by_id.get(tool_id)
        if versions is None:
            raise ToolNotFoundError(tool_id)
        try:
            return versions[tool_version]
        except KeyError:
            raise ToolVersionNotFoundError(tool_id, tool_version) from None

    def is_active(self, tool_id: str, tool_version: str) -> bool:
        """Whether the exact `(tool_id, tool_version)` pair is registered
        and its status is `active` -- Task 2's own "active/disabled
        state" responsibility. Raises the same `ToolNotFoundError` /
        `ToolVersionNotFoundError` as `get_tool()` for a pair this
        registry does not govern at all; a *registered* tool whose status
        is `disabled`/`deprecated` returns `False` rather than raising --
        it is a real, known, currently-non-executable entry, not an
        unknown one (Task 4's execution policy is what turns that `False`
        into a `tool_disabled` denial with zero transport calls)."""
        return self.get_tool(tool_id, tool_version).status is ToolStatus.ACTIVE
