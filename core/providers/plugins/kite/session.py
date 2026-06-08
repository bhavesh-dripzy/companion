"""Kite session merge — instruments LRU + holdings/positions/orders (replacement).

The planner reuses `session_kite_instruments` (an LRU of tradingsymbol→token/price)
to issue quote/order tools without re-searching; holdings/positions/open-orders are
whole-snapshot replacement state refreshed each time their read-only tool runs.
The pending order ticket (mutating two-step flow) is intentionally not handled here.
"""

from __future__ import annotations

from typing import Any

from core.contracts.tool_result import ToolResult
from core.providers.mcp.lru import lru_merge
from core.providers.plugins.kite import snapshots as S

_MAX_INSTRUMENTS = 40
_INSTRUMENT_TOOLS = {"search_instruments", "get_ltp", "get_ohlc", "get_quotes"}


def merge(slice_: dict[str, Any], results: list[ToolResult]) -> dict[str, Any]:
    merged = dict(slice_)
    fresh_instruments: list[dict[str, Any]] = []
    for tr in results:
        if not tr.ok:
            continue
        env = tr.data if isinstance(tr.data, dict) else {}
        if env.get("is_error"):
            continue
        if tr.tool in _INSTRUMENT_TOOLS:
            fresh_instruments.extend(
                S.instruments_from_search(env)
                if tr.tool == "search_instruments"
                else S.instruments_from_quotes(env)
            )
        elif tr.tool == "get_holdings":
            snap = S.holdings_snapshot(env)
            if snap is not None:
                merged["session_kite_holdings"] = snap
        elif tr.tool == "get_positions":
            snap = S.positions_snapshot(env)
            if snap is not None:
                merged["session_kite_positions"] = snap
        elif tr.tool == "get_orders":
            orders = S.open_orders(env)
            if orders:
                merged["session_kite_open_orders"] = orders
    if fresh_instruments:
        merged["session_kite_instruments"] = lru_merge(
            slice_.get("session_kite_instruments"),
            fresh_instruments,
            key=lambda e: e.get("tradingsymbol"),
            cap=_MAX_INSTRUMENTS,
        )
    return merged
