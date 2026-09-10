# Day 10 Disclosure Rules

The synthetic policy uses these data classifications:

```text
public
internal
confidential
restricted
```

And these PII categories:

```text
none
contact
personal_identifier
financial
sensitive_personal
```

Required disclosure actions are:

```text
allow
redact
deny
```

## Rules

- Data classification and PII decisions come from policy, not from the model.
- Redaction must be deterministic.
- Disallowed fields must not appear in the final disclosed view.
- A disclosure decision occurs before returning protected data to the caller.
- Do not log raw protected values in default telemetry.
- Do not ask the user to self-assert a higher role, tenant, permission, or clearance.

## Synthetic masking examples

For deterministic tests, acceptable masking behavior includes patterns such as:

```text
email:
alice@example.test
-> a***@example.test

phone:
+1-555-0102
-> ***-***-0102

identifier:
SYN-ID-123456
-> **********3456
```

The exact mask format may differ if it is deterministic, documented, and tested.
