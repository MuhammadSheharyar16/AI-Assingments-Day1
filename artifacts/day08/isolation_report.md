# Day 8 — Isolation Report

Generated 2026-09-08 by `scripts/day08_generate_memory_artifacts.py` from real `MemorySessionService` calls (`src/aico/memory/service.py`) - the exact Task 3 case matrix. No turn/summary content appears anywhere in this file - see Redaction Check.

## Case Matrix

| Case | Requester | Session Owner | Outcome | Denial Reason |
|---|---|---|---|---|
| same-owner access | `TENANT-SYN-001/USER-SYN-001` | `TENANT-SYN-001/USER-SYN-001` | **allow** | (n/a) |
| cross-user, same tenant | `TENANT-SYN-001/USER-SYN-002` | `TENANT-SYN-001/USER-SYN-001` | **deny** | `not_found` |
| cross-tenant, same-looking user id | `TENANT-SYN-002/USER-SYN-001` | `TENANT-SYN-001/USER-SYN-001` | **deny** | `not_found` |
| different session, same owner | `TENANT-SYN-001/USER-SYN-001` | `TENANT-SYN-001/USER-SYN-001 (session SES-LZ8bclVk...)` | **allow, no cross-session content** | (n/a) |
| guessed/nonexistent session id | `TENANT-SYN-001/USER-SYN-001` | `(none)` | **deny** | `not_found` |

## Fail-Closed Confirmation

Denial for a wrong-owner request and denial for a nonexistent session id must be indistinguishable from outside the service - neither message reveals whether another tenant's session exists.

- Wrong-owner denial message: `session not found`
- Nonexistent-session denial message: `session not found`
- Identical: **True**

## Redaction Check

Confirmed programmatically (this script asserts each line below before writing the file):

- [x] raw turn content - absent (no turns were ever added to these sessions)
- [x] which tenant/session a denial actually refers to - never disclosed beyond the sanitized reason category
