"""Golden test: the clean `ZeptoProvider` builds product cards from REAL data.

Drives the provider against a sanitized real capture (search "milk", live
serviceability→search on admin_bhavesh), asserting the structured product cards +
LRU reflect the real product the live turn surfaced (Frubon HiLo, pvid ecfaa892…)
and that paise→rupee normalization is applied (5800 paise → ₹58.0).
"""

import json
from pathlib import Path

import pytest

from core.contracts.tool_result import ProviderResult, ToolResult
from core.providers.plugins.zepto import ZeptoProvider

pytestmark = pytest.mark.component

_FIX = json.loads((Path(__file__).parent / "fixtures/zepto/search_milk.json").read_text())
_PVID = "ecfaa892-af50-420a-baaa-d1c139a7d218"


def _result() -> ProviderResult:
    return ProviderResult(
        provider="zepto",
        results=[
            ToolResult(
                tool=_FIX["tool"],
                provider="zepto",
                ok=not (_FIX["result"] or {}).get("is_error"),
                arguments=_FIX.get("arguments") or {},
                data=_FIX["result"],
            )
        ],
    )


def test_builds_structured_product_cards_from_real_data():
    cards = ZeptoProvider().build_attachments(_result())
    assert len(cards) == 1
    wire = cards[0].to_wire()
    assert wire["kind"] == "zepto_product_cards" and wire["provider"] == "zepto"
    frubon = next(p for p in wire["products"] if p["product_variant_id"] == _PVID)
    assert frubon["name"].startswith("Frubon HiLo")
    assert frubon["price_inr"] == 58.0 and frubon["price_text"] == "₹58", "paise→rupee normalized"
    assert frubon["store_product_id"] and frubon["query"] == "milk"


def test_text_only_reply_yields_no_cards():
    err = ProviderResult(
        provider="zepto",
        results=[
            ToolResult(
                tool="search_products",
                provider="zepto",
                ok=True,
                data={"is_error": False, "data": {"raw_text": "Error: Store not selected."}},
            )
        ],
    )
    assert ZeptoProvider().build_attachments(err) == []


def test_builds_cart_card_and_session_cart():
    env = {
        "is_error": False,
        "data": {
            "items": [
                {
                    "productVariantId": _PVID,
                    "storeProductId": "sp1",
                    "name": "Frubon HiLo Milk",
                    "quantity": 2,
                    "price": 5800,
                    "packSize": "500 ml X 2",
                }
            ],
            "isEmpty": False,
        },
    }
    tr = ToolResult(tool="view_cart", provider="zepto", ok=True, data=env)
    provider = ZeptoProvider()
    result = ProviderResult(provider="zepto", results=[tr])
    cards = [c for c in provider.build_attachments(result) if c.kind == "zepto_cart_card"]
    assert len(cards) == 1
    cart = cards[0].to_wire()["zepto_cart"]
    assert cart["items"][0]["name"] == "Frubon HiLo Milk" and cart["items"][0]["price_inr"] == 58.0
    assert cart["grand_total_paise"] == 11600  # 5800 paise x 2
    assert provider.merge({}, result)["session_zepto_cart"]["items"]


def test_cart_card_always_carries_delivery_address():
    """The Zepto cart card ships with the committed delivery address (resolved this
    turn) + the saved-address list so the UI can always show where it delivers."""
    env = {
        "is_error": False,
        "data": {
            "items": [
                {"productVariantId": _PVID, "storeProductId": "sp1", "name": "Milk", "quantity": 1}
            ],
            "isEmpty": False,
        },
    }
    tr = ToolResult(tool="view_cart", provider="zepto", ok=True, data=env)
    provider = ZeptoProvider()
    result = ProviderResult(provider="zepto", results=[tr])
    result.diagnostics["resolved_zepto_address_id"] = "ZHOME"
    result.diagnostics["saved_address_rows"] = [{"address_id": "ZHOME", "label": "Home"}]
    card = next(c for c in provider.build_attachments(result) if c.kind == "zepto_cart_card")
    wire = card.to_wire()
    assert wire["delivery_address_id"] == "ZHOME"
    assert wire["delivery_addresses"][0]["id"] == "ZHOME"


def test_session_products_lru_keyed_by_variant_id():
    merged = ZeptoProvider().merge({}, _result())
    products = merged.get("session_zepto_products") or []
    ids = [p["product_variant_id"] for p in products]
    assert _PVID in ids and len(ids) == len(set(ids)), "deduped variant LRU"


# ── cart hardening + location anchor (POC-verified Zepto contract) ─────────────

from core.providers.base import ExecCtx  # noqa: E402
from core.providers.mcp.mcp_base import McpProvider  # noqa: E402


def _ctx(**kw) -> ExecCtx:
    ctx = ExecCtx(user_id="u1")
    ctx.placeholders["__ZEPTO_DEVICE_ID__"] = "dev-123"
    for k, v in kw.items():
        setattr(ctx, k, v)
    return ctx


def test_effective_geo_prefers_saved_location_over_live_gps():
    ctx = _ctx(
        user_geo={"latitude": 1.0, "longitude": 2.0},
        session={"selected_zepto_location": {"latitude": 12.93, "longitude": 77.62}},
    )
    assert ZeptoProvider._effective_geo(ctx) == (12.93, 77.62)
    # Falls back to live GPS when nothing saved.
    assert ZeptoProvider._effective_geo(_ctx(user_geo={"latitude": 1.0, "longitude": 2.0})) == (
        1.0,
        2.0,
    )
    assert ZeptoProvider._effective_geo(_ctx()) is None


def test_update_cart_forces_device_id_and_strips_replace_cart():
    provider = ZeptoProvider()
    ctx = _ctx(scratch={"_zepto_valid_ids": {"pv1": "sp1"}})
    args = {
        "deviceId": "planner-guess",
        "replaceCart": True,
        "cartItems": [{"productVariantId": "pv1", "storeProductId": "WRONG", "quantity": 2}],
    }
    provider.before_call("update_cart", args, ctx)
    assert args["deviceId"] == "dev-123"
    assert "replaceCart" not in args
    # spid repaired from the validated map (planner sent WRONG).
    assert args["cartItems"] == [
        {"productVariantId": "pv1", "storeProductId": "sp1", "quantity": 2}
    ]


def test_update_cart_drops_fabricated_pvid_but_keeps_removal():
    provider = ZeptoProvider()
    ctx = _ctx(scratch={"_zepto_valid_ids": {"pv1": "sp1"}})
    args = {
        "cartItems": [
            {"productVariantId": "pv1", "storeProductId": "sp1", "quantity": 1},
            {"productVariantId": "FAKE", "storeProductId": "x", "quantity": 3},  # invented → drop
            {"productVariantId": "pv9", "storeProductId": "sp9", "quantity": 0},  # removal → keep
        ]
    }
    provider.before_call("update_cart", args, ctx)
    pvids = {i["productVariantId"]: i["quantity"] for i in args["cartItems"]}
    assert pvids == {"pv1": 1, "pv9": 0}, "fabricated add dropped; qty=0 removal preserved"


def test_harvest_collects_pvid_spid_from_search():
    provider = ZeptoProvider()
    ctx = _ctx()
    provider.harvest("search_products", _FIX["result"], ctx)
    valid = ctx.scratch.get("_zepto_valid_ids") or {}
    assert valid.get(_PVID), "search results feed the valid pvid->spid map"


def test_emptied_cart_clears_session_snapshot():
    """Regression: an emptied/cleared Zepto cart must clear the session snapshot
    (structured isEmpty or prose 'cart is empty'), not keep showing stale items."""
    provider = ZeptoProvider()
    for env in (
        {"is_error": False, "data": {"items": [], "isEmpty": True}},
        {"is_error": False, "data": "🛒 Your cart is empty."},
    ):
        tr = ToolResult(tool="view_cart", provider="zepto", ok=True, data=env)
        result = ProviderResult(provider="zepto", results=[tr])
        merged = provider.merge(
            {"session_zepto_cart": {"items": [{"product_variant_id": "X", "quantity": 2}]}}, result
        )
        assert merged["session_zepto_cart"]["items"] == [], env


# ── address switch sticks (regression: switch bound the store but reverted) ────


def test_explicit_address_switch_recorded_and_recommitted():
    """When the planner switches to a CONCRETE saved address, the provider records
    it (before_call) so run() surfaces it and merge re-commits the session's
    delivery address — without this the switch binds the store this turn but
    reverts to the old committed address next turn (mirrors Instamart)."""
    prov = ZeptoProvider()
    ctx = _ctx(
        session={"selected_zepto_address_id": "HOME"},
        scratch={
            "zepto_store_registered": True,
            "resolved_zepto_address_id": "HOME",
            "saved_address_rows": [
                {"address_id": "HOME", "label": "Home"},
                {"address_id": "OFFICE", "label": "Office"},
            ],
        },
    )
    # The switch must NOT be short-circuited — it has to bind the new store.
    out = prov.before_call("select_saved_address", {"addressId": "OFFICE"}, ctx)
    assert out is None
    assert ctx.scratch["zepto_switched_address_id"] == "OFFICE"
    assert ctx.scratch["resolved_zepto_address_id"] == "OFFICE"
    # merge re-commits it (surfaced via diagnostics by run()).
    result = ProviderResult(provider="zepto", results=[])
    result.diagnostics["switched_address_id"] = "OFFICE"
    assert (
        prov.merge({"selected_zepto_address_id": "HOME"}, result)["selected_zepto_address_id"]
        == "OFFICE"
    )


def test_redundant_select_saved_address_is_short_circuited():
    """Re-binding the already-selected address is redundant (before_run did it) —
    short-circuit the round-trip, and do NOT record it as a switch."""
    prov = ZeptoProvider()
    ctx = _ctx(
        scratch={"zepto_store_registered": True, "resolved_zepto_address_id": "HOME"},
    )
    out = prov.before_call("select_saved_address", {"addressId": "HOME"}, ctx)
    assert isinstance(out, dict) and out["is_error"] is False
    assert "zepto_switched_address_id" not in ctx.scratch


def test_unknown_address_id_is_not_treated_as_a_switch():
    """A concrete id that is NOT one of the user's saved addresses is a planner
    fabrication — never record it as a switch (don't re-commit a bogus address)."""
    prov = ZeptoProvider()
    ctx = _ctx(
        scratch={
            "zepto_store_registered": True,
            "resolved_zepto_address_id": "HOME",
            "saved_address_rows": [{"address_id": "HOME", "label": "Home"}],
        },
    )
    prov.before_call("select_saved_address", {"addressId": "MADE_UP"}, ctx)
    assert "zepto_switched_address_id" not in ctx.scratch


def test_run_surfaces_switch_and_reclassifies_empty_cart_error(monkeypatch):
    """run(): an errored view_cart that reports an empty cart is reclassified to a
    successful empty cart (not 'technical issue'), and a recorded switch is
    surfaced via diagnostics for merge to re-commit."""
    import asyncio

    async def fake_run(self, calls, ctx):
        ctx.scratch["zepto_switched_address_id"] = "OFFICE"
        ctx.scratch["resolved_zepto_address_id"] = "OFFICE"
        empty = ToolResult(
            tool="view_cart", provider="zepto", ok=False, error_detail="Your cart is empty."
        )
        return ProviderResult(provider="zepto", results=[empty])

    monkeypatch.setattr(McpProvider, "run", fake_run)
    prov = ZeptoProvider()
    # An anchor is committed so the needs_location guard passes.
    result = asyncio.run(prov.run([], ExecCtx(session={"selected_zepto_address_id": "OFFICE"})))
    cart_tr = next(tr for tr in result.results if tr.tool == "view_cart")
    assert cart_tr.ok and cart_tr.error_detail is None
    assert cart_tr.data["data"]["isEmpty"] is True
    assert result.diagnostics["switched_address_id"] == "OFFICE"


def test_update_cart_all_fabricated_is_short_circuited():
    """When every planner item is a fabricated pvid (none valid at this store),
    reconcile empties cartItems and before_call returns a synthetic no-op rather
    than forwarding a bad payload to Zepto."""
    prov = ZeptoProvider()
    ctx = _ctx(scratch={"_zepto_valid_ids": {"pv1": "sp1"}})
    args = {"cartItems": [{"productVariantId": "FAKE", "storeProductId": "x", "quantity": 2}]}
    out = prov.before_call("update_cart", args, ctx)
    assert isinstance(out, dict) and out["is_error"] is False
    assert args["cartItems"] == []


def test_cart_cards_dedupe_update_then_view_to_single_card():
    """update_cart then view_cart in one turn describe the same final cart — render
    exactly one card (no duplicates), the last successful one (mirrors Instamart)."""
    env = {
        "is_error": False,
        "data": {
            "items": [
                {"productVariantId": _PVID, "storeProductId": "sp1", "name": "Milk", "quantity": 1}
            ],
            "isEmpty": False,
        },
    }
    upd = ToolResult(tool="update_cart", provider="zepto", ok=True, data=env)
    view = ToolResult(tool="view_cart", provider="zepto", ok=True, data=env)
    result = ProviderResult(provider="zepto", results=[upd, view])
    cards = [c for c in ZeptoProvider().build_attachments(result) if c.kind == "zepto_cart_card"]
    assert len(cards) == 1
    assert cards[0].to_wire()["source"] == "zepto.view_cart"  # last wins


# ── stale committed address (deleted/renamed on the Zepto app) self-heal ───────


def test_before_run_reconciles_committed_address_deleted_on_app(monkeypatch):
    """The 'address changed outside chat' case: a committed address absent from the
    live saved-address list was deleted/renamed on the Zepto app. before_run must
    NOT pin it (no dead 'Delivering to'), fall back to GPS, and flag it as stale."""
    import asyncio

    async def fake_call_tool(session, name, arguments=None):
        # list_saved_addresses returns a list that NO LONGER contains GONE.
        return "RAW"

    monkeypatch.setattr("core.providers.plugins.zepto.provider.call_tool", fake_call_tool)
    monkeypatch.setattr("core.providers.plugins.zepto.provider.tool_result_payload", lambda raw: {})
    monkeypatch.setattr(
        "core.providers.plugins.zepto.provider.extract_zepto_address_rows",
        lambda payload: [{"address_id": "HOME", "label": "Home"}],
    )
    ctx = ExecCtx(
        session={"selected_zepto_address_id": "GONE"},
        user_geo={"latitude": 12.9, "longitude": 77.6},
    )
    asyncio.run(ZeptoProvider().before_run(session=object(), ctx=ctx))
    assert ctx.scratch["zepto_stale_committed_address"] == "GONE"
    # Did NOT resolve/pin the dead id; fell back to GPS for store binding.
    assert ctx.scratch.get("resolved_zepto_address_id") != "GONE"
    assert ctx.placeholders.get("__ZEPTO_ADDRESS_ID__") != "GONE"
    assert ctx.placeholders.get("__USER_LAT__") == 12.9


def test_merge_clears_stale_committed_address():
    """A stale committed address flagged this turn is cleared from the session so we
    stop re-committing a dead id."""
    result = ProviderResult(provider="zepto", results=[])
    result.diagnostics["stale_committed_address"] = "GONE"
    merged = ZeptoProvider().merge({"selected_zepto_address_id": "GONE"}, result)
    assert "selected_zepto_address_id" not in merged


def test_valid_committed_address_is_not_flagged_stale(monkeypatch):
    """A committed address still present in the live list must bind normally and
    never be flagged stale (no false positive)."""
    import asyncio

    bound: list = []

    async def fake_call_tool(session, name, arguments=None):
        bound.append((name, arguments))
        return "RAW"

    monkeypatch.setattr("core.providers.plugins.zepto.provider.call_tool", fake_call_tool)
    monkeypatch.setattr("core.providers.plugins.zepto.provider.tool_result_payload", lambda raw: {})
    monkeypatch.setattr(
        "core.providers.plugins.zepto.provider.extract_zepto_address_rows",
        lambda payload: [{"address_id": "HOME", "label": "Home"}],
    )
    ctx = ExecCtx(session={"selected_zepto_address_id": "HOME"})
    asyncio.run(ZeptoProvider().before_run(session=object(), ctx=ctx))
    assert "zepto_stale_committed_address" not in ctx.scratch
    assert ctx.scratch["resolved_zepto_address_id"] == "HOME"
    assert ("select_saved_address", {"addressId": "HOME"}) in bound


# ── long multi-turn conversation: end-to-end session-state evolution ───────────


def _cart_env(items: list[tuple[str, str, int]]) -> dict:
    """A structured view_cart/update_cart envelope from (pvid, name, qty) tuples."""
    return {
        "is_error": False,
        "data": {
            "items": [
                {
                    "productVariantId": pvid,
                    "storeProductId": f"sp-{pvid}",
                    "name": name,
                    "quantity": qty,
                    "price": 5000,
                }
                for pvid, name, qty in items
            ],
            "isEmpty": not items,
        },
    }


def _cart_result(items, *, diagnostics=None) -> ProviderResult:
    upd = ToolResult(tool="update_cart", provider="zepto", ok=True, data=_cart_env(items))
    view = ToolResult(tool="view_cart", provider="zepto", ok=True, data=_cart_env(items))
    r = ProviderResult(provider="zepto", results=[upd, view])
    for k, v in (diagnostics or {}).items():
        r.diagnostics[k] = v
    return r


def _cart_pvids(session: dict) -> dict[str, int]:
    cart = session.get("session_zepto_cart") or {}
    return {i["product_variant_id"]: i["quantity"] for i in cart.get("items") or []}


def test_long_conversation_end_to_end_session_evolution():
    """A realistic long Zepto conversation, threading the session dict turn-by-turn
    through merge() + before_call(), asserting cart, committed address, and product
    LRU stay correct across: onboard → search → add → add again → change qty →
    remove → switch address → address deleted on app → empty cart → re-add."""
    prov = ZeptoProvider()
    session: dict = {}

    # Turn 1 — onboard + search "vegetables": before_run resolved HOME, search fills LRU.
    r1 = _result()  # search milk fixture (a real structured search)
    r1.diagnostics["resolved_zepto_address_id"] = "HOME"
    r1.diagnostics["saved_address_rows"] = [
        {"address_id": "HOME", "label": "Home"},
        {"address_id": "OFFICE", "label": "Office"},
    ]
    session = prov.merge(session, r1)
    assert session["selected_zepto_address_id"] == "HOME", "committed seeded from resolved"
    assert any(p["product_variant_id"] == _PVID for p in session["session_zepto_products"])
    assert [r["address_id"] for r in session["session_zepto_addresses"]] == ["HOME", "OFFICE"]

    # Turn 2 — add Tomato. before_call reconcile validates the pvid + forces deviceId.
    ctx = _ctx(session=session, scratch={"_zepto_valid_ids": {"TOM": "sp-TOM"}})
    add_args = {"cartItems": [{"productVariantId": "TOM", "quantity": 1}]}
    assert prov.before_call("update_cart", add_args, ctx) is None
    assert add_args["cartItems"][0]["storeProductId"] == "sp-TOM"
    assert add_args["deviceId"] == "dev-123"
    session = prov.merge(session, _cart_result([("TOM", "Tomato", 1)]))
    assert _cart_pvids(session) == {"TOM": 1}
    assert session["selected_zepto_address_id"] == "HOME", "committed unchanged by a cart op"

    # Turn 3 — add Onion (Zepto cart merges server-side; cart now has both).
    session = prov.merge(session, _cart_result([("TOM", "Tomato", 1), ("ONI", "Onion", 1)]))
    assert _cart_pvids(session) == {"TOM": 1, "ONI": 1}

    # Turn 4 — change Tomato qty to 3.
    session = prov.merge(session, _cart_result([("TOM", "Tomato", 3), ("ONI", "Onion", 1)]))
    assert _cart_pvids(session) == {"TOM": 3, "ONI": 1}

    # Turn 5 — remove Onion (qty 0 → omitted from the resulting cart).
    session = prov.merge(session, _cart_result([("TOM", "Tomato", 3)]))
    assert _cart_pvids(session) == {"TOM": 3}

    # Turn 6 — switch delivery address to OFFICE; the switch must STICK.
    ctx = _ctx(
        session=session,
        scratch={
            "zepto_store_registered": True,
            "resolved_zepto_address_id": "HOME",
            "saved_address_rows": session["session_zepto_addresses"],
        },
    )
    assert prov.before_call("select_saved_address", {"addressId": "OFFICE"}, ctx) is None
    assert ctx.scratch["zepto_switched_address_id"] == "OFFICE"
    session = prov.merge(
        session, _cart_result([("TOM", "Tomato", 3)], diagnostics={"switched_address_id": "OFFICE"})
    )
    assert session["selected_zepto_address_id"] == "OFFICE", "switch stuck across the turn"

    # Turn 7 — OFFICE was deleted/renamed on the Zepto app → self-heal (clear committed).
    stale = ProviderResult(provider="zepto", results=[])
    stale.diagnostics["stale_committed_address"] = "OFFICE"
    session = prov.merge(session, stale)
    assert "selected_zepto_address_id" not in session, "dead committed id cleared"

    # Turn 8 — view cart now ERRORS empty (Zepto's empty-cart error). run() reclassifies.
    async def fake_run(self, calls, ctx):
        return ProviderResult(
            provider="zepto",
            results=[
                ToolResult(
                    tool="view_cart", provider="zepto", ok=False, error_detail="Your cart is empty."
                )
            ],
        )

    import asyncio

    from core.providers.mcp.mcp_base import McpProvider as _Mcp

    orig = _Mcp.run
    _Mcp.run = fake_run
    try:
        result = asyncio.run(prov.run([], ExecCtx(session={"selected_zepto_address_id": "HOME"})))
    finally:
        _Mcp.run = orig
    cart_tr = next(t for t in result.results if t.tool == "view_cart")
    assert cart_tr.ok and cart_tr.data["data"]["isEmpty"] is True
    session = prov.merge(session, result)
    assert session["session_zepto_cart"] == {"is_empty": True, "items": []}, "cart cleared"

    # Turn 9 — re-add Tomato after the empty cart: fresh cart, committed re-seeds.
    readd = _cart_result([("TOM", "Tomato", 2)], diagnostics={"resolved_zepto_address_id": "HOME"})
    session = prov.merge(session, readd)
    assert _cart_pvids(session) == {"TOM": 2}
    assert session["selected_zepto_address_id"] == "HOME", "committed re-seeded after heal"
