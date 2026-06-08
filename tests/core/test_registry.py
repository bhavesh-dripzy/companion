"""Unit tests for the provider/tool registry."""

import pytest

from core.contracts.tool import AuthMode, ConfirmPolicy, Tool
from core.contracts.tool_call import ToolCall
from core.registry import (
    UNSUPPORTED,
    ProviderRegistry,
    ToolRegistry,
    build_registry,
    default_registry,
)
from core.registry.seed import builtin_tools

pytestmark = pytest.mark.unit


def test_builtin_catalog_reproduces_providers():
    reg = build_registry()
    providers = set(reg.tools.providers())
    assert {
        "zomato",
        "swiggy_food",
        "swiggy_instamart",
        "zepto",
        "district",
        "kite",
        "twilio",
    } <= providers
    assert len(reg.tools.for_provider("zomato")) == 11
    # HTTP plugins (maps/calendar/uber/whatsapp) add their tools on top of the seed.
    assert len(reg.tools) >= len(builtin_tools())
    assert {"maps", "calendar", "uber", "whatsapp"} <= providers


def test_tool_lookup_scoped_by_provider():
    reg = build_registry()
    # get_order_history is owned by both zomato and kite; provider scoping disambiguates.
    assert reg.tools.get("get_order_history", provider="kite").provider == "kite"
    assert reg.tools.get("get_order_history", provider="zomato").provider == "zomato"


def test_alias_resolves_to_canonical():
    reg = build_registry()
    assert reg.tools.get("get_user_addresses").name == "get_addresses"
    assert reg.tools.get("search_menu_items").name == "search_menu"


def test_confirm_policies():
    reg = build_registry()
    assert reg.tools.get("place_order", provider="kite").confirm is ConfirmPolicy.TWO_STEP
    assert reg.tools.get("checkout_cart").confirm is ConfirmPolicy.SINGLE
    assert reg.tools.get("search_restaurants").confirm is ConfirmPolicy.NEVER


def test_twilio_is_stateless_static():
    reg = build_registry()
    tool = reg.tools.get("make_phone_call")
    assert tool.provider == "twilio"
    assert tool.auth_mode is AuthMode.NONE
    assert tool.source == "static"


def test_route_uses_provider_hint_then_owner():
    tools = ToolRegistry()
    tools.register(Tool(name="search_restaurants", provider="zomato"))
    tools.register(Tool(name="make_phone_call", provider="twilio"))
    reg = ProviderRegistry(tools=tools)

    grouped = reg.route(
        [
            ToolCall(tool="search_restaurants"),  # owner = zomato
            ToolCall(tool="make_phone_call"),  # owner = twilio
            ToolCall(tool="unknown_tool"),  # -> unsupported
        ]
    )
    assert grouped["zomato"][0].tool == "search_restaurants"
    assert grouped["twilio"][0].tool == "make_phone_call"
    assert UNSUPPORTED in grouped


def test_route_invalid_hint_falls_back_to_owner():
    reg = build_registry()
    # A hint that isn't a registered provider is ignored in favour of the owner.
    grouped = reg.route([ToolCall(tool="search_restaurants", provider="bogus")])
    assert "swiggy_food" in grouped


def test_default_registry_is_singleton():
    assert default_registry() is default_registry()
