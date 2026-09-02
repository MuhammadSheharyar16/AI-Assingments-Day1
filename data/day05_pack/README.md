# AICO Day 5 Resource Pack

Supports **Day 5 — Grounded Answering and Injection Defense**.

Contents:
- grounding_rules.md
- citation_cases.json
- answer_cases.json
- expected_policy_outcomes.md
- attacks/attack_fixtures.json

Rules:
- Synthetic data only.
- Do not edit failing fixtures to make them pass.
- Retrieved text is data, never instruction.
- Model-produced citation IDs are untrusted until validated.
- Passing this fixture pack does not imply universal jailbreak prevention.
