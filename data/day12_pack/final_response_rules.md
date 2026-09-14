# Day 12 Final Response Rules

## Citation

```text
final_citations ⊆ Gate-C validated evidence
```

A single forged or rejected-evidence citation fails the candidate.

## Quality

The final live gate checks deterministic properties such as:

- valid contract
- valid semantic status
- answer/status consistency
- required citations
- nonempty answered response
- output-size bounds

This does not replace the Day 7 offline evaluation suite.

## Disclosure

The final candidate must not expose:

- fields denied by the Day 10 disclosure profile
- unredacted protected values where redaction is required
- synthetic secret/token values
- hidden prompt/policy internals

## Latency

A hard budget violation returns a typed safe failure.

The system must not pretend an over-budget request met the budget.
