# ADR-0002: Unified tool schema + provider registry

- Status: accepted
- Date: 2026-06-08

## Context

The "tool schema" today is a plaintext markdown bullet list of tool names baked into planner prompts,
plus per-provider frozensets (`_ALLOWED_TOOLS`, `_ZEPTO_TOOLS`, …) and the `ToolProvider.tools`
sets in `tool_runtime.py`. Validation is name-only. Adding Twilio required editing four places
(provider registry, dispatch if/elif, orchestrator known-sets, a bespoke planner) and it still did
not fit the food-shaped signatures because it has no addresses/cart/menu state. This is the
"schema too strict" blocker (issue #27).

## Decision

Introduce one `Tool` Pydantic model (name, provider, description, JSON-Schema `params`, `auth_mode`,
`confirm`, `mutating`, `session_reads`, `placeholder_outputs`, `source`) that describes both
runtime-discovered MCP tools and statically-declared HTTP tools. A `ProviderRegistry` +
`ToolRegistry` is the single source of truth from which we derive: provider list, tool→provider
dispatch, the **generated** planner-prompt tools block, and the **jsonschema** argument validator.

A provider is added by implementing a plugin and adding one line to `core/registry/bootstrap.py`.
Stateless providers set `session_cls = None`, so Twilio no longer has to pretend to be food-shaped.

## Consequences

- The "edit 4 places" problem disappears; the dispatcher is registry-driven (no if/elif).
- Planner prompts shrink to a behavioural preamble with a generated tools block.
- A migration step seeds the registry from today's provider data to reproduce current behaviour
  before any provider is re-implemented.
