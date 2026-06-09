"""Kite (Zerodha) session-context renderer for the clean planner.

`Plan._generic_plan` calls `provider.planner_context(ctx)` and PREPENDS the
returned string to the planner input; the transcript, the current user message
and the `(resolved address_id: ...)` line are added by `_format_input`, so this
module renders ONLY the session-state BRIEF blocks for the Kite slice.

The blocks are a faithful parity port of the Kite branches of the legacy
`app/services/agents/pipeline.py::_format_routing_message` (instruments LRU,
holdings/positions snapshots, open orders, the pending order ticket) plus the
shared carried-forward-placeholders line. Headers, caps and the
`  - <name> → <id_key>=<id>` row shape are reproduced verbatim — they are tuned.

Self-contained: stdlib + `typing` only; reads `ctx.session.legacy` (flat
`session_kite_*` keys) and `ctx.session.placeholders`. Every block is guarded so
it renders only when its session list/dict is non-empty, and non-dict rows or
rows missing their literal id are skipped.
"""

from __future__ import annotations

from typing import Any


def build_context(ctx: Any) -> str:
    """Render the Kite session brief; join present blocks with blank lines.

    Returns "" when no Kite block (or the placeholders line) is present.
    """
    session = getattr(ctx, "session", None)
    legacy = getattr(session, "legacy", None)
    legacy = legacy if isinstance(legacy, dict) else {}
    placeholders = getattr(session, "placeholders", None)
    placeholders = placeholders if isinstance(placeholders, dict) else {}

    parts: list[str] = []
    _add_placeholders(parts, placeholders)
    _add_instruments(parts, legacy.get("session_kite_instruments"))
    _add_holdings(parts, legacy.get("session_kite_holdings"))
    _add_positions(parts, legacy.get("session_kite_positions"))
    _add_open_orders(parts, legacy.get("session_kite_open_orders"))
    _add_order_ticket(parts, legacy.get("session_kite_order_ticket"))
    return "\n\n".join(parts)


def _add_placeholders(parts: list[str], placeholders: dict[str, str]) -> None:
    keys = sorted({str(k) for k in placeholders if isinstance(k, str) and k.strip()})
    if keys:
        parts.append(
            "Carried-forward placeholders available (set by previous turns; "
            "use them directly in tool arguments — the executor will substitute "
            "the real values): " + ", ".join(keys)
        )


def _add_instruments(parts: list[str], instruments: Any) -> None:
    if not instruments or not isinstance(instruments, list):
        return
    rows: list[str] = []
    for entry in instruments[:20]:
        if not isinstance(entry, dict):
            continue
        sym = str(entry.get("tradingsymbol") or "").strip()
        if not sym:
            continue
        exch = str(entry.get("exchange") or "").strip()
        token = entry.get("instrument_token")
        lp = entry.get("last_price")
        fetched = entry.get("price_fetched_at")
        tail_bits: list[str] = []
        if isinstance(lp, int | float):
            tail_bits.append(f"ltp ₹{lp:g}")
        if isinstance(fetched, int | float):
            tail_bits.append(f"price_fetched_at={int(fetched)}")
        tail = f" — {' / '.join(tail_bits)}" if tail_bits else ""
        head = f"{exch}:{sym}" if exch else sym
        rows.append(
            f"  - {head} → tradingsymbol={sym}, exchange={exch or '?'}, "
            f"instrument_token={token}{tail}"
        )
    if rows:
        parts.append(
            "Kite instruments already seen this session — when the user "
            "refers to one by name, copy the literal tradingsymbol / "
            "exchange / instrument_token below instead of re-running "
            "`search_instruments`. The `price_fetched_at` (unix seconds) "
            "lets you decide if a cached quote is stale (refetch `get_ltp` "
            "if older than ~10s before placing an order):\n" + "\n".join(rows)
        )


def _add_holdings(parts: list[str], holdings_snap: Any) -> None:
    if not holdings_snap or not isinstance(holdings_snap, dict):
        return
    holdings = holdings_snap.get("holdings")
    if not isinstance(holdings, list) or not holdings:
        return
    rows: list[str] = []
    for h in holdings[:15]:
        if not isinstance(h, dict):
            continue
        sym = str(h.get("tradingsymbol") or "").strip()
        if not sym:
            continue
        qty = h.get("quantity")
        pnl = h.get("pnl")
        bits: list[str] = []
        if qty is not None:
            bits.append(f"qty {qty}")
        if isinstance(pnl, int | float):
            bits.append(f"P&L ₹{pnl:g}")
        tail = f" ({', '.join(bits)})" if bits else ""
        rows.append(f"  - {sym}{tail}")
    if rows:
        parts.append(
            "Current Kite holdings (last `get_holdings` snapshot) — "
            "answer portfolio questions from this without re-fetching:\n" + "\n".join(rows)
        )


def _add_positions(parts: list[str], positions_snap: Any) -> None:
    if not positions_snap or not isinstance(positions_snap, dict):
        return
    rows: list[str] = []
    for bucket in ("net", "day"):
        raw = positions_snap.get(bucket)
        if not isinstance(raw, list):
            continue
        for p in raw[:10]:
            if not isinstance(p, dict):
                continue
            sym = str(p.get("tradingsymbol") or "").strip()
            if not sym:
                continue
            qty = p.get("quantity")
            pnl = p.get("pnl")
            bits = [bucket]
            if qty is not None:
                bits.append(f"qty {qty}")
            if isinstance(pnl, int | float):
                bits.append(f"P&L ₹{pnl:g}")
            rows.append(f"  - {sym} ({', '.join(bits)})")
    if rows:
        parts.append("Current Kite positions (last `get_positions` snapshot):\n" + "\n".join(rows))


def _add_open_orders(parts: list[str], open_orders: Any) -> None:
    if not open_orders or not isinstance(open_orders, list):
        return
    rows: list[str] = []
    for o in open_orders[:15]:
        if not isinstance(o, dict):
            continue
        oid = str(o.get("order_id") or "").strip()
        if not oid:
            continue
        sym = str(o.get("tradingsymbol") or "").strip() or "?"
        side = str(o.get("transaction_type") or "").strip()
        qty = o.get("quantity")
        status = str(o.get("status") or "").strip()
        rows.append(f"  - {side} {sym} qty {qty} [{status}] → order_id={oid}")
    if rows:
        parts.append(
            "Kite open orders this session — `modify_order` / `cancel_order` "
            "REQUIRE the literal order_id below; never invent one, and only "
            "modify/cancel after the user explicitly confirms which order:\n" + "\n".join(rows)
        )


def _add_order_ticket(parts: list[str], ticket: Any) -> None:
    if not ticket or not isinstance(ticket, dict):
        return
    sym = str(ticket.get("tradingsymbol") or "").strip() or "?"
    side = str(ticket.get("transaction_type") or "").strip() or "?"
    qty = ticket.get("quantity")
    product = str(ticket.get("product") or "").strip() or "?"
    otype = str(ticket.get("order_type") or "").strip() or "?"
    price = ticket.get("price")
    price_bit = f" @ ₹{price:g}" if isinstance(price, int | float) else ""
    parts.append(
        "PENDING UNCONFIRMED ORDER TICKET (assembled last turn, NOT yet "
        f"placed): {side} {qty} {sym} — product {product}, {otype}{price_bit}. "
        "If the user explicitly confirms ('yes' / 'confirm' / 'place it'), "
        "emit the matching `place_order` (refetch `get_ltp` first if the "
        "cached price is stale). If the user edits it, re-assemble the "
        "ticket and keep waiting. If the user backs out, drop it."
    )
