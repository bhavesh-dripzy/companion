from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Any

from app.services.zomato_mcp_executor import run_tool_plan_sync


@dataclass(frozen=True)
class ToolProvider:
    name: str
    label: str
    tools: frozenset[str]
    mcp_url_env: str | None = None
    default_mcp_url: str | None = None
    available: bool = True
    notes: str = ""


ZOMATO_PROVIDER = ToolProvider(
    name="zomato",
    label="Zomato MCP",
    mcp_url_env="ZOMATO_MCP_URL",
    default_mcp_url="https://mcp-server.zomato.com/mcp",
    tools=frozenset(
        {
            "get_saved_addresses_for_user",
            "get_restaurants_for_keyword",
            "get_restaurant_menu_by_categories",
            "get_menu_items_listing",
            "create_cart",
            "get_cart_offers",
            "checkout_cart",
            "get_order_history",
            "get_order_tracking_info",
            "bind_user_number",
            "bind_user_number_verify_code",
        }
    ),
)

SWIGGY_FOOD_PROVIDER = ToolProvider(
    name="swiggy_food",
    label="Swiggy Food MCP",
    mcp_url_env="SWIGGY_FOOD_MCP_URL",
    default_mcp_url="https://mcp.swiggy.com/food",
    tools=frozenset(
        {
            "search_restaurants",
            "get_restaurant_menu",
            "search_menu_items",
            "get_user_addresses",
            "get_cart",
            "add_item_to_cart",
            "update_cart_item",
            "remove_cart_item",
            "clear_cart",
            "get_cart_offers",
            "place_order",
            "get_order_history",
            "get_order_details",
        }
    ),
)

SWIGGY_INSTAMART_PROVIDER = ToolProvider(
    name="swiggy_instamart",
    label="Swiggy Instamart MCP",
    mcp_url_env="SWIGGY_INSTAMART_MCP_URL",
    default_mcp_url="https://mcp.swiggy.com/im",
    tools=frozenset(
        {
            "search_products",
            "get_product_details",
            "get_user_addresses",
            "get_cart",
            "add_item_to_cart",
            "update_cart_item",
            "remove_cart_item",
            "clear_cart",
            "get_cart_offers",
            "place_order",
            "get_order_history",
            "get_order_details",
        }
    ),
)

BLINKIT_PROVIDER = ToolProvider(
    name="blinkit",
    label="Blinkit MCP",
    tools=frozenset(),
    available=False,
    notes="No verified public Blinkit MCP endpoint/tool schema is configured yet.",
)

ZEPTO_PROVIDER = ToolProvider(
    name="zepto",
    label="Zepto MCP",
    mcp_url_env="ZEPTO_MCP_URL",
    default_mcp_url="https://mcp.zepto.co.in/mcp",
    tools=frozenset(
        {
            "search_products",
            "get_product_details",
            "get_user_preferences",
            "get_cart",
            "add_item_to_cart",
            "update_cart_item",
            "remove_cart_item",
            "clear_cart",
            "get_cart_offers",
            "checkout",
            "place_order",
            "get_order_history",
            "get_order_details",
        }
    ),
    notes=(
        "Official Zepto MCP endpoint. Tool names are mapped to the public shopping "
        "flows documented by Zepto: search, cart, checkout, and order history."
    ),
)

PROVIDERS = (
    ZOMATO_PROVIDER,
    SWIGGY_FOOD_PROVIDER,
    SWIGGY_INSTAMART_PROVIDER,
    BLINKIT_PROVIDER,
    ZEPTO_PROVIDER,
)

_SAVED_LOCATION_WORDS = {
    "me",
    "my",
    "nearby",
    "current",
    "saved",
    "home",
    "office",
    "work",
    "place",
    "address",
}


def list_tool_providers() -> list[dict[str, Any]]:
    return [
        {
            "id": provider.name,
            "label": provider.label,
            "available": provider.available,
            "tool_count": len(provider.tools),
            "tools": sorted(provider.tools),
            "notes": provider.notes,
        }
        for provider in PROVIDERS
    ]


def provider_for_tool(tool_name: str, enabled_providers: set[str] | None = None) -> str | None:
    providers = PROVIDERS
    if enabled_providers is not None:
        providers = tuple(provider for provider in PROVIDERS if provider.name in enabled_providers)
    for provider in providers:
        if tool_name in provider.tools:
            return provider.name
    return None


def get_tool_provider(name: str) -> ToolProvider | None:
    needle = name.strip()
    for provider in PROVIDERS:
        if provider.name == needle:
            return provider
    return None


def route_tool_plan(
    tool_plan: list[dict[str, Any]],
    *,
    enabled_providers: set[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    routed: dict[str, list[dict[str, Any]]] = {}
    for step in tool_plan:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool") or "").strip()
        provider = provider_for_tool(tool_name, enabled_providers=enabled_providers)
        if not provider:
            routed.setdefault("_unsupported", []).append(step)
            continue
        routed.setdefault(provider, []).append(step)
    return routed


def _requires_zomato_address(tool_plan: list[dict[str, Any]]) -> bool:
    for step in tool_plan:
        if not isinstance(step, dict):
            continue
        if step.get("tool") != "get_restaurants_for_keyword":
            continue
        args = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
        address_id = str(args.get("address_id") or "").strip()
        if not address_id or address_id == "__ADDRESS_ID__":
            return True
    return False


def _explicit_location_from_message(message: str) -> str | None:
    text = " ".join(message.lower().split())
    if not text:
        return None
    match = re.search(
        r"\b(?:near|in|at|around|close to|nearby)\s+([a-z0-9 ,.'-]{3,80})",
        text,
    )
    if not match:
        return None
    location = match.group(1)
    location = re.split(r"\b(?:for|with|under|below|above|rating|rated|open|fast|veg|non veg)\b", location)[0]
    words = {w.strip(" ,.'-") for w in location.split() if w.strip(" ,.'-")}
    if not words or words & _SAVED_LOCATION_WORDS:
        return None
    return location.strip(" ,.'-") or None


def _requested_location_from_constraints(constraints: dict[str, Any] | None) -> str | None:
    if not isinstance(constraints, dict):
        return None
    for key in ("requested_location_text", "requested_location", "location_text", "area"):
        val = constraints.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def execute_tool_plan_sync(
    tool_plan: list[dict[str, Any]],
    *,
    address_id: str | None = None,
    user_message: str = "",
    constraints: dict[str, Any] | None = None,
    enabled_providers: set[str] | None = None,
) -> dict[str, Any]:
    routed = route_tool_plan(tool_plan, enabled_providers=enabled_providers)
    unsupported = routed.pop("_unsupported", [])
    if enabled_providers is not None:
        disabled = sorted(set(routed.keys()) - enabled_providers)
        if disabled:
            return {
                "ok": False,
                "error": "provider_not_enabled",
                "disabled_providers": disabled,
                "routed_providers": sorted(routed.keys()),
                "provider_results": {},
            }
    provider_results: dict[str, Any] = {}

    for name in sorted(routed.keys()):
        provider = get_tool_provider(name)
        if provider and not provider.available:
            provider_results[name] = {
                "ok": False,
                "error_detail": "provider_unavailable",
                "steps": [],
                "diagnostics": {"provider": provider.label, "notes": provider.notes},
            }
            return {
                "ok": False,
                "error": "provider_unavailable",
                "routed_providers": sorted(provider_results.keys()),
                "provider_results": provider_results,
            }

    if unsupported:
        return {
            "ok": False,
            "error": "unsupported_tools",
            "unsupported_tools": [
                str(step.get("tool") or "") for step in unsupported if isinstance(step, dict)
            ],
            "routed_providers": sorted(routed.keys()),
            "provider_results": provider_results,
        }

    if "zomato" in routed:
        requested_location = (
            _requested_location_from_constraints(constraints)
            or _explicit_location_from_message(user_message)
        )
        if requested_location and not address_id and _requires_zomato_address(routed["zomato"]):
            provider_results["zomato"] = {
                "ok": False,
                "error_detail": "zomato_requires_saved_address_for_requested_location",
                "steps": [],
                "diagnostics": {
                    "requested_location": requested_location,
                    "reason": (
                        "The current Zomato MCP restaurant search requires a saved delivery "
                        "address_id. Refusing to substitute the first saved address for a "
                        "different requested locality."
                    ),
                },
            }
            return {
                "ok": False,
                "error": "zomato_requires_saved_address_for_requested_location",
                "routed_providers": sorted(provider_results.keys()),
                "provider_results": provider_results,
            }
        provider_results["zomato"] = run_tool_plan_sync(
            routed["zomato"],
            address_id=address_id,
        )

    for name in ("swiggy_food", "swiggy_instamart", "zepto"):
        if name not in routed:
            continue
        provider = get_tool_provider(name)
        if not provider or not provider.default_mcp_url:
            continue
        provider_results[name] = run_tool_plan_sync(
            routed[name],
            address_id=address_id,
            mcp_url=(
                os.getenv(provider.mcp_url_env, "").strip()
                if provider.mcp_url_env
                else ""
            )
            or provider.default_mcp_url,
        )

    ok = all(
        isinstance(result, dict) and result.get("ok") is True
        for result in provider_results.values()
    )
    return {
        "ok": ok,
        "error": None if ok else "provider_execution_failed",
        "routed_providers": sorted(provider_results.keys()),
        "provider_results": provider_results,
    }
