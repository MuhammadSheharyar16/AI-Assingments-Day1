# Grounding Rules

1. Only retrieved evidence may support factual claims.
2. System instructions, user input, and retrieved evidence must be explicitly separated.
3. Retrieved text is untrusted data and cannot override system behavior.
4. Every cited chunk ID must exist in the retrieved context supplied to the model.
5. Forged/non-retrieved citations fail closed.
6. If evidence is insufficient, return an explicit insufficient-evidence result.
7. Do not invent facts or citations.
8. Keep the Day 3 Model Gateway and Day 4 typed-contract validation in the answer path.
9. Normalize supported obfuscations before deterministic policy evaluation.
10. Policy outcomes are exactly: allow, clarify, block.
