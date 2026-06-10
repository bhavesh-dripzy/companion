"""Swiggy Food placeholder resolver (port of the legacy executor's bookkeeping).

Swiggy keys `search_restaurants` hits as `data.restaurants[].id` — too generic
for the shared alias walk — so in-plan `search_restaurants` → menu-tool chaining
needs a structured first-hit learn. The legacy executor also cleared the carried
`__RESTAURANT_ID__` whenever a fresh search ran (restaurant pivot) and refreshed
it when the planner pasted a literal id into a restaurant-scoped tool; without
those, menu/cart steps silently target the previous turn's restaurant.
"""

from __future__ import annotations

from typing import Any

from core.providers.mcp.placeholders import (
    DEFAULT_ALIASES,
    DEFAULT_ISSUERS,
    PlaceholderResolver,
)

_RESTAURANT_ALIASES = DEFAULT_ALIASES["__RESTAURANT_ID__"] + (
    "swiggy_restaurant_id",
    "swiggyRestaurantId",
    "store_id",
    "storeId",
)

_ALIASES = {**DEFAULT_ALIASES, "__RESTAURANT_ID__": _RESTAURANT_ALIASES}

_ISSUERS = {
    **DEFAULT_ISSUERS,
    "search_restaurants": ("__RESTAURANT_ID__",),
    "place_food_order": ("__ORDER_ID__",),
}

_RESTAURANT_ARG_KEYS = ("restaurantId", "restaurant_id", "restaurantIdOfAddedItem")

_RESTAURANT_SCOPED_TOOLS = frozenset(
    {
        "search_menu",
        "get_restaurant_menu",
        "get_restaurant_categories",
        "update_food_cart",
        "place_food_order",
    }
)


def _is_ad_row(row: dict[str, Any]) -> bool:
    """Promoted rows: Swiggy suffixes the name with '(Ad)' and/or sets ad flags."""
    name = str(row.get("name") or "").strip().lower()
    return (
        name.endswith("(ad)")
        or bool(row.get("promoted"))
        or bool(row.get("isAd"))
        or bool(row.get("adTrackingId"))
    )


def _first_restaurant_id(payload: dict[str, Any]) -> str | None:
    """First ORGANIC restaurant id from a non-error `search_restaurants` envelope.

    Dish rows (Swiggy's universal search mixes them into restaurants[] for dish
    queries) are skipped — learning a menu-item id as __RESTAURANT_ID__ would
    poison every downstream menu/cart step. Promoted '(Ad)' rows rank first but
    rarely match the query (a falooda search led with a biryani ad), so the id
    is learned from the first organic hit, falling back to an ad only when the
    page is all ads."""
    from core.providers.plugins.swiggy_food.cards import is_restaurant_row

    if not isinstance(payload, dict) or payload.get("is_error"):
        return None
    data = payload.get("data")
    if isinstance(data, dict) and not isinstance(data.get("restaurants"), list):
        inner = data.get("data")
        data = inner if isinstance(inner, dict) else data
    if not isinstance(data, dict) or not isinstance(data.get("restaurants"), list):
        return None
    first_ad: str | None = None
    for row in data["restaurants"]:
        if not is_restaurant_row(row):
            continue
        rid = row.get("id") or row.get("restaurant_id") or row.get("restaurantId")
        text = str(rid).strip() if rid is not None else ""
        if not text:
            continue
        if _is_ad_row(row):
            first_ad = first_ad or text
            continue
        return text
    return first_ad


class SwiggyFoodResolver(PlaceholderResolver):
    """Shared resolver plus Swiggy's restaurant-id learning and pivot rules."""

    def __init__(self) -> None:
        super().__init__(aliases=_ALIASES, issuers=_ISSUERS)

    def learn(self, values: dict[str, str], payload: dict[str, Any]) -> dict[str, str]:
        """Learn `__RESTAURANT_ID__` from the search envelope's first hit, then aliases."""
        learned: dict[str, str] = {}
        if "__RESTAURANT_ID__" not in values:
            rid = _first_restaurant_id(payload)
            if rid:
                values["__RESTAURANT_ID__"] = rid
                learned["__RESTAURANT_ID__"] = rid
        learned.update(super().learn(values, payload))
        return learned

    def learn_from_args(self, values: dict[str, str], tool: str, args: dict[str, Any]) -> None:
        """Treat a literal restaurant id on a restaurant-scoped tool as an explicit pivot."""
        if tool not in _RESTAURANT_SCOPED_TOOLS or not isinstance(args, dict):
            return
        for key in _RESTAURANT_ARG_KEYS:
            text = str(args.get(key) or "").strip()
            if not text or (text.startswith("__") and text.endswith("__")):
                continue
            values["__RESTAURANT_ID__"] = text
            return
