from __future__ import annotations

from dataclasses import dataclass, replace
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
    stdio_command: str | None = None
    stdio_args: tuple[str, ...] = ()


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

KITE_PROVIDER = ToolProvider(
    name="kite",
    label="Zerodha Kite MCP",
    mcp_url_env="KITE_MCP_URL",
    default_mcp_url="https://mcp.kite.trade/mcp",
    auth_mode="tool",
    tools=frozenset(
        {
            "login",
            "search_instruments",
            "get_quotes",
            "get_ltp",
            "get_ohlc",
            "get_historical_data",
            "get_profile",
            "get_margins",
            "get_holdings",
            "get_positions",
            "get_mf_holdings",
            "get_orders",
            "get_trades",
            "get_order_history",
            "get_order_trades",
            "get_gtts",
        }
    ),
    notes=(
        "Hosted Zerodha Kite MCP endpoint. Companion exposes read-only/account "
        "inspection tools by default; trading tools are intentionally not routed."
    ),
)

GROWW_PROVIDER = ToolProvider(
    name="groww",
    label="Groww MCP",
    mcp_url_env="GROWW_MCP_URL",
    default_mcp_url="https://mcp.groww.in/mcp",
    tools=frozenset(),
    notes=(
        "Official Groww MCP endpoint. It requires Groww OAuth before tools can "
        "be discovered; connect and test this provider to cache its tool schema."
    ),
)

CALORIE_PROVIDER = ToolProvider(
    name="daily_calorie_tracker",
    label="Daily Calorie Tracker MCP",
    auth_mode="tool",
    tools=frozenset(
        {
            "add_meal",
            "get_daily_summary",
            "get_weekly_report",
            "search_food",
        }
    ),
    notes=(
        "Local stdio MCP for calorie tracking. Companion stores its SQLite "
        "database per user in Secrets Manager between Lambda cold starts."
    ),
    stdio_command="node",
    stdio_args=("node_modules/daily-calorie-tracker-mcp/dist/index.js",),
)

BUILTIN_PROVIDERS = (
    ZOMATO_PROVIDER,
    SWIGGY_FOOD_PROVIDER,
    SWIGGY_INSTAMART_PROVIDER,
    BLINKIT_PROVIDER,
    ZEPTO_PROVIDER,
    KITE_PROVIDER,
    GROWW_PROVIDER,
    CALORIE_PROVIDER,
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


def _builtin_provider_tool_records(user_id: str | None = None) -> dict[str, list[str]]:
    payload = _mcp_token_secret(user_id)
    records = payload.get("builtin_provider_tools") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    out: dict[str, list[str]] = {}
    builtin_ids = {provider.name for provider in BUILTIN_PROVIDERS}
    for pid, tools in records.items():
        if str(pid) not in builtin_ids or not isinstance(tools, list):
            continue
        clean = sorted({str(tool).strip() for tool in tools if str(tool).strip()})
        if clean:
            out[str(pid)] = clean
    return out


def _builtin_providers(user_id: str | None = None) -> tuple[ToolProvider, ...]:
    tool_overrides = _builtin_provider_tool_records(user_id)
    providers: list[ToolProvider] = []
    for provider in BUILTIN_PROVIDERS:
        tools = tool_overrides.get(provider.name)
        providers.append(
            replace(provider, tools=frozenset(tools)) if tools else provider
        )
    return tuple(providers)


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
        if auth_mode not in {"oauth", "url", "tool"}:
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
    return _builtin_providers(user_id) + _custom_providers(user_id)


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
    if clean_auth_mode not in {"oauth", "url", "tool"}:
        raise ValueError("MCP auth mode must be 'oauth', 'url', or 'tool'.")
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


def update_tool_provider_tools(provider_id: str, tools: list[str], user_id: str | None = None) -> None:
    builtin_ids = {provider.name for provider in BUILTIN_PROVIDERS}
    if provider_id not in builtin_ids:
        update_custom_provider_tools(provider_id, tools, user_id=user_id)
        return
    records = _builtin_provider_tool_records(user_id)
    records[provider_id] = sorted({str(tool).strip() for tool in tools if str(tool).strip()})
    _merge_mcp_token_secret(user_id, builtin_provider_tools=records)


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
        "transport": "stdio" if provider.stdio_command else "http",
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
    mentions_kite = "kite" in text or "zerodha" in text
    mentions_groww = "groww" in text
    mentions_calories = "calorie" in text or "calories" in text

    finance_words = {
        "stock",
        "stocks",
        "share",
        "shares",
        "portfolio",
        "holdings",
        "holding",
        "position",
        "positions",
        "orders",
        "trades",
        "margin",
        "margins",
        "ltp",
        "quote",
        "quotes",
        "ohlc",
        "candle",
        "candles",
        "nifty",
        "sensex",
        "f&o",
        "fo",
        "option",
        "options",
        "equity",
        "demat",
        "kite",
        "zerodha",
        "groww",
    }
    health_words = {
        "calorie",
        "calories",
        "ate",
        "eaten",
        "nutrition",
        "protein",
        "diet",
    }
    meal_log_words = {" log ", " logged ", " track ", " tracked ", " record ", " recorded "}
    meal_type_words = {" breakfast ", " lunch ", " dinner ", " snack ", " meal "}

    if mentions_calories and "daily_calorie_tracker" in providers:
        return {"daily_calorie_tracker"}
    if any(word in text for word in health_words) and "daily_calorie_tracker" in providers:
        return {"daily_calorie_tracker"}
    if (
        any(token in text for token in meal_log_words)
        and any(token in text for token in meal_type_words)
        and "daily_calorie_tracker" in providers
    ):
        return {"daily_calorie_tracker"}

    if mentions_kite and "kite" in providers:
        return {"kite"}
    if mentions_groww and "groww" in providers:
        return {"groww"}
    if any(word in text for word in finance_words):
        if "kite" in providers:
            return {"kite"}
        if "groww" in providers:
            return {"groww"}

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
        # We used to refuse to substitute the first address if a different location was requested.
        # Now we let it proceed to run_tool_plan_sync, which will try to prefetch and resolve.
        # The response LLM will handle the result (success or skipped tools).
        provider_results["zomato"] = run_tool_plan_sync(
            routed["zomato"],
            address_id=address_id,
            user_id=user_id,
            location_text=requested_location,
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

        if provider.stdio_command:
            from app.services.stdio_mcp_executor import run_stdio_tool_plan_sync

            provider_results[name] = run_stdio_tool_plan_sync(
                routed[name],
                command=provider.stdio_command,
                args=list(provider.stdio_args),
                user_id=user_id,
            )
            continue

        # Pre-execution token check for OAuth-backed providers. Zapier MCP URLs
        # carry their own secret; Kite exposes a provider-side login tool instead
        # of relying on mcp-remote token files.
        from app.services.zomato_mcp_executor import (
            get_mcp_token_status,
            repo_root_from_settings,
            restore_mcp_auth_tokens,
        )
        if provider.auth_mode == "oauth":
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

        if name == "swiggy_food":
            provider_results[name] = run_swiggy_tool_plan_sync(
                routed[name],
                address_id=address_id,
                mcp_url=resolved_url,
                user_id=user_id,
                location_text=requested_location,
            )
        elif name == "swiggy_instamart":
            from app.services.instamart_mcp_executor import run_tool_plan_sync as run_instamart_plan

            provider_results[name] = run_instamart_plan(
                routed[name],
                address_id=address_id,
                mcp_url=resolved_url,
                user_id=user_id,
                location_text=requested_location,
            )
        else:
            provider_results[name] = run_tool_plan_sync(
                routed[name],
                address_id=address_id,
                mcp_url=resolved_url,
                user_id=user_id,
                location_text=requested_location,
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
