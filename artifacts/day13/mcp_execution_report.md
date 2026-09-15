# Day 13 — MCP Execution Report

Generated 2026-09-15 by `scripts/day13_generate_tool_artifacts.py` from real `ToolExecutor.execute()` calls (Tasks 2/4/5/6/7) against the real committed registry (`registry_version` `1.0`) and policy (`policy_version` `1.0`), driving a deterministic `FakeToolTransport` (Task 6) -- never a real remote MCP server.

## Successful Active Tool Execution

- `tool_id`/`tool_version`: `supplier_status_lookup`@`1.0.0`
- `status`: **success**
- transport `call_count`: **1**
- `retry_count`: `0`
- output-schema-validated `payload` keys: `['as_of', 'status', 'supplier_id']`

## Invalid Input

- `tool_id`/`tool_version`: `supplier_status_lookup`@`1.0.0`
- `status`: **failure**
- `error_category`: `input_invalid`
- transport `call_count`: **0**
- `retry_count`: `0`

## Policy Denial (Missing Trusted Permission)

- `tool_id`/`tool_version`: `supplier_status_lookup`@`1.0.0`
- `status`: **failure**
- `error_category`: `policy_denied`
- transport `call_count`: **0**
- `retry_count`: `0`

## Disabled Tool

- `tool_id`/`tool_version`: `supplier_record_update`@`1.0.0`
- `status`: **failure**
- `error_category`: `tool_disabled`
- transport `call_count`: **0**
- `retry_count`: `0`

## Valid Output-Schema Result

- `tool_id`/`tool_version`: `supplier_status_lookup`@`1.0.0`
- `status`: **success**
- transport `call_count`: **1**
- `retry_count`: `0`
- output-schema-validated `payload` keys: `['as_of', 'status', 'supplier_id']`
