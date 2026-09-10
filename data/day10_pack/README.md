# AICO Day 10 Resource Pack

Supports **Day 10 - Gate-B Permissions, Tenant Isolation, PII and Safe Disclosure**.

## Contents

```text
day10_pack/
  README.md
  gate_b_policy_requirements.md
  disclosure_rules.md
  fixtures/
    gate_b_policy_v1.json
    permission_cases.json
    tenant_scope_cases.json
    pii_disclosure_cases.json
```

## Core rule

Gate-B decides:

- whether the trusted caller may proceed
- the effective tenant/data scope
- the allowed data classification
- the PII/disclosure action

The model, memory, and request body may not widen authorization.

## Fixed-fixture rule

These are synthetic fixed inputs for Day 10.

Do not edit a failing fixture to make the implementation pass.

The supplied policy is a lab policy, not a production authorization model.
