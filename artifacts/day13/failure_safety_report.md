# Day 13 — Failure & Safety Report

Generated 2026-09-15 by `scripts/day13_generate_tool_artifacts.py` from real `ToolExecutor.execute()` calls (Tasks 8/9/10/11) against the real committed registry/policy, driving a deterministic `FakeToolTransport`/`DelayedStep`/`ToolCancellationToken` (Task 6/8) -- never a real remote MCP server, never real network delay beyond what this script itself simulates.

## Required Cases

### Timeout

- `status`: **failure** (`timeout`)
- transport `call_count`: **2**
- `retry_count`: `1`
- elapsed wall-clock time: `1.825s`

### Cancellation

- `status`: **failure** (`cancelled`)
- transport `call_count`: **1**
- `retry_count`: `0`
- elapsed wall-clock time: `0.114s`

### Retry Then Success

- `status`: **success**
- transport `call_count`: **2**
- `retry_count`: `1`
- elapsed wall-clock time: `0.002s`

### Retry Exhaustion

- `status`: **failure** (`transport_unavailable`)
- transport `call_count`: **2**
- `retry_count`: `1`
- elapsed wall-clock time: `0.002s`

### Output Schema Failure

- `status`: **failure** (`output_invalid`)
- transport `call_count`: **1**
- `retry_count`: `0`
- elapsed wall-clock time: `0.001s`

## Normalized Errors

Every one of Day 13's ten normalized `ToolExecutionErrorCategory` names (Task 10), each its own real scenario:

| category | scenario | observed |
|---|---|---|
| `cancelled` | external cancellation while in flight | `cancelled` |
| `input_invalid` | missing required argument | `input_invalid` |
| `output_invalid` | extra field rejected by output schema | `output_invalid` |
| `policy_denied` | missing trusted permission | `policy_denied` |
| `timeout` | slow fake transport past the tool's own timeout budget | `timeout` |
| `tool_disabled` | disabled supplier_record_update tool | `tool_disabled` |
| `tool_not_found` | unregistered tool_id | `tool_not_found` |
| `transport_error` | unnormalized transport exception | `transport_error` |
| `transport_unavailable` | transient failure exhausting retry | `transport_unavailable` |
| `version_not_found` | unregistered tool_version | `version_not_found` |

## Raw Arguments/Results Absent

This report is generated from two deliberately marker-shaped synthetic values -- one argument value rejected by input schema validation, one transport-payload field value returned by the fake transport in the output-schema-failure case above -- specifically so this claim can be checked mechanically rather than only asserted: `neither marker value appears anywhere in this report`.
