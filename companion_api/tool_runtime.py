"""Tool-provider catalog + CRUD for the Settings UI (web/admin concern).

The provider catalog (built-in MCP providers the user can connect, plus their
per-user tool overrides and custom/Zapier MCPs) and the DynamoDB-backed CRUD
behind `/api/tools/providers/*` and the MCP setup routes. Relocated from
`app/services/tool_runtime.py` in the legacy teardown — only the CRUD/catalog
moved; the legacy turn-execution dispatch (`route_tool_plan` /
`execute_tool_plan_sync`) died with the legacy pipeline.

Per-user custom providers and built-in tool overrides persist in the same MCP
token secret as OAuth tokens, via `core.providers.mcp.token_store`.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, replace
from typing import Any

from core.providers.mcp.token_store import mcp_token_secret, merge_mcp_token_secret


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
            # Exactly the 10 tools the Instamart MCP currently exposes.
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
    notes=(
        "Swiggy's q-commerce (groceries) surface. Mutating tools "
        "(update_cart, clear_cart, checkout) are guarded by explicit user "
        "confirmation in the planner prompt; update_cart is always chained "
        "with get_cart so the cart card carries the authoritative bill."
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
            # Discovery
            "search_products",
            "search_multiple_products",
            "get_product_details",
            "get_past_order_items",
            "zepto_shop",
            # Location & store
            "get_location_serviceability",
            "list_saved_addresses",
            "select_saved_address",
            "add_saved_address",
            "update_drop_zone",
            "select_store",
            # Cart
            "view_cart",
            "update_cart",
            # User profile
            "get_user_details",
            "update_user_name",
            # Orders
            "list_order_history",
            "get_order_detail",
            # Payment & order placement
            "get_payment_methods",
            "create_order",
            "create_online_payment_order",
            "create_wallet_order",
            "create_upi_reserve_pay_order",
            "check_payment_status",
        }
    ),
    notes=(
        "Official Zepto MCP endpoint. Discovery, address/store, cart, "
        "and order/payment flows. Mutating tools (update_cart, address "
        "switches, order placement) are guarded by the planner prompt."
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
            # Auth
            "login",
            # Market data
            "search_instruments",
            "get_quotes",
            "get_ltp",
            "get_ohlc",
            "get_historical_data",
            # Account / portfolio (read)
            "get_profile",
            "get_margins",
            "get_holdings",
            "get_positions",
            "get_mf_holdings",
            # Orders & trades (read)
            "get_orders",
            "get_trades",
            "get_order_history",
            "get_order_trades",
            "get_gtts",
            # Mutating — real-money order placement / modification. Registered
            # (routable) but the planner prompt gates every one behind
            # mandatory two-step user confirmation; the executor never
            # auto-emits them.
            "place_order",
            "modify_order",
            "cancel_order",
            "place_gtt_order",
            "modify_gtt_order",
            "delete_gtt_order",
        }
    ),
    notes=(
        "Hosted Zerodha Kite MCP endpoint — a REAL-MONEY trading surface. "
        "Read-only/account-inspection tools run freely; mutating order tools "
        "(place/modify/cancel order + GTTs) are guarded by mandatory two-step "
        "confirmation in the planner prompt and are never auto-called by the "
        "executor."
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

DISTRICT_PROVIDER = ToolProvider(
    name="district",
    label="District MCP",
    mcp_url_env="DISTRICT_MCP_URL",
    default_mcp_url="https://mcp-server.district.in/mcp",
    tools=frozenset(
        {
            # Dining (table reservations)
            "search_dining_restaurants",
            "get_dynamic_filters",
            "get_dining_restaurants_details",
            "get_table_reservation_slots",
            "create_table_reservation_booking",
            "get_booking_details",
            # Movies / shows
            "get_dynamic_filters_movie",
            "search_movies",
            "search_theatres",
            "search_shows",
            "get_movie_critique_reviews",
            # Events / venues / artists
            "get_dynamic_filters_event",
            "search_events",
            "search_event_venues",
            "search_artists",
        }
    ),
    notes=(
        "District dining table reservations, movie/theatre/show discovery, "
        "and event/venue/artist discovery."
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

TWILIO_PROVIDER = ToolProvider(
    name="twilio",
    label="Twilio Voice Calls",
    mcp_url_env="TWILIO_MCP_URL",
    default_mcp_url="https://twilio.local/mcp",
    auth_mode="tool",
    tools=frozenset(
        {
            "make_phone_call",
            "get_call_status",
        }
    ),
    notes=(
        "Outbound voice calls via Twilio with dynamic TTS messages "
        "(Amazon Polly Aditi voice, Hinglish-capable). "
        "Credentials in AWS Secrets Manager (companion/twilio-credentials). "
        "All calls require explicit user confirmation before execution."
    ),
)

BUILTIN_PROVIDERS = (
    ZOMATO_PROVIDER,
    SWIGGY_FOOD_PROVIDER,
    SWIGGY_INSTAMART_PROVIDER,
    DISTRICT_PROVIDER,
    BLINKIT_PROVIDER,
    ZEPTO_PROVIDER,
    KITE_PROVIDER,
    GROWW_PROVIDER,
    CALORIE_PROVIDER,
    TWILIO_PROVIDER,
)


def _slugify_provider_id(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    if not slug:
        slug = "custom_mcp"
    if not slug.startswith("custom_"):
        slug = f"custom_{slug}"
    return slug[:48]


def _custom_provider_records(user_id: str | None = None) -> dict[str, dict[str, Any]]:
    payload = mcp_token_secret(user_id)
    records = payload.get("custom_providers") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    return {str(pid): record for pid, record in records.items() if isinstance(record, dict)}


def _builtin_provider_tool_records(user_id: str | None = None) -> dict[str, list[str]]:
    payload = mcp_token_secret(user_id)
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
        providers.append(replace(provider, tools=frozenset(tools)) if tools else provider)
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
    merge_mcp_token_secret(user_id, custom_providers=records)
    return provider_to_dict(
        get_tool_provider(pid, user_id=user_id) or _custom_providers(user_id)[-1]
    )


def update_custom_provider_tools(
    provider_id: str, tools: list[str], user_id: str | None = None
) -> None:
    records = _custom_provider_records(user_id)
    record = records.get(provider_id)
    if not isinstance(record, dict):
        return
    record["tools"] = sorted({str(tool).strip() for tool in tools if str(tool).strip()})
    record["updated_at"] = int(time.time())
    records[provider_id] = record
    merge_mcp_token_secret(user_id, custom_providers=records)


def update_tool_provider_tools(
    provider_id: str, tools: list[str], user_id: str | None = None
) -> None:
    builtin_ids = {provider.name for provider in BUILTIN_PROVIDERS}
    if provider_id not in builtin_ids:
        update_custom_provider_tools(provider_id, tools, user_id=user_id)
        return
    records = _builtin_provider_tool_records(user_id)
    records[provider_id] = sorted({str(tool).strip() for tool in tools if str(tool).strip()})
    merge_mcp_token_secret(user_id, builtin_provider_tools=records)


def delete_custom_tool_provider(provider_id: str, user_id: str | None = None) -> bool:
    records = _custom_provider_records(user_id)
    if provider_id not in records:
        return False
    records.pop(provider_id, None)
    merge_mcp_token_secret(user_id, custom_providers=records)
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


def get_tool_provider(name: str, user_id: str | None = None) -> ToolProvider | None:
    needle = name.strip()
    for provider in all_tool_providers(user_id):
        if provider.name == needle:
            return provider
    return None
