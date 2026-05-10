from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.zomato_mcp_executor import run_tool_plan_sync


@dataclass(frozen=True)
class ToolProvider:
    name: str
    tools: frozenset[str]


ZOMATO_PROVIDER = ToolProvider(
    name="zomato",
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

PROVIDERS = (ZOMATO_PROVIDER,)


def provider_for_tool(tool_name: str) -> str | None:
    for provider in PROVIDERS:
        if tool_name in provider.tools:
            return provider.name
    return None


def route_tool_plan(tool_plan: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    routed: dict[str, list[dict[str, Any]]] = {}
    for step in tool_plan:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool") or "").strip()
        provider = provider_for_tool(tool_name)
        if not provider:
            routed.setdefault("_unsupported", []).append(step)
            continue
        routed.setdefault(provider, []).append(step)
    return routed


def execute_tool_plan_sync(
    tool_plan: list[dict[str, Any]],
    *,
    address_id: str | None = None,
) -> dict[str, Any]:
    routed = route_tool_plan(tool_plan)
    unsupported = routed.pop("_unsupported", [])
    provider_results: dict[str, Any] = {}

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
        provider_results["zomato"] = run_tool_plan_sync(
            routed["zomato"],
            address_id=address_id,
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
