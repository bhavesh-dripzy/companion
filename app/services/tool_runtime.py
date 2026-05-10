from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from app.services.zomato_mcp_executor import run_tool_plan_sync


@dataclass(frozen=True)
class ToolProvider:
    name: str
    label: str
    tools: frozenset[str]


ZOMATO_PROVIDER = ToolProvider(
    name="zomato",
    label="Zomato MCP",
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
            "tool_count": len(provider.tools),
            "tools": sorted(provider.tools),
        }
        for provider in PROVIDERS
    ]


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
    routed = route_tool_plan(tool_plan)
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
