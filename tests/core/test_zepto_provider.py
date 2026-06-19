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
    assert args["cartItems"] == [{"productVariantId": "pv1", "storeProductId": "sp1", "quantity": 2}]


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
