"""Golden test: the clean `ZomatoProvider` builds correct cards + session from REAL data.

Drives `ZomatoProvider` directly against the sanitized real capture
`fixtures/zomato/search_pizza.json`, asserting the cards + session LRU reflect the
real restaurant the live turn surfaced (Domino's, res_id 20324557). This validates
the clean execution-result -> card / session path against real provider responses.
"""

import asyncio
import json
from pathlib import Path

import pytest

from core.contracts.tool_result import ProviderResult, ToolResult
from core.providers.base import ExecCtx
from core.providers.plugins.zomato import ZomatoProvider

_BASE = "core.providers.mcp.mcp_base"  # before_run resolution lives in the shared base

pytestmark = pytest.mark.component

_FIXTURE = json.loads((Path(__file__).parent / "fixtures/zomato/search_pizza.json").read_text())
_MENU = json.loads((Path(__file__).parent / "fixtures/zomato/menu_listing.json").read_text())
_ADDR_ENVELOPE = next(
    s["result"] for s in _FIXTURE["steps"] if s["tool"] == "get_saved_addresses_for_user"
)


def _provider_result() -> ProviderResult:
    results = [
        ToolResult(
            tool=step["tool"],
            provider="zomato",
            ok=not (step["result"] or {}).get("is_error"),
            arguments=step.get("arguments") or {},
            data=step["result"],
        )
        for step in _FIXTURE["steps"]
    ]
    return ProviderResult(provider="zomato", results=results, ok=_FIXTURE["ok"])


def test_clean_provider_builds_restaurant_cards_from_real_data():
    cards = ZomatoProvider().build_attachments(_provider_result())
    assert cards, "expected at least one card from the real restaurant search"
    blob = json.dumps([c.to_wire() for c in cards])
    assert "20324557" in blob, "the real restaurant id from the live capture should appear"
    assert all(c.kind == "restaurant_cards" for c in cards)


def test_clean_provider_session_restaurants_round_trip():
    merged = ZomatoProvider().merge({}, _provider_result())
    restaurants = merged.get("session_restaurants") or []
    assert restaurants, "expected session_restaurants carried forward from real data"
    assert restaurants[0]["res_id"] == "20324557", "most-recent restaurant should lead the LRU"
    # Lean projection: only the planner-facing fields, no bulky items[]/images.
    assert all("items" not in r and "res_image" not in r for r in restaurants)


def _menu_result() -> ProviderResult:
    return ProviderResult(
        provider="zomato",
        results=[
            ToolResult(
                tool=_MENU["tool"],
                provider="zomato",
                ok=not (_MENU["result"] or {}).get("is_error"),
                arguments=_MENU.get("arguments") or {},
                data=_MENU["result"],
            )
        ],
    )


def test_clean_provider_builds_menu_cards_from_real_data():
    cards = ZomatoProvider().build_attachments(_menu_result())
    menu = [c for c in cards if c.kind == "menu_cards"]
    assert len(menu) == 1, "one menu_cards attachment from the real menu listing"
    wire = menu[0].to_wire()
    assert str(wire["res_id"]) == "22676334", "the real restaurant id should stamp the card"
    assert wire["restaurant_name"] == "Domino's Pizza"
    items = wire["items"]
    # Only the rich (partial_menu) rows render — 5 in the fixture — in visible order.
    assert len(items) == 5, "renders exactly the rich rows visible in item_mappings"
    first = items[0]
    assert first["name"] == "Classic Stuffed Garlic Bread", "visible (item_mappings) order is kept"
    assert first["variant_id"] == "v_1048395714" and first["price"] == 159
    assert first["veg_kind"] == "veg" and first["image_url"].startswith("https://")


def test_clean_provider_builds_session_menu_for_cart_by_name():
    # session_menu lets the planner pick a variant_id on a follow-up create_cart.
    menu_tr = ToolResult(
        tool="get_menu_items_listing",
        provider="zomato",
        ok=True,
        data=_MENU["result"],
    )
    merged = ZomatoProvider().merge({}, ProviderResult(provider="zomato", results=[menu_tr]))
    session_menu = merged.get("session_menu") or []
    garlic = next((e for e in session_menu if e["variant_id"] == "v_1048395714"), None)
    assert garlic is not None, "the real menu item should be cached for cart-by-name"
    assert garlic["item_name"] == "Classic Stuffed Garlic Bread" and garlic["price"] == 159
    assert garlic["res_id"] == "22676334" and garlic["item_id"] == "ctl_785568411"


def test_clean_provider_session_menu_includes_keyword_featured_items():
    # Featured items[] from a keyword search also feed session_menu, res_id-stamped.
    kw_tr = ToolResult(
        tool="get_restaurants_for_keyword",
        provider="zomato",
        ok=True,
        data=_FIXTURE["steps"][1]["result"],
    )
    merged = ZomatoProvider().merge({}, ProviderResult(provider="zomato", results=[kw_tr]))
    session_menu = merged.get("session_menu") or []
    assert session_menu, "keyword featured items should populate session_menu"
    # Every entry is stamped with the restaurant it belongs to (parity trap).
    assert all(e.get("res_id") for e in session_menu)


def test_clean_provider_session_lru_dedupes_and_caps():
    prior = [{"res_id": "20324557", "name": "Old Domino's"}, {"res_id": "999", "name": "Other"}]
    merged = ZomatoProvider().merge({"session_restaurants": prior}, _provider_result())
    restaurants = merged["session_restaurants"]
    ids = [r["res_id"] for r in restaurants]
    assert ids[0] == "20324557" and ids.count("20324557") == 1, "fresh hit dedupes the stale dup"
    assert restaurants[0]["name"] != "Old Domino's", "fresh entry overwrites the stale one"
    assert len(restaurants) == 8, "the LRU is capped at the remembered-restaurants limit"


def test_clean_provider_surfaces_session_addresses_from_real_data():
    merged = ZomatoProvider().merge({}, _provider_result())
    addresses = merged.get("session_addresses") or []
    assert addresses, "expected saved addresses surfaced from the real capture"
    assert all(a["address_id"] and a["label"] for a in addresses)


def test_before_run_prefers_request_address_id():
    ctx = ExecCtx(address_id="REQ123")
    asyncio.run(ZomatoProvider().before_run(session=None, ctx=ctx))
    assert ctx.placeholders["__ADDRESS_ID__"] == "REQ123"


def test_before_run_resolves_from_carried_session_addresses():
    ctx = ExecCtx(session={"session_addresses": [{"address_id": "CARR1", "label": "Home"}]})
    asyncio.run(ZomatoProvider().before_run(session=None, ctx=ctx))
    assert ctx.placeholders["__ADDRESS_ID__"] == "CARR1"


def test_before_run_auto_fetches_address_when_none_known(monkeypatch):
    async def fake_call_tool(session, name, arguments=None):
        assert name == "get_saved_addresses_for_user"
        return "RAW_MCP_RESULT"

    monkeypatch.setattr(f"{_BASE}.call_tool", fake_call_tool)
    monkeypatch.setattr(f"{_BASE}.tool_result_payload", lambda raw: _ADDR_ENVELOPE)
    ctx = ExecCtx()  # no request id, no carried addresses
    asyncio.run(ZomatoProvider().before_run(session=object(), ctx=ctx))
    assert ctx.placeholders.get("__ADDRESS_ID__"), "auto-fetch should learn an address id"


def test_before_run_auto_fetch_failure_is_safe(monkeypatch):
    async def boom(session, name, arguments=None):
        raise RuntimeError("mcp connection aborted")

    monkeypatch.setattr(f"{_BASE}.call_tool", boom)
    ctx = ExecCtx()
    asyncio.run(ZomatoProvider().before_run(session=object(), ctx=ctx))
    assert "__ADDRESS_ID__" not in ctx.placeholders


def test_clean_provider_session_lru_retains_prior_tail_under_cap():
    # A small fresh result (1 hit) leaves room, so prior entries survive in the tail.
    one_hit = ProviderResult(
        provider="zomato",
        results=[
            ToolResult(
                tool="get_restaurants_for_keyword",
                provider="zomato",
                ok=True,
                data={"data": {"results": [{"res_id": "111", "name": "Fresh Place"}]}},
            )
        ],
    )
    prior = [{"res_id": "999", "name": "Old Place"}]
    merged = ZomatoProvider().merge({"session_restaurants": prior}, one_hit)
    ids = [r["res_id"] for r in merged["session_restaurants"]]
    assert ids == ["111", "999"], "fresh leads, prior retained in the tail"
