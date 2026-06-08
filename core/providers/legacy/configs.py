"""Per-provider config for the LegacyMcpAdapter (migration bridge).

Each entry points at the provider's proven legacy executor + attachment
collectors and maps its session-slice keys to the executor's session_* kwargs.
This is the whole "port" for an MCP provider during migration — the executor and
card builders run unchanged, so behaviour is identical. Internals move into a
clean McpProvider per provider at cutover (Phase 8), validated with live fixtures.
"""

from __future__ import annotations

from core.contracts.tool import AuthMode
from core.providers.legacy.adapter import LegacyMcpConfig

_PIPE = "app.services.agents.pipeline"
_FOOD_SESSION = {
    "session_restaurants": "restaurants",
    "session_addresses": "addresses",
    "session_menu": "menu",
    "session_cart": "cart",
}

MCP_CONFIGS: tuple[LegacyMcpConfig, ...] = (
    LegacyMcpConfig(
        id="zomato",
        label="Zomato MCP",
        auth_mode=AuthMode.OAUTH,
        run_fn_path="app.services.zomato.mcp_executor:run_tool_plan_sync",
        url_env="ZOMATO_MCP_URL",
        default_url="https://mcp-server.zomato.com/mcp",
        session_map=_FOOD_SESSION,
        collector_paths=(
            f"{_PIPE}:_collect_payment_attachments",
            f"{_PIPE}:_collect_menu_attachments",
            f"{_PIPE}:_collect_restaurant_attachments",
            f"{_PIPE}:_collect_cart_attachments",
        ),
    ),
    LegacyMcpConfig(
        id="swiggy_food",
        label="Swiggy Food MCP",
        auth_mode=AuthMode.OAUTH,
        run_fn_path="app.services.swiggy.mcp_executor:run_swiggy_tool_plan_sync",
        url_env="SWIGGY_FOOD_MCP_URL",
        default_url="https://mcp.swiggy.com/food",
        session_map=_FOOD_SESSION,
        collector_paths=(
            f"{_PIPE}:_collect_swiggy_menu_attachments",
            f"{_PIPE}:_collect_swiggy_restaurant_attachments",
        ),
    ),
    LegacyMcpConfig(
        id="swiggy_instamart",
        label="Swiggy Instamart MCP",
        auth_mode=AuthMode.OAUTH,
        run_fn_path="app.services.instamart.mcp_executor:run_tool_plan_sync",
        url_env="SWIGGY_INSTAMART_MCP_URL",
        default_url="https://mcp.swiggy.com/im",
        session_map={
            "session_instamart_products": "products",
            "session_instamart_addresses": "addresses",
            "session_instamart_cart": "cart",
        },
        collector_paths=(f"{_PIPE}:_collect_instamart_attachments",),
    ),
    LegacyMcpConfig(
        id="zepto",
        label="Zepto MCP",
        auth_mode=AuthMode.OAUTH,
        run_fn_path="app.services.zepto.mcp_executor:run_zepto_tool_plan_sync",
        url_env="ZEPTO_MCP_URL",
        default_url="https://mcp.zepto.co.in/mcp",
        pass_address=False,
        session_map={
            "session_zepto_products": "products",
            "session_zepto_addresses": "addresses",
            "session_zepto_cart": "cart",
        },
        collector_paths=(f"{_PIPE}:_collect_zepto_attachments",),
    ),
    LegacyMcpConfig(
        id="district",
        label="District MCP",
        auth_mode=AuthMode.OAUTH,
        run_fn_path="app.services.district.mcp_executor:run_district_tool_plan_sync",
        url_env="DISTRICT_MCP_URL",
        default_url="https://mcp-server.district.in/mcp",
        pass_address=False,
        session_map={
            "session_restaurants": "restaurants",
            "session_movies": "movies",
            "session_theatres": "theatres",
            "session_events": "events",
            "session_venues": "venues",
            "session_artists": "artists",
            "session_reservation": "reservation",
        },
        collector_paths=(
            f"{_PIPE}:_collect_district_attachments",
            f"{_PIPE}:_collect_district_reservation_attachments",
        ),
    ),
    LegacyMcpConfig(
        id="kite",
        label="Zerodha Kite MCP",
        auth_mode=AuthMode.TOOL,
        run_fn_path="app.services.kite.mcp_executor:run_tool_plan_sync",
        url_env="KITE_MCP_URL",
        default_url="https://mcp.kite.trade/mcp",
        pass_address=False,
        pass_location=False,
        session_map={
            "session_kite_instruments": "instruments",
            "session_kite_holdings": "holdings",
            "session_kite_positions": "positions",
            "session_kite_open_orders": "open_orders",
            "session_kite_order_ticket": "order_ticket",
        },
        collector_paths=(f"{_PIPE}:_collect_kite_attachments",),
    ),
)
