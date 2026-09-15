"""
Day 13 Task 12 -- no direct model-to-tool execution.

Proves the repository's own dependency direction (`Day 13 Task.pdf`, TASK
12):

    caller / future workflow
    -> Tool Executor
    -> Registry + Policy
    -> MCP Gateway
    -> Transport

and that the disallowed shape --

    model output
    -> raw tool name
    -> transport.execute(...)

-- has no code path anywhere in this repository, today or by construction:

  - `aico.tools`'s own internal import graph is a strict, acyclic layering
    (`models`/`errors` -> `registry`/`policy`/`schema_validator`/
    `transport` -> `mcp_gateway` -> `executor`): nothing beneath
    `mcp_gateway.py` even imports it, and nothing beneath `executor.py`
    imports it either -- a hypothetical future caller that only reaches
    for `registry.py`/`policy.py` directly still has zero import-level
    path to transport;
  - no module outside `aico.tools` -- specifically the model-facing layers,
    `aico.rag` and `aico.platform` (`ModelGateway`) -- imports anything
    from `aico.tools` at all today, let alone `transport`/`mcp_gateway`
    specifically;
  - no call site anywhere in `src/` outside `aico/tools/` invokes
    `.execute(` on anything transport-shaped (the one legitimate
    non-tools `.execute(` in the whole tree, `memory/store.py`'s SQLite
    cursor, is unrelated and explicitly excluded);
  - `MCPGateway.execute()`/`ToolTransport.execute()` accept only typed,
    already-resolved objects (`ToolDefinition`, `ToolExecutionRequest`,
    `ToolExecutionPolicyDecision`, `ToolTransportRequest`) -- there is no
    signature anywhere that takes a bare tool-name string and a raw
    arguments dict;
  - `ToolExecutor` is the only entrypoint with no bypassing helper
    (`execute` is its sole public method -- already proven in
    `test_day13_executor.py`; re-asserted here as part of this file's own
    complete Task 12 proof);
  - behaviorally: a "model proposes a tool call" scenario -- an
    adversarial-looking dict shaped like model output, naming an
    unregistered tool and attempting to smuggle privilege-looking keys
    into `arguments` -- still only reaches this pipeline by being turned
    into a typed `ToolExecutionRequest` and run through the full
    `ToolExecutor.execute()` sequence, which still fails closed
    (`tool_not_found`, zero transport calls) exactly as it would for any
    other caller; there is no shortcut.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from aico.tools.executor import ToolExecutionErrorCategory, ToolExecutionStatus, ToolExecutor
from aico.tools.mcp_gateway import MCPGateway
from aico.tools.models import ToolExecutionRequest
from aico.tools.policy import ToolExecutionPolicy
from aico.tools.registry import ToolRegistry
from aico.tools.transport import FakeToolTransport, ToolTransport

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "aico"
TOOLS_DIR = SRC_ROOT / "tools"
COMMITTED_REGISTRY_PATH = REPO_ROOT / "tools" / "registry.v1.json"
COMMITTED_POLICY_PATH = REPO_ROOT / "policy" / "tool_execution_policy.v1.json"


def _internal_tool_imports(path: Path) -> set[str]:
    """Every `aico.tools.<module>` this file imports from, as bare module
    names (e.g. `{"errors", "models"}`)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("aico.tools."):
            modules.add(node.module.removeprefix("aico.tools."))
        if isinstance(node, ast.ImportFrom) and node.module == "aico.tools":
            modules.add("__init__")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("aico.tools.") and alias.name != "aico.tools":
                    modules.add(alias.name.removeprefix("aico.tools."))
    return modules


# ══════════════════════════════════════════════════════════════════════
# `aico.tools`'s own internal import graph is a strict, acyclic layering.
# ══════════════════════════════════════════════════════════════════════

# Every internal edge this package is allowed to have, by filename. A file
# not listed here, or an import this file's own set does not name, fails
# the test below -- this is the one place the *entire* allowed dependency
# graph is written down.
_ALLOWED_INTERNAL_IMPORTS: dict[str, set[str]] = {
    "models.py": set(),
    "errors.py": set(),
    "registry.py": {"errors", "models"},
    "policy.py": {"errors", "models"},
    "schema_validator.py": {"errors", "models"},
    "transport.py": {"errors"},
    "mcp_gateway.py": {"errors", "models", "policy", "transport"},
    "executor.py": {"errors", "mcp_gateway", "models", "policy", "registry", "schema_validator", "transport"},
    "__init__.py": {"errors", "executor", "mcp_gateway", "models", "policy", "registry", "schema_validator", "transport"},
}

# The one thing this whole test exists to prove: nothing "below"
# `mcp_gateway.py`/`executor.py` in the intended layering is allowed to
# import either of them -- a caller that only reaches for the registry or
# policy module directly still has no import-level path to transport.
_FORBIDDEN_UPWARD_IMPORTS: dict[str, set[str]] = {
    "models.py": {"executor", "mcp_gateway", "registry", "policy", "schema_validator", "transport"},
    "errors.py": {"executor", "mcp_gateway", "registry", "policy", "schema_validator", "transport"},
    "registry.py": {"executor", "mcp_gateway"},
    "policy.py": {"executor", "mcp_gateway"},
    "schema_validator.py": {"executor", "mcp_gateway"},
    "transport.py": {"executor", "mcp_gateway"},
    "mcp_gateway.py": {"executor"},
}


class TestInternalDependencyLayering:
    @pytest.mark.parametrize("filename", sorted(_ALLOWED_INTERNAL_IMPORTS))
    def test_only_allowed_internal_imports(self, filename: str) -> None:
        actual = _internal_tool_imports(TOOLS_DIR / filename)
        allowed = _ALLOWED_INTERNAL_IMPORTS[filename]
        unexpected = actual - allowed
        assert not unexpected, f"{filename} imports unexpected aico.tools modules: {unexpected}"

    @pytest.mark.parametrize("filename", sorted(_FORBIDDEN_UPWARD_IMPORTS))
    def test_no_upward_import_toward_gateway_or_executor(self, filename: str) -> None:
        actual = _internal_tool_imports(TOOLS_DIR / filename)
        forbidden = _FORBIDDEN_UPWARD_IMPORTS[filename]
        violated = actual & forbidden
        assert not violated, f"{filename} must never import {violated} (upward dependency)"

    def test_every_source_file_in_the_package_is_accounted_for(self) -> None:
        """Guards against a new file being added to `aico/tools/` without
        also updating this test's own dependency map."""
        actual_files = {p.name for p in TOOLS_DIR.glob("*.py")}
        assert actual_files == set(_ALLOWED_INTERNAL_IMPORTS)


# ══════════════════════════════════════════════════════════════════════
# No model/RAG-facing module imports anything from `aico.tools`.
# ══════════════════════════════════════════════════════════════════════


class TestModelLayerHasNoToolAccess:
    @pytest.mark.parametrize("relative_dir", ["rag", "platform"])
    def test_no_module_imports_anything_from_aico_tools(self, relative_dir: str) -> None:
        """`aico.rag` (the RAG/answer-generation pipeline) and
        `aico.platform` (`ModelGateway`, the one place this codebase calls
        an LLM) each import nothing from `aico.tools` at all today -- a
        stronger statement than "doesn't import transport specifically":
        the model-facing layers have zero ability to reach any part of
        the governed tool boundary, let alone bypass it."""
        target_dir = SRC_ROOT / relative_dir
        offending: list[str] = []
        for path in target_dir.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("aico.tools"):
                    offending.append(str(path))
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("aico.tools"):
                            offending.append(str(path))
        assert offending == []

    def test_no_module_outside_aico_tools_imports_transport_or_gateway(self) -> None:
        """Repository-wide: only files inside `aico/tools/` may import
        `aico.tools.transport` or `aico.tools.mcp_gateway` at all."""
        offending: list[str] = []
        for path in SRC_ROOT.rglob("*.py"):
            if TOOLS_DIR in path.parents:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and (
                    "aico.tools.transport" in node.module or "aico.tools.mcp_gateway" in node.module
                ):
                    offending.append(str(path))
        assert offending == []


# ══════════════════════════════════════════════════════════════════════
# No `.execute(` call site outside `aico/tools/` reaches anything
# transport-shaped.
# ══════════════════════════════════════════════════════════════════════


def test_no_execute_call_site_outside_aico_tools_except_the_known_sqlite_cursor() -> None:
    """The disallowed shape: `model output -> raw tool name ->
    transport.execute(...)`. A blunt but effective proxy across this
    repository's actual current shape: the only `.execute(` call sites
    anywhere in `src/` are inside `aico/tools/` itself, plus exactly one
    unrelated, already-known exception -- `aico/memory/store.py`'s SQLite
    cursor (`cursor.execute(sql, params)`), which has nothing to do with
    tool transport and predates this package entirely."""
    known_non_tools_exception = SRC_ROOT / "memory" / "store.py"
    offending: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        if TOOLS_DIR in path.parents or path == known_non_tools_exception:
            continue
        text = path.read_text(encoding="utf-8")
        if ".execute(" in text:
            offending.append(str(path))

    assert offending == []
    # Sanity: the known exception really does still contain `.execute(` --
    # this test would otherwise silently stop proving anything about it if
    # that file were ever refactored to stop using a raw cursor.
    assert ".execute(" in known_non_tools_exception.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════
# The gateway/transport surface takes only typed, resolved objects --
# never a bare tool-name string.
# ══════════════════════════════════════════════════════════════════════


def test_mcp_gateway_execute_has_no_bare_string_tool_name_parameter() -> None:
    import inspect

    signature = inspect.signature(MCPGateway.execute)
    param_names = set(signature.parameters) - {"self"}
    assert param_names == {"tool", "request", "validated_arguments", "policy_decision", "cancellation"}

    annotations = {name: param.annotation for name, param in signature.parameters.items() if name != "self"}
    # `tool`/`request`/`policy_decision` are each a specific governed type,
    # never `str`; only `validated_arguments` is a plain dict, and it is
    # the *already schema-validated* payload, not a raw tool name.
    assert "ToolDefinition" in str(annotations["tool"])
    assert "ToolExecutionRequest" in str(annotations["request"])
    assert "ToolExecutionPolicyDecision" in str(annotations["policy_decision"])


def test_tool_transport_protocol_execute_has_no_bare_string_tool_name_parameter() -> None:
    import inspect

    signature = inspect.signature(ToolTransport.execute)
    param_names = set(signature.parameters) - {"self"}
    assert param_names == {"request", "cancellation"}
    assert "ToolTransportRequest" in str(signature.parameters["request"].annotation)


def test_tool_executor_has_exactly_one_public_entrypoint() -> None:
    """Re-asserted here as part of this file's own complete Task 12 proof
    (already proven once in `test_day13_executor.py`, Task 7's own file)."""
    public_callables = [
        name for name in dir(ToolExecutor) if not name.startswith("_") and callable(getattr(ToolExecutor, name, None))
    ]
    assert public_callables == ["execute"]


# ══════════════════════════════════════════════════════════════════════
# Behavioral: a "model proposes a tool call" scenario still requires the
# full typed pipeline -- there is no shortcut.
# ══════════════════════════════════════════════════════════════════════


def _build_executor(steps: list) -> ToolExecutor:
    registry = ToolRegistry.load(COMMITTED_REGISTRY_PATH)
    policy = ToolExecutionPolicy.load(COMMITTED_POLICY_PATH)
    gateway = MCPGateway(FakeToolTransport(steps))
    return ToolExecutor(registry, policy, gateway)


class TestModelProposedToolCallStillRequiresTheFullPipeline:
    def test_model_invented_tool_name_fails_closed_with_zero_transport_calls(self) -> None:
        """A model's own free-form suggestion -- an unregistered tool name
        it invented -- is only ever executable by first being turned into
        a typed `ToolExecutionRequest`. There is no function anywhere that
        takes `"model_invented_tool"` as a bare string and reaches
        transport with it."""
        model_proposed_call = {
            "tool": "model_invented_tool",  # what a model might emit -- never used as-is
            "arguments": {"supplier_id": "SUP-ALPHA"},
        }
        executor = _build_executor([{"never": "reached"}])

        # The only way to act on this is to build the typed request --
        # note `trusted_permissions` is never sourced from the model
        # payload above; it is supplied here as trusted, out-of-band
        # application context, exactly as a real caller must.
        request = ToolExecutionRequest.model_validate(
            {
                "tool_id": model_proposed_call["tool"],
                "tool_version": "1.0.0",
                "arguments": model_proposed_call["arguments"],
                "trusted_permissions": ["read_structured_supplier"],
            }
        )

        result = executor.execute(request)

        assert result.status is ToolExecutionStatus.FAILURE
        assert result.error_category is ToolExecutionErrorCategory.TOOL_NOT_FOUND
        assert executor._gateway._transport.call_count == 0  # noqa: SLF001 -- white-box call-count check

    def test_model_smuggled_privilege_keys_in_arguments_are_still_inert(self) -> None:
        """A model-shaped payload that tries to smuggle a role/permission
        claim into `arguments` still cannot self-assert permission -- Task
        3's own guarantee, re-proven here through the full executor as
        part of Task 12's "if a future model proposes a tool call, this
        layer still requires registry, policy and schema validation"."""
        model_proposed_call = {
            "tool": "supplier_status_lookup",
            "arguments": {
                "supplier_id": "SUP-ALPHA",
                "role": "admin",
                "permissions": ["read_structured_supplier", "write_structured_supplier"],
            },
        }
        executor = _build_executor([{"never": "reached"}])
        request = ToolExecutionRequest.model_validate(
            {
                "tool_id": model_proposed_call["tool"],
                "tool_version": "1.0.0",
                "arguments": model_proposed_call["arguments"],
                "trusted_permissions": [],  # the trusted application grants nothing here
            }
        )

        result = executor.execute(request)

        # Rejected by input schema validation (the schema itself does not
        # declare `role`/`permissions`) before policy/transport are ever
        # reached -- either way, the smuggled keys grant nothing.
        assert result.status is ToolExecutionStatus.FAILURE
        assert result.error_category in (
            ToolExecutionErrorCategory.INPUT_INVALID,
            ToolExecutionErrorCategory.POLICY_DENIED,
        )
        assert executor._gateway._transport.call_count == 0  # noqa: SLF001 -- white-box call-count check

    def test_valid_model_proposed_call_still_goes_through_registry_policy_and_schema(self) -> None:
        """Even a *legitimate*, correctly-permissioned model-proposed call
        still runs the full sequence -- there is no fast path for a
        well-formed request either."""
        payload = {"supplier_id": "SUP-ALPHA", "status": "active", "as_of": "2026-09-15T09:00:00Z"}
        executor = _build_executor([payload])
        request = ToolExecutionRequest.model_validate(
            {
                "tool_id": "supplier_status_lookup",
                "tool_version": "1.0.0",
                "arguments": {"supplier_id": "SUP-ALPHA"},
                "trusted_permissions": ["read_structured_supplier"],
            }
        )

        result = executor.execute(request)

        assert result.status is ToolExecutionStatus.SUCCESS
        assert result.payload == payload
