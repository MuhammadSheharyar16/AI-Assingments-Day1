# Day 12 Gate-D Policy Requirements

Gate-D validates the actual final candidate response before API release.

The policy must govern:

```text
policy_version
allowed response statuses
citation requirements
quality limits
disclosure checks
latency budgets
safe-failure behavior
```

## Required principles

- citation IDs must reconcile with Gate-C-approved evidence
- typed/semantic-invalid response cannot pass
- disclosure policy is deterministic
- protected values cannot be released
- latency budgets are deterministic
- unsafe candidate content is never echoed in safe failure
- policy is read-only at runtime
