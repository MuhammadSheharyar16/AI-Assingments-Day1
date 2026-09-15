# Day 13 — Tool Registry Report

Generated 2026-09-15 by `scripts/day13_generate_tool_artifacts.py` from a real `ToolRegistry.load()` call (Task 2) against the real committed `tools\registry.v1.json`.

## Summary

- `registry_version`: `1.0`
- Total registered tool/version entries: **2**
- Active: **1**
- Disabled: **1**

## Owner / Risk / Side-Effect Summary

| tool_id | tool_version | status | owner | risk_level | side_effecting | idempotent |
|---|---|---|---|---|---|---|
| `supplier_status_lookup` | `1.0.0` | `active` | AICO Synthetic Procurement Platform | `low` | False | True |
| `supplier_record_update` | `1.0.0` | `disabled` | AICO Synthetic Procurement Platform | `high` | True | False |

## Invalid-Registry Rejection

A duplicate `(tool_id, tool_version)` entry (mirrors `registry_validation_cases.json` REG13-002), fed through the real `ToolRegistryDocument.model_validate()` validator (Task 1):

> Value error, duplicate tool/version: 'supplier_status_lookup'@'1.0.0'
