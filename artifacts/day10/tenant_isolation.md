# Day 10 — Tenant Isolation

Generated 2026-09-10 by `scripts/day10_generate_gate_b_artifacts.py` from real `ControlPlaneAnswerService.answer()` calls (Day 10 Task 13) wired to counting Model-Gateway/retriever fakes (Task 11's own instrumentation technique — no real network call), plus the underlying `GateB.authorize()` decisions (Task 3/5) shown in full. Trusted tenant in both cases: `TENANT-A`.

## Same-Tenant: Allowed

Requested tenant: `TENANT-A` (matches the trusted tenant).

- `decision`: **allow**
- `rule_id`: `GB-R001`
- `effective_tenant_scope`: `('TENANT-A',)`
- `effective_data_classes`: `('internal',)`
- `effective_pii_policy`: `('none', 'contact')`
- `disclosure_profile`: `policy_reader`
- Protected dependency calls: gateway=1, retriever=1 — reached exactly once each, confirming the counters below are actually wired in, not silently disconnected.

## Cross-Tenant: Denied

Requested tenant: `TENANT-B` (does **not** match the trusted tenant `TENANT-A`).

- `decision`: **deny**
- `reason_code`: `cross_tenant_denied`
- `effective_tenant_scope`: `()` (empty — nothing granted)
- `effective_data_classes`: `()` (empty)
- `disclosure_profile`: `None` (none)

## Proof: Zero Protected Calls On Cross-Tenant Denial

Model Gateway calls: **0**; retriever calls: **0** — **PASS**. The control is enforced *before* protected evidence access (Task 11): a denied cross-tenant request never reaches retrieval or the Model Gateway at all, proven by the counting fakes actually wired into the pipeline.
