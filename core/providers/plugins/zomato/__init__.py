"""Zomato plugin (clean McpProvider) — the sole Zomato path.

The full rendering layer is validated against real Zomato data: restaurant + menu
search cards (golden fixtures + live read-only runs) and cart + payment cards
(pure functions of the create_cart / checkout_cart envelopes, contract-tested
against the documented shapes). Follow-up create_cart-by-item-name uses the
provider's `session_menu` menu_index (variant→price/variants/addon schema).
"""

from core.providers.plugins.zomato.provider import ZomatoProvider

__all__ = ["ZomatoProvider"]
