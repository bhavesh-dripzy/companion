"""Golden test: the clean `SwiggyInstamartProvider` builds product cards from REAL data.

Drives the provider against a sanitized real capture (search "milk", live read-only
run on admin_bhavesh), asserting the per-variant product cards + spinId LRU reflect
the real product the live turn surfaced (Amul Gold Milk 1 Ltr, spinId S8PF9T8YLG).
"""

import json
from pathlib import Path

import pytest

from core.contracts.tool_result import ProviderResult, ToolResult
from core.providers.plugins.swiggy_instamart import SwiggyInstamartProvider

pytestmark = pytest.mark.component

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
    amul = next(p for p in products if p["spin_id"] == "S8PF9T8YLG")
    assert amul["name"] == "Amul Gold Milk 1 Ltr" and amul["price_inr"] == 72.0
    assert amul["query"] == "milk"


def test_session_products_lru_keyed_by_spin_id():
    merged = SwiggyInstamartProvider().merge({}, _result())
    products = merged.get("session_instamart_products") or []
    assert products, "expected session_instamart_products carried forward"
    ids = [p["spin_id"] for p in products]
    assert "S8PF9T8YLG" in ids and len(ids) == len(set(ids)), "deduped spinId LRU"
