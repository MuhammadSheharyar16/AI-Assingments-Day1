# Day 11 Evidence Policy Requirements

Gate-C evaluates **actual returned evidence**, not whether a matching record happens to exist elsewhere in the corpus.

## Required governed concepts

The implementation must support:

```text
source registry
source status
source authority
allowed intents
supported facets
source version
freshness policy
required facets
conflict policy
minimum evidence requirement
```

## Required failure behavior

Fail or return insufficient evidence when appropriate for:

- unknown source
- disabled source
- invalid source/intent relationship
- broken provenance
- content-hash mismatch
- source-version mismatch
- stale evidence
- missing required facet
- unresolved conflicting evidence
- Gate-B scope violation

The model is not the authority for any of these decisions.
