"""Placeholder substitution, learning, and staleness clearing for MCP plans.

Ported from `zomato.mcp_executor`. The planner emits opaque IDs as placeholders
(`__CART_ID__`, ...) that one step learns from a prior step's response. The
resolver substitutes them before a call, reports unresolved ones, learns new
values from a response, and clears stale values when a tool re-issues a resource.
"""

from __future__ import annotations

import re
from typing import Any

# Any surviving __TOKEN__ after substitution is an unresolved placeholder —
# alias-independent, so ids the resolver can't learn still block the call
# instead of leaking literal placeholder strings to the server.
_PLACEHOLDER_TOKEN = re.compile(r"__[A-Z][A-Z0-9_]*__")

# placeholder -> response field names that carry its value (dual-key aware).
DEFAULT_ALIASES: dict[str, tuple[str, ...]] = {
    "__ADDRESS_ID__": ("address_id", "addressId"),
    "__RESTAURANT_ID__": ("restaurant_id", "restaurantId", "res_id", "resId", "restaurantIdString"),
    "__CART_ID__": ("cart_id", "cartId"),
    "__ORDER_ID__": ("order_id", "orderId"),
}

# Tools that mint a fresh value for a placeholder — any value carried from a
# previous turn is stale and must be cleared so the new response is re-learned.
DEFAULT_ISSUERS: dict[str, tuple[str, ...]] = {
    "get_restaurants_for_keyword": ("__RESTAURANT_ID__",),
    "create_cart": ("__CART_ID__",),
    "checkout_cart": ("__ORDER_ID__",),
    "place_order": ("__ORDER_ID__",),
}


def _walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk(value)


class PlaceholderResolver:
    """Substitute / learn / clear placeholders. Plugins may pass custom maps."""

    def __init__(
        self,
        aliases: dict[str, tuple[str, ...]] | None = None,
        issuers: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.aliases = aliases or DEFAULT_ALIASES
        self.issuers = issuers or DEFAULT_ISSUERS

    def substitute(self, obj: Any, values: dict[str, str]) -> Any:
        if isinstance(obj, str) and obj in values:
            return values[obj]
        if isinstance(obj, dict):
            return {k: self.substitute(v, values) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.substitute(v, values) for v in obj]
        return obj

    def unresolved(self, obj: Any) -> list[str]:
        found: list[str] = []
        if isinstance(obj, str) and _PLACEHOLDER_TOKEN.fullmatch(obj):
            return [obj]
        if isinstance(obj, dict):
            for value in obj.values():
                found.extend(self.unresolved(value))
        elif isinstance(obj, list):
            for value in obj:
                found.extend(self.unresolved(value))
        return sorted(set(found))

    def learn_from_args(self, values: dict[str, str], tool: str, args: dict[str, Any]) -> None:
        """Hook: harvest placeholder values from a step's literal arguments (no-op by default)."""

    def learn(self, values: dict[str, str], payload: dict[str, Any]) -> dict[str, str]:
        """Harvest first-seen placeholder values from a tool's response data."""
        learned: dict[str, str] = {}
        data = payload.get("data") if isinstance(payload, dict) else None
        for obj in _walk(data):
            if not isinstance(obj, dict):
                continue
            for placeholder, aliases in self.aliases.items():
                if placeholder in values or placeholder in learned:
                    continue
                for alias in aliases:
                    text = str(obj.get(alias) or "").strip()
                    if text:
                        learned[placeholder] = text
                        break
        values.update(learned)
        return learned

    def clear_stale(self, values: dict[str, str], tool: str) -> None:
        """Drop placeholders a tool re-issues so the new response is re-learned."""
        for placeholder in self.issuers.get(tool, ()):
            values.pop(placeholder, None)
