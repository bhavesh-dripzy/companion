"""Kite (Zerodha) snapshot/harvest functions (clean port of the executor helpers).

Pure functions of the read-only tool envelopes: instruments (search / quotes),
holdings, positions, open orders. Ported from `kite.mcp_executor._kite_*`. The
clean provider only renders read-only state; mutating order tools are never
auto-emitted (two-step confirm stays the planner's job).
"""

from __future__ import annotations

import time
from typing import Any


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip().replace(",", ""))
        except (TypeError, ValueError):
            return None
    return None


def _coerce_int(value: Any) -> int | None:
    f = _coerce_float(value)
    return int(f) if f is not None else None


def _split_instrument_key(key: str) -> tuple[str | None, str | None]:
    """Split a `"NSE:RELIANCE"` quote key into (exchange, tradingsymbol)."""
    if not isinstance(key, str) or ":" not in key:
        return (None, None)
    exch, _, sym = key.partition(":")
    return (exch.strip().upper() or None, sym.strip().upper() or None)


def _walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _instrument_row(
    *,
    tradingsymbol: Any,
    exchange: Any,
    instrument_token: Any,
    name: Any = None,
    last_price: Any = None,
    stamp_price: bool = False,
) -> dict[str, Any] | None:
    sym = str(tradingsymbol or "").strip().upper()
    if not sym:
        return None
    row: dict[str, Any] = {
        "tradingsymbol": sym,
        "exchange": str(exchange or "").strip().upper() or None,
        "instrument_token": _coerce_int(instrument_token),
        "name": str(name or "").strip() or None,
        "last_price": _coerce_float(last_price),
    }
    if stamp_price and row["last_price"] is not None:
        row["price_fetched_at"] = int(time.time())
    return row


def instruments_from_search(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for obj in _walk(envelope.get("data") if isinstance(envelope, dict) else None):
        if not isinstance(obj, dict):
            continue
        sym = obj.get("tradingsymbol") or obj.get("tradingSymbol")
        token = obj.get("instrument_token") or obj.get("instrumentToken")
        if not sym or token is None:
            continue
        row = _instrument_row(
            tradingsymbol=sym,
            exchange=obj.get("exchange"),
            instrument_token=token,
            name=obj.get("name"),
            last_price=obj.get("last_price") or obj.get("lastPrice"),
        )
        if row:
            rows.append(row)
    return rows


def instruments_from_quotes(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    data = envelope.get("data") if isinstance(envelope, dict) else None
    if not isinstance(data, dict):
        return rows
    inner = data.get("data") if isinstance(data.get("data"), dict) else None
    candidate = inner or data
    is_quote_map = any(
        isinstance(v, dict) and ("last_price" in v or "instrument_token" in v)
        for v in candidate.values()
    )
    if not is_quote_map:
        return rows
    for key, val in candidate.items():
        if not isinstance(val, dict):
            continue
        exch, sym = _split_instrument_key(str(key))
        row = _instrument_row(
            tradingsymbol=sym,
            exchange=exch,
            instrument_token=val.get("instrument_token") or val.get("instrumentToken"),
            last_price=val.get("last_price") or val.get("lastPrice"),
            stamp_price=True,
        )
        if row:
            rows.append(row)
    return rows


_HOLDING_FIELDS = (
    ("quantity", _coerce_int),
    ("average_price", _coerce_float),
    ("last_price", _coerce_float),
    ("close_price", _coerce_float),
    ("pnl", _coerce_float),
    ("day_change", _coerce_float),
    ("day_change_percentage", _coerce_float),
)


def _holding_row(h: dict[str, Any]) -> dict[str, Any] | None:
    sym = str(h.get("tradingsymbol") or h.get("tradingSymbol") or "").strip().upper()
    if not sym:
        return None
    row: dict[str, Any] = {
        "tradingsymbol": sym,
        "exchange": str(h.get("exchange") or "").strip().upper() or None,
        "instrument_token": _coerce_int(h.get("instrument_token") or h.get("instrumentToken")),
        "product": str(h.get("product") or "").strip().upper() or None,
    }
    for key, fn in _HOLDING_FIELDS:
        row[key] = fn(h.get(key))
    return row


def holdings_snapshot(envelope: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(envelope, dict):
        return None
    for obj in _walk(envelope.get("data")):
        if not isinstance(obj, dict):
            continue
        rows = obj.get("holdings") if isinstance(obj.get("holdings"), list) else None
        if rows is None and isinstance(envelope.get("data"), list):
            rows = envelope["data"]
        if not rows:
            continue
        holdings = [r for r in (_holding_row(h) for h in rows if isinstance(h, dict)) if r]
        if holdings:
            return {"holdings": holdings, "fetched_at": int(time.time())}
    return None


def _position_rows(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in raw if isinstance(raw, list) else []:
        if not isinstance(p, dict):
            continue
        sym = str(p.get("tradingsymbol") or p.get("tradingSymbol") or "").strip().upper()
        if not sym:
            continue
        out.append(
            {
                "tradingsymbol": sym,
                "exchange": str(p.get("exchange") or "").strip().upper() or None,
                "instrument_token": _coerce_int(
                    p.get("instrument_token") or p.get("instrumentToken")
                ),
                "product": str(p.get("product") or "").strip().upper() or None,
                "quantity": _coerce_int(p.get("quantity")),
                "average_price": _coerce_float(p.get("average_price")),
                "last_price": _coerce_float(p.get("last_price")),
                "pnl": _coerce_float(p.get("pnl")),
                "m2m": _coerce_float(p.get("m2m")),
                "value": _coerce_float(p.get("value")),
            }
        )
    return out


def positions_snapshot(envelope: dict[str, Any]) -> dict[str, Any] | None:
    for obj in _walk(envelope.get("data") if isinstance(envelope, dict) else None):
        if isinstance(obj, dict) and ("net" in obj or "day" in obj):
            net, day = _position_rows(obj.get("net")), _position_rows(obj.get("day"))
            if net or day:
                return {"net": net, "day": day, "fetched_at": int(time.time())}
    return None


_ORDER_STR = ("status", "order_type", "product", "variety", "order_timestamp")
_ORDER_NUM = ("quantity", "filled_quantity", "pending_quantity")
_ORDER_FLT = ("price", "average_price", "trigger_price")


def open_orders(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    data = envelope.get("data") if isinstance(envelope, dict) else None
    rows = data if isinstance(data, list) else None
    if rows is None:
        rows = next(
            (
                o["orders"]
                for o in _walk(data)
                if isinstance(o, dict) and isinstance(o.get("orders"), list)
            ),
            None,
        )
    out: list[dict[str, Any]] = []
    for o in rows or []:
        if not isinstance(o, dict):
            continue
        oid = str(o.get("order_id") or o.get("orderId") or "").strip()
        if not oid:
            continue
        row: dict[str, Any] = {
            "order_id": oid,
            "tradingsymbol": str(o.get("tradingsymbol") or o.get("tradingSymbol") or "")
            .strip()
            .upper()
            or None,
            "exchange": str(o.get("exchange") or "").strip().upper() or None,
            "transaction_type": str(o.get("transaction_type") or "").strip().upper() or None,
        }
        for k in _ORDER_STR:
            row[k] = str(o.get(k) or "").strip() or None
        for k in _ORDER_NUM:
            row[k] = _coerce_int(o.get(k))
        for k in _ORDER_FLT:
            row[k] = _coerce_float(o.get(k))
        out.append(row)
    return out
