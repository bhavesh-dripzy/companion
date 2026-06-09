"""Builtin tool catalog, seeded from the legacy provider definitions.

A migration bridge: it reproduces today's provider/tool surface (from
`app/services/tool_runtime.py` + the planner tool sets) so the registry, prompt
generation, and validation can be exercised before any plugin is ported. Each
plugin becomes the authoritative source of its tools in Phase 4 — params come
from MCP discovery / declarations then; here `params` is empty.
"""

from __future__ import annotations

from core.contracts.tool import AuthMode, ConfirmPolicy, Tool

# Real-money order tools (Kite): mandatory two-step confirmation.
_TWO_STEP = {
    "place_order",
    "modify_order",
    "cancel_order",
    "place_gtt_order",
    "modify_gtt_order",
    "delete_gtt_order",
}
# Purchasing / booking tools: one explicit user confirmation.
_SINGLE = {
    "checkout_cart",
    "place_food_order",
    "checkout",
    "create_order",
    "create_online_payment_order",
    "create_wallet_order",
    "create_upi_reserve_pay_order",
    "create_table_reservation_booking",
    "make_phone_call",
}
# Cart / account mutations (gated in prompts; flagged for the confirmation layer).
_MUTATING = (
    _TWO_STEP
    | _SINGLE
    | {
        "create_cart",
        "update_food_cart",
        "flush_food_cart",
        "update_cart",
        "clear_cart",
        "update_drop_zone",
        "select_store",
        "select_saved_address",
        "add_saved_address",
    }
)
# Placeholders a tool's result can supply downstream (representative subset).
_PLACEHOLDERS: dict[str, tuple[str, ...]] = {
    "get_saved_addresses_for_user": ("__ADDRESS_ID__",),
    "get_addresses": ("__ADDRESS_ID__",),
    "list_saved_addresses": ("__ADDRESS_ID__",),
    "get_restaurants_for_keyword": ("__RESTAURANT_ID__",),
    "search_restaurants": ("__RESTAURANT_ID__",),
    "create_cart": ("__CART_ID__",),
}
_ALIASES: dict[str, tuple[str, ...]] = {
    "get_addresses": ("get_user_addresses",),
    "search_menu": ("search_menu_items",),
}

# (provider_id, label, auth_mode, space-separated tool names)
_SPECS: tuple[tuple[str, str, AuthMode, str], ...] = (
    (
        "zomato",
        "Zomato MCP",
        AuthMode.OAUTH,
        "get_saved_addresses_for_user get_restaurants_for_keyword "
        "get_restaurant_menu_by_categories get_menu_items_listing create_cart "
        "get_cart_offers checkout_cart get_order_history get_order_tracking_info "
        "bind_user_number bind_user_number_verify_code",
    ),
    (
        "swiggy_food",
        "Swiggy Food MCP",
        AuthMode.OAUTH,
        "get_addresses search_restaurants search_menu get_restaurant_menu get_food_cart "
        "update_food_cart flush_food_cart place_food_order fetch_food_coupons "
        "apply_food_coupon get_food_orders get_food_order_details track_food_order report_error",
    ),
    (
        "swiggy_instamart",
        "Swiggy Instamart MCP",
        AuthMode.OAUTH,
        "get_addresses search_products your_go_to_items get_cart update_cart clear_cart "
        "checkout get_orders track_order report_error",
    ),
    (
        "zepto",
        "Zepto MCP",
        AuthMode.OAUTH,
        "search_products search_multiple_products get_product_details get_past_order_items "
        "zepto_shop get_location_serviceability list_saved_addresses select_saved_address "
        "add_saved_address update_drop_zone select_store view_cart update_cart get_user_details "
        "update_user_name list_order_history get_order_detail get_payment_methods create_order "
        "create_online_payment_order create_wallet_order create_upi_reserve_pay_order "
        "check_payment_status",
    ),
    (
        "district",
        "District MCP",
        AuthMode.OAUTH,
        "search_dining_restaurants get_dynamic_filters get_dining_restaurants_details "
        "get_table_reservation_slots create_table_reservation_booking get_booking_details "
        "get_dynamic_filters_movie search_movies search_theatres search_shows "
        "get_movie_critique_reviews get_dynamic_filters_event search_events "
        "search_event_venues search_artists",
    ),
    (
        "kite",
        "Zerodha Kite MCP",
        AuthMode.TOOL,
        "login search_instruments get_quotes get_ltp get_ohlc get_historical_data get_profile "
        "get_margins get_holdings get_positions get_mf_holdings get_orders get_trades "
        "get_order_history get_order_trades get_gtts place_order modify_order cancel_order "
        "place_gtt_order modify_gtt_order delete_gtt_order",
    ),
    (
        "daily_calorie_tracker",
        "Daily Calorie Tracker MCP",
        AuthMode.TOOL,
        "add_meal get_daily_summary get_weekly_report search_food",
    ),
    ("twilio", "Twilio Voice Calls", AuthMode.NONE, "make_phone_call get_call_status"),
)


def _confirm(name: str) -> ConfirmPolicy:
    if name in _TWO_STEP:
        return ConfirmPolicy.TWO_STEP
    if name in _SINGLE:
        return ConfirmPolicy.SINGLE
    return ConfirmPolicy.NEVER


def builtin_tools() -> list[Tool]:
    """Build the seed `Tool` list (params empty; plugins fill them in Phase 4)."""
    tools: list[Tool] = []
    for provider, _label, auth, names in _SPECS:
        for name in names.split():
            tools.append(
                Tool(
                    name=name,
                    provider=provider,
                    auth_mode=auth,
                    confirm=_confirm(name),
                    mutating=name in _MUTATING,
                    placeholder_outputs=_PLACEHOLDERS.get(name, ()),
                    aliases=_ALIASES.get(name, ()) if provider in ("swiggy_food", "zomato") else (),
                    source="mcp" if provider != "twilio" else "static",
                )
            )
    return tools


def builtin_labels() -> dict[str, str]:
    return {provider: label for provider, label, _auth, _names in _SPECS}
