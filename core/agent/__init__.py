"""Companion agent — the rebuilt turn pipeline.

Thin Router → domain-shaped DomainHandler → LLM Resolver (+ deterministic validation gate)
→ per-provider CartStrategy → ProviderAdapter (+ Normalizer) → kept MCP layer. The LLM
(Sarvam) only routes / resolves / narrates; provider execution is deterministic. Adding a
provider = one adapter + its cart strategy + error table, registered in `registry.py`.

See ARCHITECTURE_REBUILD.md / ARCHITECTURE_RELAY.md.
"""
