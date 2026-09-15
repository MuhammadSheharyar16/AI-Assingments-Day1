"""
Day 13 Task 14 — Tool Registry / MCP execution / failure-safety artifacts.

Run: uv run python scripts/day13_generate_tool_artifacts.py

Generates the three required artifacts from real system behavior, not
hand-written prose — the same discipline
`scripts/day12_generate_gate_d_artifacts.py` already established: the
real `ToolRegistry` (Task 2) loaded from the real committed
`tools/registry.v1.json`, the real `ToolExecutionPolicy` (Task 4) loaded
from the real committed `policy/tool_execution_policy.v1.json`, and the
real `ToolExecutor` (Task 7-9, 13) driving a deterministic
`FakeToolTransport` (Task 6/8) exactly the way every mandatory test does.

    artifacts/day13/tool_registry_report.md    — registry version, tool/
                                                   version count, active/
                                                   disabled count, an
                                                   owner/risk/side-effect
                                                   summary table, and one
                                                   real invalid-registry
                                                   rejection (a duplicate
                                                   tool/version, Task 1's
                                                   own `ToolRegistryDocument`
                                                   validator)
    artifacts/day13/mcp_execution_report.md    — a successful active-tool
                                                   execution, invalid
                                                   input (transport calls
                                                   = 0), policy denial
                                                   (transport calls = 0),
                                                   the disabled tool
                                                   (transport calls = 0),
                                                   and a valid output-
                                                   schema result, each a
                                                   real `ToolExecutor.
                                                   execute()` call
    artifacts/day13/failure_safety_report.md   — timeout, cancellation,
                                                   retry-then-success,
                                                   retry exhaustion,
                                                   output-schema failure,
                                                   every normalized error
                                                   category with its own
                                                   real scenario, and an
                                                   explicit, checked proof
                                                   that no raw argument/
                                                   transport-payload value
                                                   appears anywhere in
                                                   this report's own text

Every request/argument/transport-payload value below is synthetic demo
data this script constructs directly (mirroring `execution_cases.json`/
`transport_failure_cases.json`/`output_validation_cases.json`'s own
fixture shapes) — never a real caller's request. No raw argument value,
transport payload value, or exception message is ever written to any of
these files; only governed ids, typed decision/category fields, counts,
and (`tool_registry_report.md`'s rejection case) the registry validator's
own structural error text."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from aico.tools.executor import ToolExecutionErrorCategory, ToolExecutionResult, ToolExecutionStatus, ToolExecutor
from aico.tools.mcp_gateway import MCPGateway
from aico.tools.models import ToolExecutionRequest, ToolRegistryDocument
from aico.tools.policy import ToolExecutionPolicy
from aico.tools.registry import ToolRegistry
from aico.tools.transport import DelayedStep, FakeToolTransport, ToolCancellationToken

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = REPO_ROOT / "artifacts" / "day13"
COMMITTED_REGISTRY_PATH = REPO_ROOT / "tools" / "registry.v1.json"
COMMITTED_POLICY_PATH = REPO_ROOT / "policy" / "tool_execution_policy.v1.json"

_LOOKUP_TOOL_ID = "supplier_status_lookup"
_LOOKUP_TOOL_VERSION = "1.0.0"
_UPDATE_TOOL_ID = "supplier_record_update"

# Deliberately marker-shaped so the "raw values absent" self-check below has
# something unambiguous to search for -- never a real secret, just synthetic
# demo data that looks the way a leaked value would.
_MARKER_ARGUMENT_VALUE = "SUP-ARTIFACT-MARKER-VALUE-13579"
_MARKER_PAYLOAD_VALUE = "2099-01-01T00:00:00Z-ARTIFACT-MARKER-24680"


def _lookup_request(**overrides: object) -> ToolExecutionRequest:
    fields: dict = {
        "tool_id": _LOOKUP_TOOL_ID,
        "tool_version": _LOOKUP_TOOL_VERSION,
        "arguments": {"supplier_id": "SUP-ALPHA"},
        "trusted_permissions": ["read_structured_supplier"],
        "request_id": "REQ-ARTIFACT",
        "correlation_id": "CORR-ARTIFACT",
    }
    fields.update(overrides)
    return ToolExecutionRequest.model_validate(fields)


def _executor(steps: list, *, cancellation_poll_seconds: float = 0.02) -> tuple[ToolExecutor, FakeToolTransport]:
    registry = ToolRegistry.load(COMMITTED_REGISTRY_PATH)
    policy = ToolExecutionPolicy.load(COMMITTED_POLICY_PATH)
    transport = FakeToolTransport(steps)
    gateway = MCPGateway(transport)
    executor = ToolExecutor(registry, policy, gateway, cancellation_poll_seconds=cancellation_poll_seconds)
    return executor, transport


def _outcome(result: ToolExecutionResult) -> str:
    if result.status is ToolExecutionStatus.SUCCESS:
        return "success"
    return f"failure ({result.error_category.value})"


# ── Task 1: tool_registry_report.md ────────────────────────────────────────


@dataclass
class ToolSummaryRow:
    tool_id: str
    tool_version: str
    status: str
    owner: str
    risk_level: str
    side_effecting: bool
    idempotent: bool


@dataclass
class RegistryEvidence:
    registry_version: str
    total_tool_versions: int
    active_count: int
    disabled_count: int
    rows: list[ToolSummaryRow]
    invalid_registry_error_text: str


def gather_registry_evidence() -> RegistryEvidence:
    registry = ToolRegistry.load(COMMITTED_REGISTRY_PATH)

    rows = [
        ToolSummaryRow(
            tool_id=tool.tool_id,
            tool_version=tool.tool_version,
            status=tool.status.value,
            owner=tool.owner,
            risk_level=tool.risk_level.value,
            side_effecting=tool.side_effecting,
            idempotent=tool.idempotent,
        )
        for tool in registry.tools
    ]
    active_count = sum(1 for r in rows if r.status == "active")
    disabled_count = sum(1 for r in rows if r.status == "disabled")

    # One real invalid-registry rejection: duplicate the first committed
    # tool's own entry (mirrors `registry_validation_cases.json` REG13-002)
    # and feed the mutated document through the real
    # `ToolRegistryDocument.model_validate()` validator (Task 1).
    raw = json.loads(COMMITTED_REGISTRY_PATH.read_text(encoding="utf-8"))
    raw["tools"] = [*raw["tools"], raw["tools"][0]]
    try:
        ToolRegistryDocument.model_validate(raw)
        invalid_registry_error_text = "(no error raised -- unexpected)"
    except ValidationError as exc:
        invalid_registry_error_text = str(exc.errors()[0]["msg"])

    return RegistryEvidence(
        registry_version=registry.registry_version,
        total_tool_versions=len(rows),
        active_count=active_count,
        disabled_count=disabled_count,
        rows=rows,
        invalid_registry_error_text=invalid_registry_error_text,
    )


def render_tool_registry_report(e: RegistryEvidence) -> str:
    lines: list[str] = []
    lines.append("# Day 13 — Tool Registry Report")
    lines.append("")
    lines.append(
        f"Generated {datetime.now(UTC).date().isoformat()} by "
        "`scripts/day13_generate_tool_artifacts.py` from a real `ToolRegistry.load()` call (Task 2) "
        f"against the real committed `{COMMITTED_REGISTRY_PATH.relative_to(REPO_ROOT)}`."
    )
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- `registry_version`: `{e.registry_version}`")
    lines.append(f"- Total registered tool/version entries: **{e.total_tool_versions}**")
    lines.append(f"- Active: **{e.active_count}**")
    lines.append(f"- Disabled: **{e.disabled_count}**")
    lines.append("")

    lines.append("## Owner / Risk / Side-Effect Summary")
    lines.append("")
    lines.append("| tool_id | tool_version | status | owner | risk_level | side_effecting | idempotent |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in e.rows:
        lines.append(
            f"| `{r.tool_id}` | `{r.tool_version}` | `{r.status}` | {r.owner} | `{r.risk_level}` | "
            f"{r.side_effecting} | {r.idempotent} |"
        )
    lines.append("")

    lines.append("## Invalid-Registry Rejection")
    lines.append("")
    lines.append(
        "A duplicate `(tool_id, tool_version)` entry (mirrors `registry_validation_cases.json` "
        "REG13-002), fed through the real `ToolRegistryDocument.model_validate()` validator (Task 1):"
    )
    lines.append("")
    lines.append(f"> {e.invalid_registry_error_text}")
    lines.append("")

    return "\n".join(lines)


# ── Task 2: mcp_execution_report.md ────────────────────────────────────────


@dataclass
class ExecutionCase:
    label: str
    request: ToolExecutionRequest
    result: ToolExecutionResult
    transport_call_count: int


@dataclass
class McpExecutionEvidence:
    registry_version: str
    policy_version: str
    cases: list[ExecutionCase]


def gather_mcp_execution_evidence() -> McpExecutionEvidence:
    cases: list[ExecutionCase] = []

    def run(label: str, request: ToolExecutionRequest, steps: list) -> None:
        executor, transport = _executor(steps)
        result = executor.execute(request)
        cases.append(ExecutionCase(label, request, result, transport.call_count))

    success_payload = {"supplier_id": "SUP-ALPHA", "status": "active", "as_of": "2026-09-15T09:00:00Z"}

    # 1. Successful active-tool execution.
    run("successful active tool execution", _lookup_request(), [success_payload])

    # 2. Invalid input -- transport calls = 0.
    run("invalid input", _lookup_request(arguments={}), [])

    # 3. Policy denial -- transport calls = 0.
    run("policy denial (missing trusted permission)", _lookup_request(trusted_permissions=[]), [])

    # 4. The disabled tool -- transport calls = 0.
    run(
        "disabled tool",
        _lookup_request(
            tool_id=_UPDATE_TOOL_ID,
            tool_version="1.0.0",
            arguments={"supplier_id": "SUP-ALPHA", "status": "inactive"},
            trusted_permissions=["write_structured_supplier"],
        ),
        [],
    )

    # 5. Valid output-schema result -- same shape as (1), reported
    # separately to make the output-schema-validated payload explicit.
    run("valid output-schema result", _lookup_request(), [success_payload])

    registry = ToolRegistry.load(COMMITTED_REGISTRY_PATH)
    policy = ToolExecutionPolicy.load(COMMITTED_POLICY_PATH)
    return McpExecutionEvidence(registry_version=registry.registry_version, policy_version=policy.policy_version, cases=cases)


def render_mcp_execution_report(e: McpExecutionEvidence) -> str:
    lines: list[str] = []
    lines.append("# Day 13 — MCP Execution Report")
    lines.append("")
    lines.append(
        f"Generated {datetime.now(UTC).date().isoformat()} by "
        "`scripts/day13_generate_tool_artifacts.py` from real `ToolExecutor.execute()` calls "
        f"(Tasks 2/4/5/6/7) against the real committed registry (`registry_version` `{e.registry_version}`) "
        f"and policy (`policy_version` `{e.policy_version}`), driving a deterministic `FakeToolTransport` "
        "(Task 6) -- never a real remote MCP server."
    )
    lines.append("")

    for c in e.cases:
        r = c.result
        lines.append(f"## {c.label.title()}")
        lines.append("")
        lines.append(f"- `tool_id`/`tool_version`: `{r.tool_id}`@`{r.tool_version}`")
        lines.append(f"- `status`: **{r.status.value}**")
        if r.error_category is not None:
            lines.append(f"- `error_category`: `{r.error_category.value}`")
        lines.append(f"- transport `call_count`: **{c.transport_call_count}**")
        lines.append(f"- `retry_count`: `{r.retry_count}`")
        if r.status is ToolExecutionStatus.SUCCESS:
            lines.append(f"- output-schema-validated `payload` keys: `{sorted(r.payload.keys())}`")
        lines.append("")

    return "\n".join(lines)


# ── Task 3: failure_safety_report.md ───────────────────────────────────────


@dataclass
class FailureSafetyCase:
    label: str
    result: ToolExecutionResult
    transport_call_count: int
    elapsed_seconds: float


@dataclass
class NormalizedErrorRow:
    category: str
    scenario: str
    observed_category: str


@dataclass
class FailureSafetyEvidence:
    cases: list[FailureSafetyCase]
    normalized_error_rows: list[NormalizedErrorRow]
    marker_values_checked: tuple[str, str]
    rendered_report_contains_marker_values: bool  # filled in after rendering, see main()


def _run_case(label: str, steps: list, *, request: ToolExecutionRequest, cancellation=None) -> FailureSafetyCase:
    executor, transport = _executor(steps, cancellation_poll_seconds=0.005)
    start = time.monotonic()
    result = executor.execute(request, cancellation=cancellation)
    elapsed = time.monotonic() - start
    return FailureSafetyCase(label, result, transport.call_count, elapsed)


def gather_failure_safety_evidence() -> FailureSafetyEvidence:
    cases: list[FailureSafetyCase] = []

    # 1. Timeout: a slow fake transport (Task 8's own tool), against the
    # real committed tool's own 900ms budget. Two slow steps, not one --
    # the real tool declares `timeout` retryable (Task 9), so a single
    # slow step would time out on attempt 1 and then exhaust the fake
    # transport's own configured steps on the retry attempt, which the
    # gateway would normalize to `transport_error` instead of a clean
    # `timeout` result.
    slow_payload = {"supplier_id": "SUP-ALPHA", "status": "active", "as_of": "x"}
    cases.append(
        _run_case(
            "timeout",
            [DelayedStep(delay_seconds=1.1, step=slow_payload), DelayedStep(delay_seconds=1.1, step=slow_payload)],
            request=_lookup_request(),
        )
    )

    # 2. Cancellation: blocked on `wait_until_cancelled`, cancelled from
    # another thread shortly after the call starts.
    token = ToolCancellationToken()

    def _cancel_shortly() -> None:
        time.sleep(0.1)
        token.cancel()

    threading.Thread(target=_cancel_shortly, daemon=True).start()
    cases.append(_run_case("cancellation", ["wait_until_cancelled"], request=_lookup_request(), cancellation=token))

    # 3. Retry then success (`transport_failure_cases.json` TR13-002).
    cases.append(
        _run_case(
            "retry then success",
            ["transport_unavailable", {"supplier_id": "SUP-ALPHA", "status": "under_review", "as_of": "x"}],
            request=_lookup_request(),
        )
    )

    # 4. Retry exhaustion (`transport_failure_cases.json` TR13-003).
    cases.append(
        _run_case(
            "retry exhaustion",
            ["transport_unavailable", "transport_unavailable"],
            request=_lookup_request(),
        )
    )

    # 5. Output schema failure (`output_validation_cases.json` OUT13-005).
    # The malformed *payload* embeds `_MARKER_PAYLOAD_VALUE` -- this is the
    # "raw transport-payload value" half of the check below; the argument
    # itself is a plain, valid one (it must reach transport for this case
    # to demonstrate output validation at all).
    cases.append(
        _run_case(
            "output schema failure",
            [
                {
                    "supplier_id": "SUP-ALPHA",
                    "status": "active",
                    "as_of": _MARKER_PAYLOAD_VALUE,
                    "secret_internal_note": "must not pass",
                }
            ],
            request=_lookup_request(),
        )
    )

    # Normalized errors: every one of Day 13's ten categories, each its
    # own real scenario -- most already produced above; the remaining ones
    # (tool_not_found/version_not_found/tool_disabled/policy_denied/
    # input_invalid) are cheap to reproduce here for one consolidated view.
    normalized_error_rows: list[NormalizedErrorRow] = []

    def normalized(category: ToolExecutionErrorCategory, scenario: str, result: ToolExecutionResult) -> None:
        observed = result.error_category.value if result.error_category is not None else "success"
        normalized_error_rows.append(NormalizedErrorRow(category.value, scenario, observed))

    tool_not_found_case = _run_case(
        "tool_not_found (normalized-error sweep)",
        [],
        request=_lookup_request(tool_id="model_invented_tool", trusted_permissions=[]),
    )
    normalized(ToolExecutionErrorCategory.TOOL_NOT_FOUND, "unregistered tool_id", tool_not_found_case.result)

    version_not_found_case = _run_case(
        "version_not_found (normalized-error sweep)", [], request=_lookup_request(tool_version="9.9.9")
    )
    normalized(ToolExecutionErrorCategory.VERSION_NOT_FOUND, "unregistered tool_version", version_not_found_case.result)

    tool_disabled_case = _run_case(
        "tool_disabled (normalized-error sweep)",
        [],
        request=_lookup_request(
            tool_id=_UPDATE_TOOL_ID,
            tool_version="1.0.0",
            arguments={"supplier_id": "SUP-ALPHA", "status": "inactive"},
            trusted_permissions=["write_structured_supplier"],
        ),
    )
    normalized(ToolExecutionErrorCategory.TOOL_DISABLED, "disabled supplier_record_update tool", tool_disabled_case.result)

    # Policy (Task 4) runs before input schema validation (Task 5) in the
    # required stage order, so an extra, marker-bearing argument key here
    # is never even inspected -- this case doubles as the "raw argument
    # value" half of the check below: denied purely on missing trusted
    # permission, the marker never reaches schema validation or transport.
    policy_denied_case = _run_case(
        "policy_denied (normalized-error sweep)",
        [],
        request=_lookup_request(
            arguments={"supplier_id": "SUP-ALPHA", "note": _MARKER_ARGUMENT_VALUE}, trusted_permissions=[]
        ),
    )
    normalized(ToolExecutionErrorCategory.POLICY_DENIED, "missing trusted permission", policy_denied_case.result)

    input_invalid_case = _run_case(
        "input_invalid (normalized-error sweep)", [], request=_lookup_request(arguments={})
    )
    normalized(ToolExecutionErrorCategory.INPUT_INVALID, "missing required argument", input_invalid_case.result)

    normalized(ToolExecutionErrorCategory.TIMEOUT, "slow fake transport past the tool's own timeout budget", cases[0].result)
    normalized(ToolExecutionErrorCategory.CANCELLED, "external cancellation while in flight", cases[1].result)
    normalized(
        ToolExecutionErrorCategory.TRANSPORT_UNAVAILABLE, "transient failure exhausting retry", cases[3].result
    )
    transport_error_case = _run_case("transport_error (normalized-error sweep)", ["transport_error"], request=_lookup_request())
    normalized(ToolExecutionErrorCategory.TRANSPORT_ERROR, "unnormalized transport exception", transport_error_case.result)
    normalized(ToolExecutionErrorCategory.OUTPUT_INVALID, "extra field rejected by output schema", cases[4].result)

    cases.extend(
        [
            tool_not_found_case,
            version_not_found_case,
            tool_disabled_case,
            policy_denied_case,
            input_invalid_case,
            transport_error_case,
        ]
    )

    return FailureSafetyEvidence(
        cases=cases,
        normalized_error_rows=normalized_error_rows,
        marker_values_checked=(_MARKER_ARGUMENT_VALUE, _MARKER_PAYLOAD_VALUE),
        rendered_report_contains_marker_values=False,  # set for real after rendering, see main()
    )


def render_failure_safety_report(e: FailureSafetyEvidence) -> str:
    lines: list[str] = []
    lines.append("# Day 13 — Failure & Safety Report")
    lines.append("")
    lines.append(
        f"Generated {datetime.now(UTC).date().isoformat()} by "
        "`scripts/day13_generate_tool_artifacts.py` from real `ToolExecutor.execute()` calls "
        "(Tasks 8/9/10/11) against the real committed registry/policy, driving a deterministic "
        "`FakeToolTransport`/`DelayedStep`/`ToolCancellationToken` (Task 6/8) -- never a real remote "
        "MCP server, never real network delay beyond what this script itself simulates."
    )
    lines.append("")

    lines.append("## Required Cases")
    lines.append("")
    for c in e.cases[:5]:
        r = c.result
        lines.append(f"### {c.label.title()}")
        lines.append("")
        lines.append(f"- `status`: **{r.status.value}**" + (f" (`{r.error_category.value}`)" if r.error_category else ""))
        lines.append(f"- transport `call_count`: **{c.transport_call_count}**")
        lines.append(f"- `retry_count`: `{r.retry_count}`")
        lines.append(f"- elapsed wall-clock time: `{c.elapsed_seconds:.3f}s`")
        lines.append("")

    lines.append("## Normalized Errors")
    lines.append("")
    lines.append("Every one of Day 13's ten normalized `ToolExecutionErrorCategory` names (Task 10), each its own real scenario:")
    lines.append("")
    lines.append("| category | scenario | observed |")
    lines.append("|---|---|---|")
    for row in sorted(e.normalized_error_rows, key=lambda r: r.category):
        lines.append(f"| `{row.category}` | {row.scenario} | `{row.observed_category}` |")
    lines.append("")

    lines.append("## Raw Arguments/Results Absent")
    lines.append("")
    lines.append(
        "This report is generated from two deliberately marker-shaped synthetic values -- one "
        "argument value rejected by input schema validation, one transport-payload field value "
        "returned by the fake transport in the output-schema-failure case above -- specifically so "
        "this claim can be checked mechanically rather than only asserted: "
        f"`{'FOUND -- FAIL' if e.rendered_report_contains_marker_values else 'neither marker value appears anywhere in this report'}`."
    )
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    registry_evidence = gather_registry_evidence()
    mcp_execution_evidence = gather_mcp_execution_evidence()
    failure_safety_evidence = gather_failure_safety_evidence()

    tool_registry_report = render_tool_registry_report(registry_evidence)
    mcp_execution_report = render_mcp_execution_report(mcp_execution_evidence)
    failure_safety_report = render_failure_safety_report(failure_safety_evidence)

    # The self-check failure_safety_report.md's own text describes: confirm
    # neither marker value leaked into the *actual rendered* report before
    # writing it, and re-render if the (should-never-happen) alternative
    # were ever true, so the file on disk always matches what it claims.
    contains_marker = any(marker in failure_safety_report for marker in failure_safety_evidence.marker_values_checked)
    if contains_marker != failure_safety_evidence.rendered_report_contains_marker_values:
        failure_safety_evidence.rendered_report_contains_marker_values = contains_marker
        failure_safety_report = render_failure_safety_report(failure_safety_evidence)
    assert not contains_marker, "a marker value leaked into failure_safety_report.md -- this must never happen"

    rendered = {
        "tool_registry_report.md": tool_registry_report,
        "mcp_execution_report.md": mcp_execution_report,
        "failure_safety_report.md": failure_safety_report,
    }

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in rendered.items():
        (ARTIFACT_DIR / name).write_text(text, encoding="utf-8")
        print(f"wrote {(ARTIFACT_DIR / name).relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
