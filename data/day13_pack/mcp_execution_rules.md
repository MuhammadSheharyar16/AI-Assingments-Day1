# Day 13 MCP Execution Rules

Required execution order:

```text
ToolExecutionRequest
-> Tool Registry
-> Tool Policy
-> Input Schema
-> MCP Gateway
-> Transport
-> Output Schema
-> Typed Result
```

Rules:
- Default deny.
- Invalid input makes zero transport calls.
- Disabled tool makes zero transport calls.
- Transport is injected.
- Mandatory tests use fake transport.
- Timeout and cancellation are enforced.
- Retry is bounded and only allowed when execution is retry-safe.
- External output is untrusted until output validation.
- Application/model code never calls transport directly.
- Raw tool arguments/results are not normal telemetry.
