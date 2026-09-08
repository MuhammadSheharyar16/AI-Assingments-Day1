# Day 8 — Context Compaction

Generated 2026-09-08 by `scripts/day08_generate_memory_artifacts.py` from real `build_memory_context`/`compact_session` calls (`src/aico/memory/context_builder.py`, `src/aico/memory/summarizer.py`) against a synthetic 10-turn session. No turn/summary text appears anywhere in this file - see Redaction Check.

## Configured Memory Budget

- This demo's `max_memory_tokens`: 40 (a small budget, chosen so compaction is guaranteed and clearly visible)
- This demo's `max_recent_turns`: 4
- `max_summary_tokens` (bounds the summary itself): 150
- Production defaults (`context_builder.DEFAULT_MEMORY_BUDGET`): `max_memory_tokens=800`, `max_recent_turns=12` - larger, so an ordinary conversation compacts far less often than this demo does.

## Context Size Before Compaction

- Session turn count: 10
- `build_memory_context` included turns: 4
- `build_memory_context` omitted turns (eligible for compaction): 6
- Token count: 16 (≤ 40, the configured budget)

## Compaction Result

- Summary version: 1
- Summary created at: 2026-01-01T00:00:00+00:00
- Summary model alias: None (`None` = deterministic `FakeSummarizer`, not model-generated)
- Compacted source turn IDs (6): ARTIFACT-TURN-00, ARTIFACT-TURN-01, ARTIFACT-TURN-02, ARTIFACT-TURN-03, ARTIFACT-TURN-04, ARTIFACT-TURN-05

## Context Size After Compaction

- `build_memory_context` included turns: 1
- Token count: 39 (≤ 40)
- Summary present: True
- The compacted source turns are not duplicated in the active context - they were removed from `recent_turns` when compacted and survive only via the summary's provenance above.

## Retrieved Evidence Remains Separate

- `SESSION MEMORY` prompt section present: True
- `RETRIEVED EVIDENCE` prompt section present: True
- The two sections are distinct (never merged): True
- Memory (including the compacted summary above) is never converted into an `EvidenceChunk` and never reaches citation validation as an evidence source (Task 7).

## Redaction Check

Confirmed programmatically (this script asserts each line below before writing the file):

- [x] raw turn content - absent
- [x] raw summary text - absent
