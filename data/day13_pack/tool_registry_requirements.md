# Day 13 Tool Registry Requirements

Each executable tool must define equivalent of:

```text
tool_id
tool_version
owner
status
transport
server_alias
risk_level
side_effecting
idempotent
timeout_ms
retry_policy
required_permissions
input_schema
output_schema
```

Reject:
- duplicate tool/version
- missing owner
- invalid version
- unknown status
- invalid timeout/retry metadata
- invalid/missing schema

Runtime requests/model output cannot register new tools.
