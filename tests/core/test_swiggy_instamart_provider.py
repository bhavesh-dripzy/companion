"""Golden test: the clean `SwiggyInstamartProvider` builds product cards from REAL data.

Drives the provider against a sanitized real capture (search "milk", live read-only
run on admin_bhavesh), asserting the per-variant product cards + spinId LRU reflect
the real product the live turn surfaced (Amul Gold Milk 1 Ltr, spinId S8PF9T8YLG).
"""

import asyncio
import json
from pathlib import Path

import pytest

from core.contracts.tool_result import ProviderResult, ToolResult
from core.providers.base import ExecCtx
from core.providers.mcp.mcp_base import McpProvider
from core.providers.plugins.swiggy_instamart import SwiggyInstamartProvider

pytestmark = pytest.mark.component


def test_instamart_address_auto_resolution_configured():
    assert SwiggyInstamartProvider.saved_addresses_tool == "get_addresses"
    assert SwiggyInstamartProvider.saved_addresses_session_key == "session_instamart_addresses"


def test_instamart_merge_surfaces_instamart_addresses():
    addr = ToolResult(
        tool="get_addresses",
        provider="swiggy_instamart",
        ok=True,
        data={"data": {"addresses": [{"addressId": "IM1", "addressTag": "Home"}]}},
    )
    merged = SwiggyInstamartProvider().merge(
        {}, ProviderResult(provider="swiggy_instamart", results=[addr])
    )
    rows = merged.get("session_instamart_addresses") or []
    assert rows and rows[0]["address_id"] == "IM1"
    assert "session_addresses" not in merged


def test_instamart_before_run_resolves_from_carried_instamart_addresses():
    ctx = ExecCtx(session={"session_instamart_addresses": [{"address_id": "IM9", "label": "Home"}]})
    asyncio.run(SwiggyInstamartProvider().before_run(session=None, ctx=ctx))
    assert ctx.placeholders["__ADDRESS_ID__"] == "IM9"


def test_instamart_before_run_auto_fetch_resolves_id_only_rows(monkeypatch):
    """Instamart rows key on `id`; the auto-fetch must resolve __ADDRESS_ID__ from it."""
    envelope = {"data": {"addresses": [{"id": "363294542", "addressTag": "Home"}]}}

    async def fake_call_tool(session, name, arguments=None):
        assert name == "get_addresses"
        return "RAW"

    monkeypatch.setattr("core.providers.mcp.mcp_base.call_tool", fake_call_tool)
    monkeypatch.setattr("core.providers.mcp.mcp_base.tool_result_payload", lambda raw: envelope)
    ctx = ExecCtx()
    asyncio.run(SwiggyInstamartProvider().before_run(session=object(), ctx=ctx))
    assert ctx.placeholders.get("__ADDRESS_ID__") == "363294542"


def test_before_run_pins_live_cart_address_on_mutating_turn(monkeypatch):
    """TRUST SWIGGY (mutating): before update_cart runs, before_run reads the cart's
    REAL address and pins it, overriding a stale committed selection — so the
    mutation can't drag the cart back to the wrong store."""
    cart_env = {
        "is_error": False,
        "data": {
            "items": [{"spinId": "S1", "itemName": "X", "quantity": 1, "discountedFinalPrice": 50}],
            "selectedAddressDetails": {"id": "LIVE_ADDR"},
        },
    }

    async def fake_call_tool(session, name, arguments=None):
        assert name == "get_cart"
        return "RAW"

    monkeypatch.setattr(
        "core.providers.plugins.swiggy_instamart.provider.call_tool", fake_call_tool
    )
    monkeypatch.setattr(
        "core.providers.plugins.swiggy_instamart.provider.tool_result_payload",
        lambda raw: cart_env,
    )
    ctx = ExecCtx(session={"selected_instamart_address_id": "STALE_ADDR"})
    ctx.scratch["saved_address_rows"] = [{"address_id": "STALE_ADDR"}]  # skip saved-addr fetch
    ctx.scratch["_instamart_mutating"] = True
    asyncio.run(SwiggyInstamartProvider().before_run(session=object(), ctx=ctx))
    assert ctx.scratch["live_cart_address"] == "LIVE_ADDR"
    assert ctx.placeholders["__ADDRESS_ID__"] == "LIVE_ADDR"
    # The anchor every subsequent call uses is now Swiggy's live address.
    assert SwiggyInstamartProvider._anchor_address(ctx) == "LIVE_ADDR"


def test_reconcile_folds_live_cart_not_stale_session():
    """When before_run captured the LIVE cart, update_cart must fold THOSE items
    (real, at the real address) — not the stale session snapshot whose lagging
    address would falsely trip the switch-guard and drop unmentioned lines."""
    prov = SwiggyInstamartProvider()
    ctx = ExecCtx(
        session={
            # Stale carried snapshot: different (old) address, would look "switched".
            "session_instamart_cart": {
                "items": [{"spin_id": "BREAD", "quantity": 2}],
                "selected_address_id": "OLD_ADDR",
            }
        }
    )
    # before_run read the real cart at the real address (bread x2 still there).
    ctx.scratch["live_cart_address"] = "REAL_ADDR"
    ctx.scratch["live_cart_snapshot"] = {
        "items": [{"spin_id": "BREAD", "quantity": 2}],
        "selected_address_id": "REAL_ADDR",
    }
    ctx.scratch["_instamart_valid_spins"] = {"BREAD", "MILK"}
    # Planner adds milk only; bread must be preserved (folded from the live cart).
    args = {"items": [{"spinId": "MILK", "quantity": 1}], "selectedAddressId": "__ADDRESS_ID__"}
    prov.before_call("update_cart", args, ctx)
    sent = {i["spinId"]: i["quantity"] for i in args["items"]}
    assert sent == {"BREAD": 2, "MILK": 1}
    assert args["selectedAddressId"] == "REAL_ADDR"  # targets the live address


def test_address_switch_with_no_new_items_moves_existing_cart():
    """A pure address MOVE (switch, planner sends no items) re-lists the existing
    cart at the new store instead of sending items:[] (which Swiggy rejects and
    which can poison the cart)."""
    prov = SwiggyInstamartProvider()
    ctx = ExecCtx(session={"selected_instamart_address_id": "OLDADDR"})
    ctx.scratch["saved_address_rows"] = [{"address_id": "NEWADDR"}, {"address_id": "OLDADDR"}]
    ctx.scratch["live_cart_address"] = "OLDADDR"
    ctx.scratch["live_cart_snapshot"] = {
        "items": [{"spin_id": "S1", "quantity": 2}],
        "selected_address_id": "OLDADDR",
    }
    ctx.scratch["_instamart_valid_spins"] = {"S1"}
    # Planner switches address (concrete NEWADDR) with NO items — a pure move.
    args = {"items": [], "selectedAddressId": "NEWADDR"}
    out = prov.before_call("update_cart", args, ctx)
    assert out is None  # not short-circuited — we have the existing item to move
    assert args["selectedAddressId"] == "NEWADDR"
    assert {i["spinId"]: i["quantity"] for i in args["items"]} == {"S1": 2}


def test_empty_update_cart_is_short_circuited():
    """update_cart with nothing to send is skipped (synthetic no-op), never sent as
    items:[] — which Swiggy rejects with 'items array is required'."""
    prov = SwiggyInstamartProvider()
    ctx = ExecCtx(session={"selected_instamart_address_id": "A1"})
    args = {"items": [], "selectedAddressId": "A1"}
    out = prov.before_call("update_cart", args, ctx)
    assert isinstance(out, dict) and out.get("is_error") is False
    assert not args.get("items")


def _fake_session_cm():
    class _S:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    return _S()


def test_failed_update_cart_restores_prior_cart(monkeypatch):
    """A failed update_cart poisons the Swiggy cart; recovery re-sends the prior
    valid cart (live snapshot) so the next get_cart works again."""
    prov = SwiggyInstamartProvider()
    sent: list = []

    async def fake_call_tool(session, name, arguments=None):
        sent.append((name, arguments))
        return "RAW"

    monkeypatch.setattr(prov, "open_session", lambda ctx: _fake_session_cm())
    monkeypatch.setattr(
        "core.providers.plugins.swiggy_instamart.provider.call_tool", fake_call_tool
    )
    ctx = ExecCtx()
    ctx.scratch["live_cart_address"] = "OLDADDR"
    ctx.scratch["live_cart_snapshot"] = {
        "items": [{"spin_id": "S1", "quantity": 1}],
        "selected_address_id": "OLDADDR",
    }
    failed = ToolResult(
        tool="update_cart",
        provider="swiggy_instamart",
        ok=False,
        error_detail="Max Per Item Quantity Limit Breached",
    )
    result = ProviderResult(provider="swiggy_instamart", results=[failed])
    asyncio.run(prov._restore_cart_if_poisoned(result, ctx))
    assert sent == [
        ("update_cart", {"items": [{"spinId": "S1", "quantity": 1}], "selectedAddressId": "OLDADDR"})
    ]


def test_restore_is_noop_without_prior_snapshot(monkeypatch):
    prov = SwiggyInstamartProvider()
    sent: list = []

    async def fake_call_tool(session, name, arguments=None):
        sent.append(name)
        return "RAW"

    monkeypatch.setattr(prov, "open_session", lambda ctx: _fake_session_cm())
    monkeypatch.setattr(
        "core.providers.plugins.swiggy_instamart.provider.call_tool", fake_call_tool
    )
    failed = ToolResult(tool="update_cart", provider="swiggy_instamart", ok=False, error_detail="x")
    result = ProviderResult(provider="swiggy_instamart", results=[failed])
    asyncio.run(prov._restore_cart_if_poisoned(result, ExecCtx()))
    assert sent == []  # nothing to restore from → no calls


def test_successful_update_cart_does_not_trigger_restore(monkeypatch):
    prov = SwiggyInstamartProvider()
    sent: list = []

    async def fake_call_tool(session, name, arguments=None):
        sent.append(name)
        return "RAW"

    monkeypatch.setattr(prov, "open_session", lambda ctx: _fake_session_cm())
    monkeypatch.setattr(
        "core.providers.plugins.swiggy_instamart.provider.call_tool", fake_call_tool
    )
    ctx = ExecCtx()
    ctx.scratch["live_cart_address"] = "OLDADDR"
    ctx.scratch["live_cart_snapshot"] = {"items": [{"spin_id": "S1", "quantity": 1}]}
    ok = ToolResult(tool="update_cart", provider="swiggy_instamart", ok=True, data={})
    asyncio.run(
        prov._restore_cart_if_poisoned(ProviderResult(provider="swiggy_instamart", results=[ok]), ctx)
    )
    assert sent == []


def test_before_run_skips_live_probe_on_non_mutating_turn(monkeypatch):
    """A read-only turn (search / show cart) must NOT pay the extra get_cart probe."""
    called: list[str] = []

    async def fake_call_tool(session, name, arguments=None):
        called.append(name)
        return "RAW"

    monkeypatch.setattr(
        "core.providers.plugins.swiggy_instamart.provider.call_tool", fake_call_tool
    )
    monkeypatch.setattr(
        "core.providers.plugins.swiggy_instamart.provider.tool_result_payload",
        lambda raw: {"data": {}},
    )
    ctx = ExecCtx(session={"selected_instamart_address_id": "X"})
    ctx.scratch["saved_address_rows"] = [{"address_id": "X"}]
    asyncio.run(SwiggyInstamartProvider().before_run(session=object(), ctx=ctx))
    assert "get_cart" not in called
    assert "live_cart_address" not in ctx.scratch


def test_instamart_before_run_records_resolved_address_in_scratch(monkeypatch):
    """before_run must record the resolved id + full list into ctx.scratch so the
    UI can show 'Delivering to ...' even when search args omit addressId."""
    envelope = {
        "structured": {
            "addresses": [
                {"id": "360938440", "addressTag": "Room", "addressLine": "Bhavesh: Crown Plaza"},
                {"id": "275412200", "addressTag": "Work", "addressLine": "Bhavesh: H1 Hostel"},
            ]
        }
    }

    async def fake_call_tool(session, name, arguments=None):
        return "RAW"

    monkeypatch.setattr("core.providers.mcp.mcp_base.call_tool", fake_call_tool)
    monkeypatch.setattr("core.providers.mcp.mcp_base.tool_result_payload", lambda raw: envelope)
    ctx = ExecCtx()
    asyncio.run(SwiggyInstamartProvider().before_run(session=object(), ctx=ctx))
    assert ctx.scratch["resolved_address_id"] == "360938440"
    assert [r["address_id"] for r in ctx.scratch["saved_address_rows"]] == ["360938440", "275412200"]


def test_instamart_search_without_addressid_arg_still_shows_delivery_address():
    """The real bug: search_products args carry no addressId, yet the product-cards
    attachment must still expose the delivery address (from resolved scratch)."""
    search = ToolResult(
        tool=_FIX["tool"],
        provider="swiggy_instamart",
        ok=True,
        arguments={"query": "milk"},  # no addressId — the real captured shape
        data=_FIX["result"],
    )
    result = ProviderResult(provider="swiggy_instamart", results=[search])
    result.diagnostics["resolved_address_id"] = "360938440"
    result.diagnostics["saved_address_rows"] = [
        {"address_id": "360938440", "addressTag": "Room", "addressLine": "Bhavesh: Crown Plaza"},
        {"address_id": "275412200", "addressTag": "Work", "addressLine": "Bhavesh: H1 Hostel"},
    ]
    cards = [
        c
        for c in SwiggyInstamartProvider().build_attachments(result)
        if c.kind == "instamart_product_cards"
    ]
    assert cards, "expected a product-cards attachment"
    wire = cards[0].to_wire()
    assert wire["delivery_address_id"] == "360938440"
    addrs = wire["delivery_addresses"]
    assert {a["tag"] for a in addrs} == {"Room", "Work"}
    assert addrs[0]["id"] == "360938440"


def test_instamart_display_address_follows_the_address_search_used(monkeypatch):
    """When the user switches address, the search runs with that concrete addressId
    (a required arg). Display + persistence must follow it, NOT before_run's nearest
    pick. Regression: switching address left the bar showing the original address."""
    rows = [
        {"id": "HOLIDAY", "addressTag": "Holiday", "addressLine": "Holiday Inn Gurgaon"},
        {"id": "ROOM", "addressTag": "Room", "addressLine": "Crown Plaza"},
    ]

    async def fake_run(self, calls, ctx):
        # before_run resolved nearest (Holiday) into scratch; the search itself
        # ran against ROOM (planner copied the picker's addressId).
        ctx.scratch["saved_address_rows"] = rows
        ctx.scratch["resolved_address_id"] = "HOLIDAY"
        search = ToolResult(
            tool="search_products",
            provider="swiggy_instamart",
            ok=True,
            arguments={"query": "soda", "addressId": "ROOM"},
            data=_FIX["result"],
        )
        return ProviderResult(provider="swiggy_instamart", results=[search])

    monkeypatch.setattr(McpProvider, "run", fake_run)
    prov = SwiggyInstamartProvider()
    result = asyncio.run(prov.run([], ExecCtx()))
    assert result.diagnostics["resolved_address_id"] == "ROOM"

    cards = [c for c in prov.build_attachments(result) if c.kind == "instamart_product_cards"]
    assert cards[0].to_wire()["delivery_address_id"] == "ROOM"
    # A bare search must NOT set selected_instamart_address_id — that was the drift
    # source (the planner's per-call addressId poisoned the committed address).
    assert not prov.merge({}, result).get("selected_instamart_address_id")


def test_committed_selection_overrides_planner_per_call_address():
    """The committed selection (UI picker / saved default) drives every call and
    overrides the planner's per-call addressId, which drifts to the first saved
    address. (A bare search still never *sets* the selection — see the test
    above; seeding happens only from the live cart when none is set.)"""
    prov = SwiggyInstamartProvider()
    # A cart built at STARRRS seeds selected=STARRRS when none was set.
    cart_tr = ToolResult(
        tool="get_cart",
        provider="swiggy_instamart",
        ok=True,
        data={
            "data": {
                "selectedAddressDetails": {"id": "STARRRS"},
                "cartTotalAmount": "₹72",
                "items": [{"spinId": "S1", "itemName": "Milk", "quantity": 1, "discountedFinalPrice": 72}],
            }
        },
    )
    merged = prov.merge(
        {}, ProviderResult(provider="swiggy_instamart", results=[cart_tr])
    )
    assert merged["selected_instamart_address_id"] == "STARRRS"

    # The committed selection wins over the planner's drifted per-call addressId.
    ctx = ExecCtx(session={"selected_instamart_address_id": "STARRRS"})
    args = {"items": [{"spinId": "S1", "quantity": 2}], "selectedAddressId": "HOLIDAY"}
    prov.before_call("update_cart", args, ctx)
    assert args["selectedAddressId"] == "STARRRS"  # planner's HOLIDAY drift overridden


def test_explicit_address_switch_wins_over_committed_and_recommits():
    """When the planner (the LLM) resolves an explicit switch — emitting a CONCRETE
    saved addressId that differs from the committed one — that address wins over the
    anchor and is re-committed as the session's selection. No message-text rule is
    involved: the only signal is the LLM choosing a concrete saved id over the
    "__ADDRESS_ID__" placeholder. A fabricated (non-saved) id is still rejected."""
    prov = SwiggyInstamartProvider()
    ctx = ExecCtx(session={"selected_instamart_address_id": "STARRRS"})
    # The saved-address list the planner copied "ROOM" from (loaded by before_run).
    ctx.scratch["saved_address_rows"] = [
        {"address_id": "STARRRS", "label": "Starrs"},
        {"address_id": "ROOM", "label": "Room"},
    ]

    # Switch: a concrete, known-saved id beats the committed STARRRS.
    args = {"query": "atta", "addressId": "ROOM"}
    prov.before_call("search_products", args, ctx)
    assert args["addressId"] == "ROOM"
    assert ctx.scratch["instamart_switched_address_id"] == "ROOM"

    # merge re-commits the switched address (surfaced via diagnostics by run()).
    result = ProviderResult(provider="swiggy_instamart", results=[])
    result.diagnostics["switched_address_id"] = "ROOM"
    assert prov.merge({}, result)["selected_instamart_address_id"] == "ROOM"


def test_unknown_address_id_is_not_treated_as_a_switch():
    """A concrete id that is NOT one of the user's saved addresses is a planner
    fabrication, not an intent — the committed anchor still wins."""
    prov = SwiggyInstamartProvider()
    ctx = ExecCtx(session={"selected_instamart_address_id": "STARRRS"})
    ctx.scratch["saved_address_rows"] = [{"address_id": "STARRRS", "label": "Starrs"}]
    args = {"query": "atta", "addressId": "MADE_UP"}
    prov.before_call("search_products", args, ctx)
    assert args["addressId"] == "STARRRS"
    assert "instamart_switched_address_id" not in ctx.scratch


def test_instamart_merge_persists_resolved_addresses_for_next_turn():
    """merge must carry the resolved address list into the session so later turns
    skip the get_addresses round-trip."""
    result = ProviderResult(provider="swiggy_instamart", results=[])
    result.diagnostics["saved_address_rows"] = [
        {"address_id": "360938440", "addressTag": "Room", "label": "Room"}
    ]
    merged = SwiggyInstamartProvider().merge({}, result)
    rows = merged["session_instamart_addresses"]
    assert rows[0]["address_id"] == "360938440" and rows[0]["provider"] == "swiggy_instamart"


_FIX = json.loads(
    (Path(__file__).parent / "fixtures/swiggy_instamart/search_milk.json").read_text()
)


def _result() -> ProviderResult:
    return ProviderResult(
        provider="swiggy_instamart",
        results=[
            ToolResult(
                tool=_FIX["tool"],
                provider="swiggy_instamart",
                ok=not (_FIX["result"] or {}).get("is_error"),
                arguments=_FIX.get("arguments") or {},
                data=_FIX["result"],
            )
        ],
    )


def test_builds_per_variant_product_cards_from_real_data():
    cards = SwiggyInstamartProvider().build_attachments(_result())
    assert len(cards) == 1
    wire = cards[0].to_wire()
    assert wire["kind"] == "instamart_product_cards" and wire["provider"] == "swiggy_instamart"
    products = wire["products"]
    assert products, "expected ≥1 product card"
    blob = json.dumps(products)
    assert "S8PF9T8YLG" in blob, "the real spinId from the live capture should appear"
    # Product-level rows carry `variants: [{spin_id, price_inr, ...}]` — the
    # live capture's Amul Gold Milk has one variation (spinId S8PF9T8YLG).
    amul = next(p for p in products if p["name"] == "Amul Gold Milk 1 Ltr")
    assert amul["brand"] == "Amul" and amul["query"] == "milk"
    spin = next(v for v in amul["variants"] if v["spin_id"] == "S8PF9T8YLG")
    assert spin["price_inr"] == 72.0 and spin["mrp_inr"] == 72.0
    assert spin["in_stock"] is True


def test_builds_cart_card_and_session_cart():
    # get_cart is mutating-state to populate; drive a representative envelope.
    env = {
        "is_error": False,
        "data": {
            "items": [
                {
                    "spinId": "S8PF9T8YLG",
                    "displayName": "Amul Gold Milk 1 Ltr",
                    "quantity": 2,
                    "price": {"mrp": 72, "offerPrice": 72},
                    "imageUrl": "https://x/milk.jpg",
                    "quantityDescription": "1 L",
                }
            ],
            "grandTotalPaise": 14400,
        },
    }
    tr = ToolResult(tool="get_cart", provider="swiggy_instamart", ok=True, data=env)
    provider = SwiggyInstamartProvider()
    result = ProviderResult(provider="swiggy_instamart", results=[tr])
    cards = [c for c in provider.build_attachments(result) if c.kind == "instamart_cart_card"]
    assert len(cards) == 1
    cart = cards[0].to_wire()["instamart_cart"]
    assert cart["items"][0]["name"] == "Amul Gold Milk 1 Ltr" and cart["items"][0]["quantity"] == 2
    assert cart["grand_total_paise"] == 14400
    assert provider.merge({}, result)["session_instamart_cart"]["items"]


def test_cart_card_always_carries_delivery_address():
    """The cart card must ship with the delivery address so the UI can show where
    it delivers. The cart's OWN selectedAddressDetails wins; the saved-address list
    rides along for label resolution."""
    env = {
        "is_error": False,
        "data": {
            "items": [{"spinId": "S1", "displayName": "Milk", "quantity": 1, "price": {"mrp": 50}}],
            "selectedAddressDetails": {"id": "ROOM"},
            "grandTotalPaise": 5000,
        },
    }
    tr = ToolResult(tool="get_cart", provider="swiggy_instamart", ok=True, data=env)
    provider = SwiggyInstamartProvider()
    result = ProviderResult(provider="swiggy_instamart", results=[tr])
    result.diagnostics["resolved_address_id"] = "STARRRS"
    result.diagnostics["saved_address_rows"] = [
        {"address_id": "ROOM", "label": "Room"},
        {"address_id": "STARRRS", "label": "Starrs"},
    ]
    card = next(c for c in provider.build_attachments(result) if c.kind == "instamart_cart_card")
    wire = card.to_wire()
    assert wire["delivery_address_id"] == "ROOM"  # cart's own address wins
    assert {a["id"] for a in wire["delivery_addresses"]} == {"ROOM", "STARRRS"}


def test_instamart_spin_placeholder_chains_search_into_update_cart():
    """A single-plan add: search_products issues __INSTAMART_SPIN__ (top result's
    spinId), which substitutes into a following update_cart in the same plan."""
    prov = SwiggyInstamartProvider()
    env = _FIX.get("result") or _FIX
    ph: dict = {}
    learned = prov.resolver.learn(ph, env if isinstance(env, dict) else {})
    assert learned.get("__INSTAMART_SPIN__") == "S8PF9T8YLG"
    ph.update(learned)
    sub = prov.resolver.substitute({"items": [{"spinId": "__INSTAMART_SPIN__", "quantity": 1}]}, ph)
    assert sub["items"][0]["spinId"] == "S8PF9T8YLG"
    # __INSTAMART_SPIN__ is transient — it must not persist across turns.
    assert "__INSTAMART_SPIN__" in prov.resolver.transient


def _exec_ctx_with_cart(items: list[dict], valid: list[str] | None = None) -> ExecCtx:
    # Seed the valid-spin set the way a real turn would: items already in the
    # cart are valid, plus any freshly-searched spins the test is about to add.
    spins = {str(i.get("spin_id")) for i in items if i.get("spin_id")}
    spins.update(valid or [])
    return ExecCtx(
        session={"session_instamart_cart": {"items": items}},
        scratch={"_instamart_valid_spins": spins},
    )


def test_update_cart_upsert_preserves_existing_lines_when_adding():
    """Regression: adding a second item must NOT wipe the first. The planner sends
    only the new line; the executor folds it onto the existing cart."""
    ctx = _exec_ctx_with_cart([{"spin_id": "ATTA", "quantity": 1}], valid=["BREAD"])
    args = {"selectedAddressId": "A1", "items": [{"spinId": "BREAD", "quantity": 1}]}
    SwiggyInstamartProvider().before_call("update_cart", args, ctx)
    by_spin = {i["spinId"]: i["quantity"] for i in args["items"]}
    assert by_spin == {"ATTA": 1, "BREAD": 1}


def test_update_cart_upsert_changes_quantity_of_existing_line():
    ctx = _exec_ctx_with_cart([{"spin_id": "ATTA", "quantity": 1}, {"spin_id": "BREAD", "quantity": 1}])
    args = {"items": [{"spinId": "ATTA", "quantity": 3}]}
    SwiggyInstamartProvider().before_call("update_cart", args, ctx)
    by_spin = {i["spinId"]: i["quantity"] for i in args["items"]}
    assert by_spin == {"ATTA": 3, "BREAD": 1}


def test_update_cart_upsert_removes_line_on_zero_quantity():
    ctx = _exec_ctx_with_cart([{"spin_id": "ATTA", "quantity": 1}, {"spin_id": "BREAD", "quantity": 2}])
    args = {"items": [{"spinId": "ATTA", "quantity": 0}]}
    SwiggyInstamartProvider().before_call("update_cart", args, ctx)
    by_spin = {i["spinId"]: i["quantity"] for i in args["items"]}
    assert by_spin == {"BREAD": 2}


def test_update_cart_after_clear_cart_rebuilds_from_scratch():
    """clear_cart earlier in the plan means update_cart must NOT merge the stale
    session cart back in."""
    ctx = _exec_ctx_with_cart([{"spin_id": "OLD", "quantity": 5}], valid=["NEW"])
    provider = SwiggyInstamartProvider()
    provider.harvest("clear_cart", {"is_error": False}, ctx)
    args = {"items": [{"spinId": "NEW", "quantity": 1}]}
    provider.before_call("update_cart", args, ctx)
    by_spin = {i["spinId"]: i["quantity"] for i in args["items"]}
    assert by_spin == {"NEW": 1}


def test_update_cart_injects_address_when_planner_omits_it():
    """Regression: planner omitted the required selectedAddressId → Swiggy errored
    'delivery address is missing'. The executor must inject the resolved address."""
    ctx = ExecCtx(placeholders={"__ADDRESS_ID__": "STARRRS"})
    args = {"items": [{"spinId": "ATTA", "quantity": 1}]}  # no selectedAddressId
    SwiggyInstamartProvider().before_call("update_cart", args, ctx)
    assert args["selectedAddressId"] == "STARRRS"


def test_search_injects_address_from_session_selected_when_missing():
    ctx = ExecCtx(session={"selected_instamart_address_id": "ROOM"})
    args = {"query": "atta"}  # no addressId
    SwiggyInstamartProvider().before_call("search_products", args, ctx)
    assert args["addressId"] == "ROOM"


def test_address_injection_does_not_override_explicit_choice():
    """A switch flow passes a concrete addressId — it must win over the resolved one."""
    ctx = ExecCtx(placeholders={"__ADDRESS_ID__": "STARRRS"})
    args = {"query": "atta", "addressId": "ROOM"}
    SwiggyInstamartProvider().before_call("search_products", args, ctx)
    assert args["addressId"] == "ROOM"


def test_checkout_injects_address_when_missing():
    ctx = ExecCtx(scratch={"resolved_address_id": "STARRRS"})
    args = {"paymentMethod": "Cash"}
    SwiggyInstamartProvider().before_call("checkout", args, ctx)
    assert args["addressId"] == "STARRRS"


# ── address-switch correctness (regression: switch was silently ignored) ───────


def test_explicit_selection_overrides_cart_address_for_switch():
    """A user's explicit address selection must win over the cart's address so an
    address SWITCH takes effect — even when the cart is non-empty at another addr."""
    ctx = ExecCtx(
        session={
            "session_instamart_cart": {"items": [{"spin_id": "X", "quantity": 1}],
                                       "selected_address_id": "CART_ADDR"},
            "selected_instamart_address_id": "PICKED_ADDR",
        }
    )
    assert SwiggyInstamartProvider._anchor_address(ctx) == "PICKED_ADDR"
    # search + update_cart both get the picked address, not the cart's.
    prov = SwiggyInstamartProvider()
    s = {"query": "milk"}
    prov.before_call("search_products", s, ctx)
    assert s["addressId"] == "PICKED_ADDR"


def test_anchor_falls_back_to_cart_address_when_no_selection():
    ctx = ExecCtx(
        session={
            "session_instamart_cart": {"items": [{"spin_id": "X", "quantity": 1}],
                                       "selected_address_id": "CART_ADDR"}
        }
    )
    assert SwiggyInstamartProvider._anchor_address(ctx) == "CART_ADDR"


def test_get_cart_address_reconciles_drifted_committed_selection():
    """TRUST SWIGGY: when this turn's get_cart reports the cart's real delivery
    address, it wins over a stale committed selection. This self-heals a committed
    address that drifted from the cart (e.g. the address was changed on the Apps
    screen, which moves Swiggy's cart but never tells the chat) — otherwise the
    stale value would override Swiggy's on the next add and revert the cart."""
    tr = ToolResult(
        tool="get_cart", provider="swiggy_instamart", ok=True, data=_live_cart_env()
    )
    result = ProviderResult(provider="swiggy_instamart", results=[tr])
    # We come in committed to a STALE address; Swiggy's cart is actually at ADDR1.
    merged = SwiggyInstamartProvider().merge(
        {"selected_instamart_address_id": "STALE_ADDR"}, result
    )
    assert merged["selected_instamart_address_id"] == "ADDR1"


def test_explicit_switch_wins_over_swiggy_cart_address():
    """A switch the user made THIS turn still wins over the cart's current address
    (the move takes effect on the next mutating call) — so trust-Swiggy reconcile
    never silently reverts a same-turn switch."""
    tr = ToolResult(
        tool="get_cart", provider="swiggy_instamart", ok=True, data=_live_cart_env()
    )
    result = ProviderResult(provider="swiggy_instamart", results=[tr])
    result.diagnostics["switched_address_id"] = "PICKED_ADDR"
    merged = SwiggyInstamartProvider().merge(
        {"selected_instamart_address_id": "OLD_ADDR"}, result
    )
    assert merged["selected_instamart_address_id"] == "PICKED_ADDR"


def test_merge_seeds_selection_from_cart_when_unset():
    tr = ToolResult(
        tool="get_cart", provider="swiggy_instamart", ok=True, data=_live_cart_env()
    )
    result = ProviderResult(provider="swiggy_instamart", results=[tr])
    merged = SwiggyInstamartProvider().merge({}, result)
    assert merged["selected_instamart_address_id"] == "ADDR1"


def test_switch_address_does_not_merge_old_address_items():
    """On an address switch, the old store's items must NOT be folded into an
    update_cart at the new address (Swiggy's one-cart-per-address model)."""
    prov = SwiggyInstamartProvider()
    ctx = ExecCtx(
        session={
            "selected_instamart_address_id": "NEWADDR",
            "session_instamart_cart": {
                "items": [{"spin_id": "OLD1", "quantity": 2}],
                "selected_address_id": "OLDADDR",
            },
        },
        scratch={"_instamart_valid_spins": {"NEW1"}},
    )
    args = {"items": [{"spinId": "NEW1", "quantity": 1}]}
    prov.before_call("update_cart", args, ctx)
    assert args["selectedAddressId"] == "NEWADDR"
    spins = {i["spinId"] for i in args["items"]}
    assert spins == {"NEW1"}, "old-address item must not leak into the new-address cart"


def test_clear_cart_empties_session_cart_snapshot():
    """Regression: clear_cart left the old items in the session snapshot, so the
    'cleared' cart kept showing stale items."""
    tr = ToolResult(
        tool="clear_cart", provider="swiggy_instamart", ok=True, data={"is_error": False}
    )
    result = ProviderResult(provider="swiggy_instamart", results=[tr])
    merged = SwiggyInstamartProvider().merge(
        {"session_instamart_cart": {"items": [{"spin_id": "OLD", "quantity": 3}]}}, result
    )
    assert merged["session_instamart_cart"]["items"] == []


def _live_cart_env() -> dict:
    """The real update_cart / get_cart envelope shape (from a live probe): items
    use itemName / mrp / discountedFinalPrice, total is a rupee string, no image."""
    return {
        "is_error": False,
        "data": {
            "selectedAddressDetails": {"id": "ADDR1", "annotation": "Starrrs"},
            "cartTotalAmount": "₹798",
            "billBreakdown": {"toPay": {"label": "To Pay", "value": "₹798"}},
            "items": [
                {
                    "spinId": "DNF3XGDN9H",
                    "itemName": "Sprite Soft Drink Bottle 12 Pieces",
                    "quantity": 2,
                    "isInStockAndAvailable": True,
                    "mrp": 480,
                    "discountedFinalPrice": 395,
                }
            ],
        },
    }


def test_cart_card_parses_live_shape_and_enriches_image_from_lru():
    env = _live_cart_env()
    tr = ToolResult(tool="update_cart", provider="swiggy_instamart", ok=True, data=env)
    result = ProviderResult(provider="swiggy_instamart", results=[tr])
    # LRU (carried from a prior search) supplies the image the cart lacks.
    result.diagnostics["session_instamart_products"] = [
        {
            "name": "Sprite Soft Drink Bottle",
            "brand": "Sprite",
            "variants": [
                {"spin_id": "DNF3XGDN9H", "image_url": "https://img/sprite.png", "quantity_description": "750 ml"}
            ],
        }
    ]
    cards = [
        c
        for c in SwiggyInstamartProvider().build_attachments(result)
        if c.kind == "instamart_cart_card"
    ]
    assert len(cards) == 1
    item = cards[0].to_wire()["instamart_cart"]["items"][0]
    assert item["name"] == "Sprite Soft Drink Bottle 12 Pieces"
    assert item["price_inr"] == 395.0 and item["mrp_inr"] == 480.0
    assert item["image_url"] == "https://img/sprite.png"  # enriched from LRU
    assert cards[0].to_wire()["instamart_cart"]["grand_total_paise"] == 79800


def test_cart_card_surfaces_itemized_bill_breakdown():
    env = {
        "is_error": False,
        "data": {
            "cartTotalAmount": "₹486",
            "items": [{"spinId": "X", "itemName": "Atta", "quantity": 1, "mrp": 524, "discountedFinalPrice": 478}],
            "billBreakdown": {
                "lineItems": [
                    {"label": "Item Total", "value": "₹478.00"},
                    {"label": "Handling Fee", "value": "₹8.00"},
                    {"label": "Delivery Partner Fee", "value": "FREE"},
                ],
                "toPay": {"label": "To Pay", "value": "₹486"},
            },
        },
    }
    tr = ToolResult(tool="get_cart", provider="swiggy_instamart", ok=True, data=env)
    cards = [
        c
        for c in SwiggyInstamartProvider().build_attachments(
            ProviderResult(provider="swiggy_instamart", results=[tr])
        )
        if c.kind == "instamart_cart_card"
    ]
    bill = cards[0].to_wire()["instamart_cart"]["bill_breakdown"]
    assert [b["label"] for b in bill] == ["Item Total", "Handling Fee", "Delivery Partner Fee"]
    handling = next(b for b in bill if b["label"] == "Handling Fee")
    assert handling["amount_paise"] == 800 and handling["is_free"] is False
    free = next(b for b in bill if b["label"] == "Delivery Partner Fee")
    assert free["is_free"] is True and free["amount_paise"] is None


def test_cart_cards_dedupe_update_then_get_to_single_final_card():
    """update_cart replaces the whole cart, so update_cart + get_cart in one turn
    describe the same cart — render exactly one card (no duplicates)."""
    upd = ToolResult(tool="update_cart", provider="swiggy_instamart", ok=True, data=_live_cart_env())
    get = ToolResult(tool="get_cart", provider="swiggy_instamart", ok=True, data=_live_cart_env())
    result = ProviderResult(provider="swiggy_instamart", results=[upd, get])
    cards = [
        c
        for c in SwiggyInstamartProvider().build_attachments(result)
        if c.kind == "instamart_cart_card"
    ]
    assert len(cards) == 1
    assert cards[0].to_wire()["source"] == "swiggy_instamart.get_cart"  # last wins


def test_session_products_lru_keyed_by_product_id():
    merged = SwiggyInstamartProvider().merge({}, _result())
    products = merged.get("session_instamart_products") or []
    assert products, "expected session_instamart_products carried forward"
    # LRU is deduped by product_id; each product row carries its full variants list.
    product_ids = [p["product_id"] for p in products]
    assert "LLB1MIIY7S" in product_ids and len(product_ids) == len(set(product_ids)), (
        "deduped productId LRU"
    )
    # The Amul Gold Milk row (productId LLB1MIIY7S) keeps its single variation.
    amul = next(p for p in products if p["product_id"] == "LLB1MIIY7S")
    spin_ids = [v["spin_id"] for v in amul["variants"]]
    assert "S8PF9T8YLG" in spin_ids
