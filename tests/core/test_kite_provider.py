"""Golden test: the clean `KiteProvider` builds cards from REAL Zerodha data.

Drives the provider against sanitized real captures from a live read-only session
on a real Zerodha account (admin logged in via the Kite TOOL-auth flow): live
get_quotes (RELIANCE ₹1263.3, TCS ₹2151.4), search_instruments, and get_margins.
Empty holdings/positions correctly yield no card (account had none).
"""

import json
from pathlib import Path

import pytest

from core.contracts.tool_result import ProviderResult, ToolResult
from core.providers.plugins.kite import KiteProvider
from core.providers.plugins.kite import snapshots as S

pytestmark = pytest.mark.component

_DIR = Path(__file__).parent / "fixtures/kite"


def _tr(name: str, tool: str) -> ToolResult:
    fix = json.loads((_DIR / f"{name}.json").read_text())
    res = fix["result"]
    return ToolResult(tool=tool, provider="kite", ok=not res.get("is_error"), data=res)


def test_instrument_cards_from_live_quotes():
    cards = KiteProvider().build_attachments(
        ProviderResult(provider="kite", results=[_tr("quotes", "get_quotes")])
    )
    assert len(cards) == 1 and cards[0].kind == "kite_instrument_cards"
    rows = {r["tradingsymbol"]: r for r in cards[0].to_wire()["instruments"]}
    assert rows["RELIANCE"]["last_price"] == 1263.3 and rows["RELIANCE"]["exchange"] == "NSE"
    assert rows["RELIANCE"]["instrument_token"] == 738561
    assert rows["TCS"]["last_price"] == 2151.4
    # get_quotes stamps a price-freshness timestamp for the planner's staleness rule.
    assert "price_fetched_at" in rows["RELIANCE"]


def test_search_instruments_and_margin_cards():
    res = ProviderResult(
        provider="kite",
        results=[_tr("instruments_search", "search_instruments"), _tr("margins", "get_margins")],
    )
    by_kind = {c.kind: c.to_wire() for c in KiteProvider().build_attachments(res)}
    assert "kite_instrument_cards" in by_kind and "kite_margin_card" in by_kind
    assert by_kind["kite_margin_card"]["margin"]["equity"]["enabled"] is True


def test_empty_holdings_positions_yield_no_card():
    empty = ProviderResult(
        provider="kite",
        results=[
            ToolResult(
                tool="get_holdings", provider="kite", ok=True, data={"is_error": False, "data": []}
            ),
            ToolResult(
                tool="get_positions", provider="kite", ok=True, data={"is_error": False, "data": []}
            ),
        ],
    )
    assert KiteProvider().build_attachments(empty) == []
    assert S.holdings_snapshot({"data": []}) is None


def test_merge_builds_instruments_lru_from_quotes():
    merged = KiteProvider().merge(
        {}, ProviderResult(provider="kite", results=[_tr("quotes", "get_quotes")])
    )
    syms = {r["tradingsymbol"] for r in merged.get("session_kite_instruments", [])}
    assert {"RELIANCE", "TCS"} <= syms
