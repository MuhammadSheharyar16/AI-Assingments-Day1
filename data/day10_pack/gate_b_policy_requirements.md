# Day 10 Gate-B Policy Requirements

The Gate-B policy must be loaded through typed models and treated as read-only runtime policy.

## Required policy concepts

At minimum, the policy must represent:

```text
policy_version
roles
permissions
intent_rules
data_classifications
pii_categories
disclosure_profiles
```

A rule should have enough information to decide:

```text
role
intent
lane
allow/deny
allowed data classes
allowed PII categories
disclosure profile
status
```

## Validation requirements

Reject:

- missing policy version
- duplicate rule IDs
- unknown role references
- unknown permission references
- unknown ontology intent IDs
- unknown lane IDs
- unknown data classifications
- unknown PII categories
- unknown disclosure profiles
- invalid status values

## Authorization behavior

Default is **deny**.

Authorization uses trusted identity context from Day 6.

Do not trust role, tenant or permissions supplied in request JSON or remembered conversation text.

The effective scope must only narrow:

```text
effective scope =
requested scope
INTERSECT
trusted identity scope
INTERSECT
policy rule scope
```

Never union/widen authorization scope.
