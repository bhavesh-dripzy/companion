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


def test_session_products_lru_keyed_by_variant_id():
    merged = ZeptoProvider().merge({}, _result())
    products = merged.get("session_zepto_products") or []
    ids = [p["product_variant_id"] for p in products]
    assert _PVID in ids and len(ids) == len(set(ids)), "deduped variant LRU"
