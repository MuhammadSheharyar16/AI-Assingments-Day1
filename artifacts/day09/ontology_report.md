# Day 9 — Ontology Report

Generated 2026-09-09 by `scripts/day09_generate_control_plane_artifacts.py` from a real `OntologyRegistry.load()` call against the committed `ontology\registry.v1.json` (Task 1/2).

## Version

- `ontology_version`: **1.0**
- Registry path: `ontology\registry.v1.json`

## Domains

| domain_id | name | status |
|---|---|---|
| `supplier_governance` | Supplier Governance | active |

## Concepts

| concept_id | name | status |
|---|---|---|
| `CON-SUPPLIER` | supplier | active |
| `CON-PAYMENT-TERMS` | payment terms | active |
| `CON-INVOICE-POLICY` | invoice submission policy | active |
| `CON-CONTRACT` | contract | active |

## Intents

| intent_id | domain | allowed_lanes | status |
|---|---|---|---|
| `INT-POLICY-QUESTION` | `supplier_governance` | rag | active |
| `INT-STRUCTURED-LOOKUP` | `supplier_governance` | mode_b | active |
| `INT-HELP` | `supplier_governance` | safe_fast_path | active |

## Lanes

Enabled for this ontology version: `rag`, `mode_b`, `clarify`, `block`, `safe_fast_path`

## Validation Result

- PASS — registry loaded into typed OntologyDocument objects without error
- Counts: 1 domain(s), 4 concept(s), 3 intent(s), 5 lane(s) enabled.

## Invalid-Registry Rejection (proof)

Case: duplicate concept_id (`CON-SUPPLIER` appears twice).

Result: **rejected** — `pydantic.ValidationError`: `Value error, duplicate concept_id: 'CON-SUPPLIER'`
