"""
AICO platform package - the single boundary all chat and embedding traffic
crosses (Day 3).

- model_gateway.py  typed chat/embed contract application code depends on
- config.py         validated loading of config/model-routing.yaml
- errors.py         normalized error types the gateway raises
- foundry_adapter.py the one file that speaks HTTP to Microsoft Foundry

Nothing outside this package may import a provider SDK/HTTP client
directly - see docs/adr/ADR-003-model-routing-and-fallback.md.
"""
