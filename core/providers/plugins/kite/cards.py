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
        elif tool == "get_gtts":
            rows = _rows_from(envelope, "gtts", _gtt_row)
            if rows:
                out.append(_att("kite_gtt_card", tool, gtts=rows))
        elif tool == "get_trades":
            rows = _rows_from(envelope, "trades", _trade_row)
            if rows:
                out.append(_att("kite_trades_card", tool, trades=rows))
        elif tool == "get_mf_holdings":
            rows = _rows_from(envelope, "mf_holdings", _mf_row)
            if rows:
                out.append(_att("kite_mf_holdings_card", tool, mf_holdings=rows))
    return out


_MAX_TABLE_ROWS = 20


def _rows_from(envelope: dict[str, Any], list_key: str, project) -> list[dict[str, Any]]:
    """Tolerant row extraction: data may be the list itself or nest it under
    `list_key` (Kite Connect response shapes vary by deployment)."""
    data = envelope.get("data") if isinstance(envelope, dict) else None
    raw = data if isinstance(data, list) else None
    if raw is None and isinstance(data, dict):
        candidate = data.get(list_key) or (data.get("data") or {})
        if isinstance(candidate, dict):
            candidate = candidate.get(list_key)
        raw = candidate if isinstance(candidate, list) else None
    rows = []
    for item in (raw or [])[:_MAX_TABLE_ROWS]:
        if isinstance(item, dict):
            row = project(item)
            if row:
                rows.append(row)
    return rows


def _trade_row(item: dict[str, Any]) -> dict[str, Any] | None:
    symbol = item.get("tradingsymbol") or item.get("trading_symbol")
    if not symbol:
        return None
    return {
        "tradingsymbol": str(symbol),
        "exchange": item.get("exchange"),
        "transaction_type": item.get("transaction_type"),
        "quantity": item.get("quantity") or item.get("filled_quantity"),
        "average_price": item.get("average_price") or item.get("price"),
        "fill_timestamp": item.get("fill_timestamp") or item.get("trade_timestamp"),
        "order_id": item.get("order_id"),
    }


def _gtt_row(item: dict[str, Any]) -> dict[str, Any] | None:
    condition = item.get("condition") if isinstance(item.get("condition"), dict) else {}
    symbol = condition.get("tradingsymbol") or item.get("tradingsymbol")
    if not symbol and item.get("id") is None:
        return None
    orders = item.get("orders") if isinstance(item.get("orders"), list) else []
    first = orders[0] if orders and isinstance(orders[0], dict) else {}
    return {
        "id": item.get("id"),
        "tradingsymbol": str(symbol) if symbol else None,
        "trigger_values": condition.get("trigger_values"),
        "last_price": condition.get("last_price"),
        "transaction_type": first.get("transaction_type"),
        "quantity": first.get("quantity"),
        "price": first.get("price"),
        "status": item.get("status"),
        "gtt_type": item.get("type"),
    }


def _mf_row(item: dict[str, Any]) -> dict[str, Any] | None:
    name = item.get("fund") or item.get("tradingsymbol")
    if not name:
        return None
    return {
        "fund": str(name),
        "tradingsymbol": item.get("tradingsymbol"),
        "quantity": item.get("quantity"),
        "average_price": item.get("average_price"),
        "last_price": item.get("last_price"),
        "pnl": item.get("pnl"),
    }


def _att(kind: str, tool: str, **fields: Any) -> Attachment:
    return Attachment(kind=kind, provider="kite", source=f"kite.{tool}", **fields)
