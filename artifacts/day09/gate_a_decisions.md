# Day 9 — Gate-A Decisions

Generated 2026-09-09 by `scripts/day09_generate_control_plane_artifacts.py` from real `GateA.classify()` calls (Task 3/4/6) against the real committed registry (ontology_version `1.0`).

## Exact

- Input: `What are the payment terms?`
- `status`: **matched**
- `domain`: `supplier_governance`
- `intent_id`: `INT-POLICY-QUESTION`
- `matched_concepts`: ['CON-PAYMENT-TERMS']
- `reason_code`: `exact_phrase_match`
- `ontology_version`: `1.0`

## Synonym

- Input: `What is the vendor payment window?`
- `status`: **matched**
- `domain`: `supplier_governance`
- `intent_id`: `INT-POLICY-QUESTION`
- `matched_concepts`: ['CON-SUPPLIER', 'CON-PAYMENT-TERMS']
- `reason_code`: `concept_synonym_match`
- `ontology_version`: `1.0`

## Ambiguous

- Input: `Show me the supplier information.`
- `status`: **ambiguous**
- `domain`: `None`
- `intent_id`: `None`
- `matched_concepts`: ['CON-SUPPLIER']
- `candidate_intents`: ['INT-POLICY-QUESTION', 'INT-STRUCTURED-LOOKUP']
- `clarification_question`: Could you clarify which of these you mean: (1) Ask a factual question answered from supplier policy documents.; or (2) Request a governed structured-data lookup to be executed by a later Mode-B component.?
- `reason_code`: `ambiguous_multiple_intents`
- `ontology_version`: `1.0`

## Unsupported

- Input: `What is tomorrow's weather?`
- `status`: **unsupported**
- `domain`: `None`
- `intent_id`: `None`
- `matched_concepts`: []
- `reason_code`: `no_governed_match`
- `ontology_version`: `1.0`

## Memory-Assisted Follow-Up

- Input: `What about its invoice policy?`
- Session context: `previous_subject="Supplier Alpha"`, `previous_intent="INT-POLICY-QUESTION"`
- Resolved by `resolve_reference` (Task 8) to: `What about Supplier Alpha invoice policy?`
- `status`: **matched**
- `domain`: `supplier_governance`
- `intent_id`: `INT-POLICY-QUESTION`
- `matched_concepts`: ['CON-SUPPLIER']
- `reason_code`: `concept_synonym_match`
- `ontology_version`: `1.0`
