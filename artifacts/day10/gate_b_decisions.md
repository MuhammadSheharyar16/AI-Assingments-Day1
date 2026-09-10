# Day 10 — Gate-B Decisions

Generated 2026-09-10 by `scripts/day10_generate_gate_b_artifacts.py` from real `GateB.authorize()` calls (Task 3-12) against the real committed policy (policy_version `1.0`, `policy\gate_b_policy.v1.json`). Every `role`/`intent_id`/`lane` below is a governed identifier, never raw user content.

## Allowed Request

- Trusted role: `supplier_reader`
- Governed intent: `INT-POLICY-QUESTION`, lane: `rag`
- Requested data classification: `internal`
- `decision`: **allow**
- `rule_id`: `GB-R001`
- `reason_code`: `rule_allowed`
- `effective_scope_summary`: `tenants=1;data_classes=[internal];pii_categories=[contact,none]`
- `disclosure_profile`: `policy_reader`
- `policy_version`: `1.0`

## Denied Role (Matched, Allowed=False)

- Trusted role: `supplier_reader`
- Governed intent: `INT-STRUCTURED-LOOKUP`, lane: `mode_b`
- Requested data classification: `internal`
- `decision`: **deny**
- `rule_id`: `GB-R002`
- `reason_code`: `rule_denied`
- `effective_scope_summary`: `tenants=0;data_classes=[];pii_categories=[]`
- `disclosure_profile`: `None`
- `policy_version`: `1.0`

## Unknown Role

- Trusted role: `finance_manager`
- Governed intent: `INT-POLICY-QUESTION`, lane: `rag`
- Requested data classification: `internal`
- `decision`: **deny**
- `rule_id`: `None`
- `reason_code`: `unknown_role`
- `effective_scope_summary`: `tenants=0;data_classes=[];pii_categories=[]`
- `disclosure_profile`: `None`
- `policy_version`: `1.0`

## Lane Mismatch

- Trusted role: `sourcing_analyst`
- Governed intent: `INT-POLICY-QUESTION`, lane: `mode_b`
- Requested data classification: `internal`
- `decision`: **deny**
- `rule_id`: `None`
- `reason_code`: `no_matching_rule`
- `effective_scope_summary`: `tenants=0;data_classes=[];pii_categories=[]`
- `disclosure_profile`: `None`
- `policy_version`: `1.0`

## Missing Rule

- Trusted role: `supplier_reader`
- Governed intent: `INT-HELP`, lane: `safe_fast_path`
- `decision`: **deny**
- `rule_id`: `None`
- `reason_code`: `no_matching_rule`
- `effective_scope_summary`: `tenants=0;data_classes=[];pii_categories=[]`
- `disclosure_profile`: `None`
- `policy_version`: `1.0`

## Clarification Case

- Trusted role: `compliance_reviewer`
- Governed intent: `INT-STRUCTURED-LOOKUP`, lane: `mode_b`
- `decision`: **clarify**
- `rule_id`: `GB-R005`
- `reason_code`: `data_class_selection_required`
- `effective_scope_summary`: `tenants=1;data_classes=[];pii_categories=[]`
- `disclosure_profile`: `None`
- `policy_version`: `1.0`
