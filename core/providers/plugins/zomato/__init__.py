"""Zomato plugin (clean McpProvider).

Status (migration): the full RENDERING layer is ported clean — restaurant + menu
search cards (validated against real Zomato: golden fixtures + live read-only
runs) and cart + payment cards (built from pure functions of the create_cart /
checkout_cart envelopes; contract-tested against the executor's documented
shapes). The bootstrap swap LegacyMcpAdapter→ZomatoProvider is gated on the
SESSION layer — `session_menu` (the menu_index: variant→price/variants/addon
schema the planner needs to create_cart by item name on follow-ups). Until that
lands, bootstrap keeps the adapter so dev never regresses.
"""

from core.providers.plugins.zomato.provider import ZomatoProvider

__all__ = ["ZomatoProvider"]
