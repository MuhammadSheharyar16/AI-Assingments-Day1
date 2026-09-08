# Day 8 — Session Lifecycle

Generated 2026-09-08 by `scripts/day08_generate_memory_artifacts.py` from real `MemorySessionService`/`SessionStore` calls and one real two-turn conversation driven through the actual `POST /ask` endpoint (fake Model Gateway/retriever - no real network call). Every identity/session id below is synthetic; no raw turn content appears anywhere in this file - see Redaction Check.

## Session Creation

- Session ID: `SES-qqiGQ-OGvSFm8ixGHxje1y_jwDOZh-vc`
- Owner: `TENANT-SYN-001/USER-SYN-001`
- Version: 1
- Created at: 2026-09-08T14:00:13.397481+00:00
- Expires at: 2026-09-08T14:30:13.397481+00:00
- Recent turns: 0, summary: none

## Two-Turn Follow-up (driven through `POST /ask`)

| Turn | Status | Session ID | Citation count | Confidence |
|---|---|---|---:|---|
| 1 | `answered` | `SES-nvL8eDNfs86iFsdyzlBKFleYpv4imy_r` | 1 | high |
| 2 | `answered` | `SES-nvL8eDNfs86iFsdyzlBKFleYpv4imy_r` | 1 | high |

Both turns share one session id (`SES-nvL8eDNfs86iFsdyzlBKFleYpv4imy_r`) - the second turn's request supplied the first turn's `session_id` and the response echoed the identical value back, confirming session continuity across the follow-up.

## Session Load

- Session ID: `SES-nvL8eDNfs86iFsdyzlBKFleYpv4imy_r`
- Version after two turns: 3
- Recent turns after two turns: 4 (one user + one assistant turn per answered request)

## Expiry

- Session ID: `SES-S8PydNBdbw7PkbnxNVL8YFcr8Y-5wFjI` (TTL 60s, deterministic injected clock advanced 61s)
- Load after TTL elapsed: denied, reason=`expired`
- No stale context was returned - the store's own ownership+expiry-scoped lookup refused it before any content could reach a caller.

## Clear / Reset

- Session ID: `SES-nvL8eDNfs86iFsdyzlBKFleYpv4imy_r`
- Before clear: version=3, recent_turns=4
- After clear: version=4, recent_turns=0, summary_present=False
- The session itself remains reloadable (clear is reset, not delete) - only its conversational state was removed.

## Redaction Check

Confirmed programmatically (this script asserts each line below before writing the file):

- [x] raw turn question/answer text - absent
- [x] authorization claims - absent
- [x] secrets/tokens - absent
