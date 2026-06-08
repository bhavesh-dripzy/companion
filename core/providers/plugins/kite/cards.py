"""Kite card dispatcher (clean port of `_collect_kite_attachments`).

Read-only tools → typed cards: instrument quotes, holdings, positions, open
orders, margin. The mutating order tools (place/modify/cancel) never produce a
card here — two-step order confirmation stays the planner's job, and the pending
order-ticket card is round-tripped from session (handled by the runner/cutover).
"""

from __future__ import annotations

from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool_result import ToolResult
from core.providers.plugins.kite import snapshots as S

_INSTRUMENT_TOOLS = {"search_instruments", "get_ltp", "get_ohlc", "get_quotes"}


def _instrument_rows(tool: str, envelope: dict[str, Any]) -> list[dict[str, Any]]:
    if tool == "search_instruments":
        return S.instruments_from_search(envelope)
    return S.instruments_from_quotes(envelope)


def build_attachments(results: list[ToolResult]) -> list[Attachment]:
    out: list[Attachment] = []
    for tr in results:
        if not tr.ok:
            continue
        envelope = tr.data if isinstance(tr.data, dict) else {}
        if envelope.get("is_error"):
            continue
        tool = tr.tool
        if tool in _INSTRUMENT_TOOLS:
            rows = _instrument_rows(tool, envelope)
            if rows:
                out.append(_att("kite_instrument_cards", tool, instruments=rows))
        elif tool == "get_holdings":
            snap = S.holdings_snapshot(envelope)
            if snap is not None:
                out.append(_att("kite_holdings_card", tool, holdings=snap))
        elif tool == "get_positions":
            snap = S.positions_snapshot(envelope)
            if snap is not None:
                out.append(_att("kite_positions_card", tool, positions=snap))
        elif tool == "get_orders":
            orders = S.open_orders(envelope)
            if orders:
                out.append(_att("kite_open_orders_card", tool, orders=orders))
        elif tool == "get_margins":
            data = envelope.get("data")
            if data is not None:
                out.append(_att("kite_margin_card", tool, margin=data))
    return out


def _att(kind: str, tool: str, **fields: Any) -> Attachment:
    return Attachment(kind=kind, provider="kite", source=f"kite.{tool}", **fields)
