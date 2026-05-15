from __future__ import annotations

from dataclasses import dataclass
import os
import re
import time
from typing import Any

from app.services.swiggy_mcp_executor import run_swiggy_tool_plan_sync
from app.services.zomato_mcp_executor import (
    _mcp_token_secret,
    _merge_mcp_token_secret,
    run_tool_plan_sync,
)


@dataclass(frozen=True)
class ToolProvider:
    name: str
    label: str
    tools: frozenset[str]
    mcp_url_env: str | None = None
    default_mcp_url: str | None = None
    auth_mode: str = "oauth"
    available: bool = True
    notes: str = ""
    custom: bool = False


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
            "get_addresses",
            "search_restaurants",
            "search_menu",
            "get_restaurant_menu",
            "get_food_cart",
            "update_food_cart",
            "flush_food_cart",
            "place_food_order",
            "fetch_food_coupons",
            "apply_food_coupon",
            "get_food_orders",
            "get_food_order_details",
            "track_food_order",
            "report_error",
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
            "get_addresses",
            "search_products",
            "your_go_to_items",
            "get_cart",
            "update_cart",
            "clear_cart",
            "checkout",
            "get_orders",
            "track_order",
            "report_error",
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

BUILTIN_PROVIDERS = (
    ZOMATO_PROVIDER,
    SWIGGY_FOOD_PROVIDER,
    SWIGGY_INSTAMART_PROVIDER,
    BLINKIT_PROVIDER,
    ZEPTO_PROVIDER,
)
PROVIDERS = BUILTIN_PROVIDERS

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


def _slugify_provider_id(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    if not slug:
        slug = "custom_mcp"
    if not slug.startswith("custom_"):
        slug = f"custom_{slug}"
    return slug[:48]


def _custom_provider_records(user_id: str | None = None) -> dict[str, dict[str, Any]]:
    payload = _mcp_token_secret(user_id)
    records = payload.get("custom_providers") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    return {
        str(pid): record
        for pid, record in records.items()
        if isinstance(record, dict)
    }


def _custom_providers(user_id: str | None = None) -> tuple[ToolProvider, ...]:
    providers: list[ToolProvider] = []
    for pid, record in _custom_provider_records(user_id).items():
        label = str(record.get("label") or pid).strip() or pid
        mcp_url = str(record.get("mcp_url") or "").strip()
        if not mcp_url:
            continue
        raw_tools = record.get("tools")
        tools = (
            frozenset(str(tool).strip() for tool in raw_tools if str(tool).strip())
            if isinstance(raw_tools, list)
            else frozenset()
        )
        auth_mode = str(record.get("auth_mode") or "").strip().lower()
        if auth_mode not in {"oauth", "url"}:
            auth_mode = "url" if "mcp.zapier.com" in mcp_url.lower() else "oauth"
        providers.append(
            ToolProvider(
                name=pid,
                label=label,
                tools=tools,
                default_mcp_url=mcp_url,
                auth_mode=auth_mode,
                available=True,
                notes=str(record.get("notes") or "Custom MCP added from the UI.").strip(),
                custom=True,
            )
        )
    return tuple(providers)


def all_tool_providers(user_id: str | None = None) -> tuple[ToolProvider, ...]:
    return BUILTIN_PROVIDERS + _custom_providers(user_id)


def save_custom_tool_provider(
    *,
    label: str,
    mcp_url: str,
    tools: list[str] | None = None,
    auth_mode: str | None = None,
    provider_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    clean_label = label.strip()
    clean_url = mcp_url.strip()
    if not clean_label:
        raise ValueError("MCP name is required.")
    if not clean_url.startswith(("https://", "http://")):
        raise ValueError("MCP URL must start with https:// or http://.")
    clean_auth_mode = (auth_mode or "").strip().lower()
    if not clean_auth_mode:
        clean_auth_mode = "url" if "mcp.zapier.com" in clean_url.lower() else "oauth"
    if clean_auth_mode not in {"oauth", "url"}:
        raise ValueError("MCP auth mode must be 'oauth' or 'url'.")
    pid = provider_id.strip() if provider_id else _slugify_provider_id(clean_label)
    builtin_ids = {provider.name for provider in BUILTIN_PROVIDERS}
    if pid in builtin_ids:
        pid = f"custom_{pid}"
    records = _custom_provider_records(user_id)
    base = pid
    counter = 2
    while pid in records and records[pid].get("mcp_url") != clean_url:
        pid = f"{base}_{counter}"
        counter += 1
    records[pid] = {
        "id": pid,
        "label": clean_label,
        "mcp_url": clean_url,
        "auth_mode": clean_auth_mode,
        "tools": sorted({str(tool).strip() for tool in (tools or []) if str(tool).strip()}),
        "notes": "Custom MCP added from the UI.",
        "updated_at": int(time.time()),
    }
    _merge_mcp_token_secret(user_id, custom_providers=records)
    return provider_to_dict(get_tool_provider(pid, user_id=user_id) or _custom_providers(user_id)[-1])


def update_custom_provider_tools(provider_id: str, tools: list[str], user_id: str | None = None) -> None:
    records = _custom_provider_records(user_id)
    record = records.get(provider_id)
    if not isinstance(record, dict):
        return
    record["tools"] = sorted({str(tool).strip() for tool in tools if str(tool).strip()})
    record["updated_at"] = int(time.time())
    records[provider_id] = record
    _merge_mcp_token_secret(user_id, custom_providers=records)


def delete_custom_tool_provider(provider_id: str, user_id: str | None = None) -> bool:
    records = _custom_provider_records(user_id)
    if provider_id not in records:
        return False
    records.pop(provider_id, None)
    _merge_mcp_token_secret(user_id, custom_providers=records)
    return True


def provider_to_dict(provider: ToolProvider) -> dict[str, Any]:
    return {
        "id": provider.name,
        "label": provider.label,
        "available": provider.available,
        "custom": provider.custom,
        "mcp_url": provider.default_mcp_url if provider.custom else None,
        "auth_mode": provider.auth_mode,
        "tool_count": len(provider.tools),
        "tools": sorted(provider.tools),
        "notes": provider.notes,
    }


def list_tool_providers(user_id: str | None = None) -> list[dict[str, Any]]:
    return [provider_to_dict(provider) for provider in all_tool_providers(user_id)]


def select_tool_providers_for_message(message: str, user_id: str | None = None) -> set[str]:
    text = f" {message.lower()} "
    providers = {provider.name: provider for provider in all_tool_providers(user_id) if provider.available}

    asks_compare = any(token in text for token in (" compare ", " vs ", " versus ", " cheaper ", " faster ", " better "))
    mentions_zomato = "zomato" in text
    mentions_swiggy = "swiggy" in text
    mentions_instamart = "instamart" in text
    mentions_zepto = "zepto" in text

    selected: set[str] = set()
    if asks_compare and (mentions_zomato or mentions_swiggy):
        if "zomato" in providers:
            selected.add("zomato")
        if "swiggy_food" in providers:
            selected.add("swiggy_food")
        if selected:
            return selected

    grocery_words = {
        "grocery",
        "groceries",
        "milk",
        "bread",
        "eggs",
        "egg",
        "atta",
        "rice",
        "dal",
        "vegetables",
        "fruits",
        "snacks",
        "household",
        "cart",
    }
    restaurant_words = {
        "restaurant",
        "restaurants",
        "pizza",
        "biryani",
        "dosa",
        "burger",
        "dinner",
        "lunch",
        "breakfast",
        "menu",
        "food",
        "cuisine",
    }

    if mentions_zepto and "zepto" in providers:
        selected.add("zepto")
    if mentions_instamart and "swiggy_instamart" in providers:
        selected.add("swiggy_instamart")
    elif mentions_swiggy and any(word in text for word in grocery_words) and "swiggy_instamart" in providers:
        selected.add("swiggy_instamart")
    elif mentions_swiggy and "swiggy_food" in providers:
        selected.add("swiggy_food")

    if mentions_zomato and "zomato" in providers:
        selected.add("zomato")

    if selected:
        return selected

    for provider in providers.values():
        if provider.custom and provider.label.lower() in text:
            selected.add(provider.name)
    if selected:
        return selected

    if any(word in text for word in grocery_words):
        if "zepto" in providers:
            return {"zepto"}
        if "swiggy_instamart" in providers:
            return {"swiggy_instamart"}

    if any(word in text for word in restaurant_words):
        if asks_compare and "zomato" in providers and "swiggy_food" in providers:
            return {"zomato", "swiggy_food"}
        if "zomato" in providers:
            return {"zomato"}
        if "swiggy_food" in providers:
            return {"swiggy_food"}

    if "zomato" in providers:
        return {"zomato"}
    return {next(iter(providers.keys()))} if providers else set()


def provider_for_tool(
    tool_name: str,
    enabled_providers: set[str] | None = None,
    user_id: str | None = None,
) -> str | None:
    providers = all_tool_providers(user_id)
    if enabled_providers is not None:
        providers = tuple(provider for provider in providers if provider.name in enabled_providers)
    for provider in providers:
        if tool_name in provider.tools:
            return provider.name
    return None


def get_tool_provider(name: str, user_id: str | None = None) -> ToolProvider | None:
    needle = name.strip()
    for provider in all_tool_providers(user_id):
        if provider.name == needle:
            return provider
    return None


def route_tool_plan(
    tool_plan: list[dict[str, Any]],
    *,
    enabled_providers: set[str] | None = None,
    user_id: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    routed: dict[str, list[dict[str, Any]]] = {}
    for step in tool_plan:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool") or "").strip()
        provider_hint = str(step.get("provider") or "").strip()
        provider = None
        if provider_hint and (enabled_providers is None or provider_hint in enabled_providers):
            hinted = get_tool_provider(provider_hint, user_id=user_id)
            if hinted and tool_name in hinted.tools:
                provider = hinted.name
        if provider is None:
            provider = provider_for_tool(
                tool_name,
                enabled_providers=enabled_providers,
                user_id=user_id,
            )
        if not provider and enabled_providers and len(enabled_providers) == 1:
            only = get_tool_provider(next(iter(enabled_providers)), user_id=user_id)
            if only and only.custom:
                provider = only.name
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
    user_id: str | None = None,
) -> dict[str, Any]:
    if not any(isinstance(step, dict) and str(step.get("tool") or "").strip() for step in tool_plan):
        return {
            "ok": False,
            "error": "no_tool_plan",
            "routed_providers": [],
            "provider_results": {},
        }

    routed = route_tool_plan(
        tool_plan,
        enabled_providers=enabled_providers,
        user_id=user_id,
    )
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
        provider = get_tool_provider(name, user_id=user_id)
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
            user_id=user_id,
        )

    routed_names = tuple(name for name in sorted(routed.keys()) if name != "zomato")
    for name in routed_names:
        if name not in routed:
            continue
        provider = get_tool_provider(name, user_id=user_id)
        if not provider or not provider.default_mcp_url:
            continue

        resolved_url = (
            os.getenv(provider.mcp_url_env, "").strip()
            if provider.mcp_url_env
            else ""
        ) or provider.default_mcp_url

        # Pre-execution token check for OAuth-backed providers. Zapier MCP URLs
        # carry their own secret and do not need mcp-remote token files.
        from app.services.zomato_mcp_executor import (
            get_mcp_token_status,
            repo_root_from_settings,
            restore_mcp_auth_tokens,
        )
        if provider.auth_mode != "url":
            token_status = get_mcp_token_status(
                repo_root_from_settings(), mcp_url=resolved_url, user_id=user_id
            )
            if not token_status["has_local_tokens"]:
                if token_status["has_secret_tokens"]:
                    restore_mcp_auth_tokens(repo_root_from_settings(), user_id=user_id)
                    token_status = get_mcp_token_status(
                        repo_root_from_settings(), mcp_url=resolved_url, user_id=user_id
                    )
                if not token_status["has_local_tokens"]:
                    provider_results[name] = {
                        "ok": False,
                        "error_detail": (
                            f"OAuth not connected for {provider.label}. "
                            f"Visit /api/mcp/oauth/start?provider={name} to authorize."
                        ),
                        "steps": [],
                        "diagnostics": token_status,
                    }
                    continue

        if name in ("swiggy_food", "swiggy_instamart"):
            provider_results[name] = run_swiggy_tool_plan_sync(
                routed[name],
                address_id=address_id,
                mcp_url=resolved_url,
                user_id=user_id,
            )
        else:
            provider_results[name] = run_tool_plan_sync(
                routed[name],
                address_id=address_id,
                mcp_url=resolved_url,
                user_id=user_id,
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
