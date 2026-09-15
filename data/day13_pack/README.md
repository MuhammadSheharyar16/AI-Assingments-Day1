# AICO Day 13 Resource Pack

Supports **Day 13 - Tool Registry and MCP Gateway**.

## Contents

```text
day13_pack/
  README.md
  tool_registry_requirements.md
  mcp_execution_rules.md
  fixtures/
    tool_registry_v1.json
    tool_execution_policy_v1.json
    registry_validation_cases.json
    execution_cases.json
    transport_failure_cases.json
    output_validation_cases.json
```

## Core rule

A tool is not executable merely because a model/user named it.

Execution requires:

```text
registered tool/version
-> deterministic policy approval
-> input schema validation
-> MCP Gateway
-> output schema validation
```

Mandatory tests use deterministic fake transport. A real remote MCP server is not required.
