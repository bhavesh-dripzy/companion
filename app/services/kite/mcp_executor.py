"""
Zerodha Kite (Kite Connect) MCP tool execution.

Kite shares the same mcp-remote-style stdio transport plumbing as the food
providers, but its tools describe a **real-money trading account**. To keep the
planner prompt free of mechanical hacks this executor stays deliberately slim —
it does three things and nothing else:

* **Placeholder substitution** — the planner may drop a small vocabulary of
  ``__KITE_*__`` placeholders into any plan and the executor resolves them
  on-the-fly from carried-forward session state (the planner copies literal
  ids out of the session indexes instead, so these are a convenience, never a
  decision point).
* **Defensive coercion** — enum casing (``transaction_type`` → ``BUY``/``SELL``,
  ``product`` → ``CNC``/``MIS``/``NRML``, …), ``quantity`` → ``int``, and
  building ``"EXCH:SYM"`` instrument strings for the quote tools.
* **Harvesting** — walks completed step envelopes and merges user-meaningful
  entities (instruments seen, holdings/positions snapshots, open orders, GTTs)
  into recency-bounded session indexes the pipeline round-trips on every turn.

CRITICAL: this executor NEVER decides to place, modify, or cancel an order.
That is the planner's job, and the planner is gated by an explicit two-step
confirmation in the prompt. The executor only runs the plan it is handed and
substitutes/coerces/harvests. There is no auto-prefetch of a mutating tool, and
no business rule (when to confirm, when a quote is stale, market-hours guards)
lives here — all of that is in ``prompts/trading.system.md``.

All read-only tools may be run freely; the ``session_kite_order_ticket`` is
round-tripped unchanged (the planner owns the pending ticket; the executor just
carries it across turns).
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import time
from pathlib import Path
from typing import Any

from app.services.zomato.mcp_executor import (
    _humanize_mcp_failure,
    _mcp_remote_config_dir,
    _stdio_env,
    _tool_result_payload,
    _walk_json,
    ensure_mcp_tokens_on_disk,
    repo_root_from_settings,
    substitute_placeholders,
)

logger = logging.getLogger(__name__)

_DEFAULT_KITE_MCP_URL = "https://mcp.kite.trade/mcp"

_MCP_INIT_TIMEOUT_SECONDS = int(
    os.getenv("KITE_MCP_INIT_TIMEOUT_SECONDS")
    or os.getenv("ZOMATO_MCP_INIT_TIMEOUT_SECONDS", "20")
)
_MCP_TOOL_TIMEOUT_SECONDS = int(
    os.getenv("KITE_MCP_TOOL_TIMEOUT_SECONDS")
    or os.getenv("ZOMATO_MCP_TOOL_TIMEOUT_SECONDS", "30")
)

# Kite placeholder vocabulary the planner may use freely. NOTE: there is NO
# lat/lng/address/device placeholder here — Kite is account-scoped, not
# location-scoped. Each placeholder is resolved from the carried-forward
# session indexes (the planner copies literal ids out of those blocks; the
# placeholders are only a convenience for the common "the one I just looked at"
# follow-up).
_KITE_INSTRUMENT_TOKEN_PLACEHOLDER = "__KITE_INSTRUMENT_TOKEN__"
_KITE_TRADINGSYMBOL_PLACEHOLDER = "__KITE_TRADINGSYMBOL__"
_KITE_EXCHANGE_PLACEHOLDER = "__KITE_EXCHANGE__"
_KITE_ORDER_ID_PLACEHOLDER = "__KITE_ORDER_ID__"
_KITE_GTT_ID_PLACEHOLDER = "__KITE_GTT_ID__"
_PLACEHOLDERS = frozenset(
    {
        _KITE_INSTRUMENT_TOKEN_PLACEHOLDER,
        _KITE_TRADINGSYMBOL_PLACEHOLDER,
        _KITE_EXCHANGE_PLACEHOLDER,
        _KITE_ORDER_ID_PLACEHOLDER,
        _KITE_GTT_ID_PLACEHOLDER,
    }
)

# Per-placeholder aliases the executor maps onto the canonical placeholder so
# the planner can stay declarative when it copies a scalar out of the session.
_KITE_PLACEHOLDER_ALIASES: dict[str, tuple[str, ...]] = {
    _KITE_INSTRUMENT_TOKEN_PLACEHOLDER: ("instrument_token", "instrumentToken"),
    _KITE_TRADINGSYMBOL_PLACEHOLDER: ("tradingsymbol", "tradingSymbol"),
    _KITE_EXCHANGE_PLACEHOLDER: ("exchange",),
    _KITE_ORDER_ID_PLACEHOLDER: ("order_id", "orderId"),
    _KITE_GTT_ID_PLACEHOLDER: ("trigger_id", "triggerId", "gtt_id"),
}

# The mutating tools. The executor lists them ONLY so it can stamp diagnostics
# and reason about which tools touched the order book — it never auto-emits one.
_MUTATING_TOOLS = frozenset(
    {
        "place_order",
        "modify_order",
        "cancel_order",
        "place_gtt_order",
        "modify_gtt_order",
        "delete_gtt_order",
    }
)

# Tools whose ``instruments`` argument must be an array of "EXCH:SYM" strings.
_QUOTE_TOOLS = frozenset({"get_ltp", "get_ohlc", "get_quotes"})

# Defensive enum vocabularies (canonical casing the Kite Connect API expects).
_TRANSACTION_TYPES = {"BUY", "SELL"}
_PRODUCTS = {"CNC", "MIS", "NRML", "MTF", "CO", "BO"}
_ORDER_TYPES = {"MARKET", "LIMIT", "SL", "SL-M"}
_VARIETIES = {"regular", "amo", "co", "iceberg", "auction"}
_VALIDITIES = {"DAY", "IOC", "TTL"}


def _kite_url(mcp_url: str | None) -> str:
    return (
        (mcp_url or "").strip()
        or os.getenv("KITE_MCP_URL", "").strip()
        or _DEFAULT_KITE_MCP_URL
    )


# ---------------------------------------------------------------------------
# Defensive coercion — enum casing + quantity int + "EXCH:SYM" assembly.
#
# The planner is responsible for picking the right values; the executor only
# normalises obvious casing slips (``buy`` → ``BUY``) and types (``"10"`` → 10)
# so a tiny LLM formatting wobble doesn't get rejected by the broker. It does
# NOT invent missing required fields — a missing ``product`` stays missing so
# the broker (and the planner's refusal rules) catch it, never the executor.
# ---------------------------------------------------------------------------


def _coerce_enum(value: Any, vocab: set[str], *, upper: bool) -> Any:
    """Snap a string onto its canonical-cased member when it matches (case-folded)."""
    if not isinstance(value, str):
        return value
    cleaned = value.strip()
    if not cleaned:
        return value
    want = cleaned.upper() if upper else cleaned.lower()
    if want in vocab:
        return want
    # Tolerate already-correct mixed-case members (e.g. "SL-M", "regular").
    for member in vocab:
        if member.upper() == cleaned.upper():
            return member
    return cleaned


def _coerce_quantity(value: Any) -> Any:
    """Coerce a quantity-shaped field to ``int`` when it cleanly converts."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value.strip()))
        except (TypeError, ValueError):
            return value
    return value


def _build_instrument_string(value: Any, placeholder_values: dict[str, Any]) -> Any:
    """Turn a bare tradingsymbol into ``"EXCH:SYM"`` using session/placeholder hints.

    Pass-through when the value already looks like ``"NSE:RELIANCE"`` (contains a
    colon) or when there's nothing to qualify it with.
    """
    if not isinstance(value, str):
        return value
    cleaned = value.strip()
    if not cleaned or ":" in cleaned:
        return cleaned or value
    exch = str(placeholder_values.get(_KITE_EXCHANGE_PLACEHOLDER) or "").strip()
    if exch:
        return f"{exch.upper()}:{cleaned.upper()}"
    return cleaned


def _normalize_plan(
    tool_plan: list[dict[str, Any]],
    *,
    placeholder_values: dict[str, Any],
) -> list[dict[str, Any]]:
    """Coerce planner output into Kite-shaped arguments (casing / types only)."""
    cleaned: list[dict[str, Any]] = []
    for raw in tool_plan:
        if not isinstance(raw, dict):
            continue
        tool = str(raw.get("tool") or "").strip()
        if not tool:
            continue
        args = dict(raw.get("arguments") or {})

        # Enum casing — applies wherever the field is present, harmless on reads.
        if "transaction_type" in args:
            args["transaction_type"] = _coerce_enum(
                args["transaction_type"], _TRANSACTION_TYPES, upper=True
            )
        if "product" in args:
            args["product"] = _coerce_enum(args["product"], _PRODUCTS, upper=True)
        if "order_type" in args:
            args["order_type"] = _coerce_enum(args["order_type"], _ORDER_TYPES, upper=True)
        if "variety" in args:
            args["variety"] = _coerce_enum(args["variety"], _VARIETIES, upper=False)
        if "validity" in args:
            args["validity"] = _coerce_enum(args["validity"], _VALIDITIES, upper=True)

        # Quantity-shaped fields → int.
        for qty_key in ("quantity", "disclosed_quantity", "iceberg_quantity", "iceberg_legs"):
            if qty_key in args and args[qty_key] not in ("", None):
                args[qty_key] = _coerce_quantity(args[qty_key])

        # Quote tools: build "EXCH:SYM" strings for each instrument entry.
        if tool in _QUOTE_TOOLS:
            instruments = args.get("instruments")
            if isinstance(instruments, str):
                instruments = [instruments]
            if isinstance(instruments, list):
                args["instruments"] = [
                    _build_instrument_string(inst, placeholder_values)
                    for inst in instruments
                ]

        cleaned.append({"tool": tool, "arguments": args})
    return cleaned


def _mentions_kite_placeholder(obj: Any) -> bool:
    if isinstance(obj, str):
        return obj in _PLACEHOLDERS
    if isinstance(obj, dict):
        return any(_mentions_kite_placeholder(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_mentions_kite_placeholder(i) for i in obj)
    return False


def _unresolved_kite_placeholders(obj: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, str):
        if obj in _PLACEHOLDERS:
            found.add(obj)
        return found
    if isinstance(obj, dict):
        for value in obj.values():
            found |= _unresolved_kite_placeholders(value)
    elif isinstance(obj, list):
        for value in obj:
            found |= _unresolved_kite_placeholders(value)
    return found


async def _run_kite_session(
    plan: list[dict[str, Any]],
    *,
    repo_root: Path,
    mcp_url: str,
    node_binary: str,
    user_id: str | None,
    placeholder_values: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
    except ImportError as exc:  # pragma: no cover - environment check
        raise RuntimeError(
            "Python MCP SDK is not installed for this interpreter. Use Python 3.10+ "
            "and install requirements.txt, or run the Lambda Python 3.12 container."
        ) from exc

    proxy_script = repo_root / "node_modules/mcp-remote/dist/proxy.js"
    if not proxy_script.is_file():
        raise RuntimeError(
            "mcp-remote is not installed — run npm install at the repo root."
        )

    # Cold-container guard: hydrate /tmp/.mcp-auth from Secrets Manager when
    # the current-version tokens file isn't already present locally.
    ensure_mcp_tokens_on_disk(repo_root, mcp_url=mcp_url, user_id=user_id)

    params = StdioServerParameters(
        command=node_binary,
        args=[str(proxy_script), mcp_url],
        cwd=str(repo_root),
        env=_stdio_env(repo_root, user_id=user_id),
    )

    step_results: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {
        "executor": "kite.mcp_executor",
    }
    if placeholder_values:
        diagnostics["seeded_placeholders"] = sorted(placeholder_values.keys())

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(
                session.initialize(), timeout=_MCP_INIT_TIMEOUT_SECONDS
            )

            step_index = 0

            async def call_tool(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
                result = await asyncio.wait_for(
                    session.call_tool(tool, arguments=arguments),
                    timeout=_MCP_TOOL_TIMEOUT_SECONDS,
                )
                return _tool_result_payload(result)

            for raw in plan:
                if not isinstance(raw, dict):
                    continue
                tool = str(raw.get("tool") or "").strip()
                if not tool:
                    continue
                base_args = copy.deepcopy(raw.get("arguments") or {})

                exec_args = substitute_placeholders(
                    copy.deepcopy(base_args), placeholder_values
                )
                if _mentions_kite_placeholder(exec_args):
                    # The executor never auto-prefetches a value here — the
                    # planner copies literal ids out of the session blocks. An
                    # unresolved placeholder means the planner referenced a
                    # value we don't have, so skip the step rather than guess
                    # (especially important for mutating tools).
                    unresolved = _unresolved_kite_placeholders(exec_args)
                    if unresolved:
                        diagnostics.setdefault("skipped_tools", []).append(
                            {
                                "tool": tool,
                                "reason": "placeholder_unresolved",
                                "placeholders": sorted(unresolved),
                            }
                        )
                        continue

                envelope = await call_tool(tool, exec_args or {})
                step_results.append(
                    {
                        "tool": tool,
                        "arguments": exec_args or {},
                        "step_index": step_index,
                        "result": envelope,
                    }
                )
                step_index += 1

    diagnostics["resolved_placeholders"] = sorted(placeholder_values.keys())
    # Surface values so pipeline can hand them back as session_state for next turn.
    diagnostics["session_placeholders"] = {
        k: str(v) for k, v in placeholder_values.items() if v is not None
    }
    return step_results, diagnostics


# ---------------------------------------------------------------------------
# Session-state harvesting — Zepto/Instamart-style LRUs of instruments seen,
# holdings/positions snapshots, open orders, and GTTs.
#
# Layered on top of the executor without disturbing its placeholder-resolution
# logic. The harvesters walk completed step envelopes, extract user-meaningful
# entities, and merge them into recency-bounded lists the pipeline round-trips
# on every turn. The planner reads these on the next turn so it can:
#   * copy a literal tradingsymbol/exchange/instrument_token triple out of
#     memory instead of re-running ``search_instruments``,
#   * answer "what's in my portfolio" without an extra ``get_holdings`` call,
#   * surface a real ``order_id`` for a modify/cancel.
#
# Each envelope's ``data`` can arrive as structured JSON OR as prose text (the
# hosted MCP sometimes narrates instead of returning JSON), so every harvester
# is structured-first with a prose-regex fallback — exactly like Instamart's
# cart snapshot tolerates an empty-cart error string.
# ---------------------------------------------------------------------------

_MAX_REMEMBERED_INSTRUMENTS = 60
_MAX_REMEMBERED_OPEN_ORDERS = 40


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
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
    """Split a ``"NSE:RELIANCE"`` data key into ``(exchange, tradingsymbol)``."""
    if not isinstance(key, str) or ":" not in key:
        return (None, None)
    exch, _, sym = key.partition(":")
    exch = exch.strip().upper() or None
    sym = sym.strip().upper() or None
    return (exch, sym)


def _instrument_row(
    *,
    tradingsymbol: str | None,
    exchange: str | None,
    instrument_token: Any,
    name: str | None = None,
    last_price: Any = None,
    stamp_price: bool = False,
) -> dict[str, Any] | None:
    """Build one session-instruments LRU row. Requires a tradingsymbol."""
    sym = str(tradingsymbol or "").strip().upper()
    if not sym:
        return None
    row: dict[str, Any] = {
        "tradingsymbol": sym,
        "exchange": (str(exchange or "").strip().upper() or None),
        "instrument_token": _coerce_int(instrument_token),
        "name": (str(name or "").strip() or None),
        "last_price": _coerce_float(last_price),
    }
    if stamp_price and row["last_price"] is not None:
        # Unix-seconds stamp the planner uses to decide if a cached quote is
        # stale (the ~10s refetch rule lives in the prompt, not here).
        row["price_fetched_at"] = int(time.time())
    return row


def _kite_instruments_from_search(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a ``search_instruments`` envelope into LRU rows (structured-first)."""
    rows: list[dict[str, Any]] = []
    if not isinstance(envelope, dict):
        return rows
    for obj in _walk_json(envelope.get("data")):
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
        if row is not None:
            rows.append(row)
    return rows


def _kite_instruments_from_quotes(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a ``get_ltp``/``get_ohlc``/``get_quotes`` envelope into priced rows.

    Kite keys the quote payload by ``"EXCH:SYM"``; we split that to recover the
    tradingsymbol/exchange and stamp ``price_fetched_at`` so the planner's
    staleness rule has a timestamp to compare against.
    """
    rows: list[dict[str, Any]] = []
    if not isinstance(envelope, dict):
        return rows
    data = envelope.get("data")
    # The quote payload is a dict keyed by "EXCH:SYM"; it may be nested under a
    # ``data`` wrapper depending on the MCP's framing.
    quote_map: dict[str, Any] | None = None
    if isinstance(data, dict):
        inner = data.get("data") if isinstance(data.get("data"), dict) else None
        candidate = inner or data
        # Heuristic: a quote map's values are dicts carrying last_price; if the
        # top dict already looks like one quote, wrap it.
        if any(
            isinstance(v, dict) and ("last_price" in v or "instrument_token" in v)
            for v in candidate.values()
        ):
            quote_map = candidate
    if not isinstance(quote_map, dict):
        return rows
    for key, val in quote_map.items():
        if not isinstance(val, dict):
            continue
        exch, sym = _split_instrument_key(str(key))
        last_price = val.get("last_price") or val.get("lastPrice")
        row = _instrument_row(
            tradingsymbol=sym,
            exchange=exch,
            instrument_token=val.get("instrument_token") or val.get("instrumentToken"),
            last_price=last_price,
            stamp_price=True,
        )
        if row is not None:
            rows.append(row)
    return rows


def _kite_holdings_snapshot(envelope: dict[str, Any]) -> dict[str, Any] | None:
    """Build a holdings snapshot from a ``get_holdings`` envelope (structured-first)."""
    if not isinstance(envelope, dict):
        return None
    holdings: list[dict[str, Any]] = []
    for obj in _walk_json(envelope.get("data")):
        if not isinstance(obj, dict):
            continue
        raw = obj.get("holdings")
        rows = raw if isinstance(raw, list) else None
        # Top-level may already be the list (data is a bare array).
        if rows is None and isinstance(envelope.get("data"), list):
            rows = envelope["data"]
        if not rows:
            continue
        for h in rows:
            if not isinstance(h, dict):
                continue
            sym = str(h.get("tradingsymbol") or h.get("tradingSymbol") or "").strip().upper()
            if not sym:
                continue
            holdings.append(
                {
                    "tradingsymbol": sym,
                    "exchange": str(h.get("exchange") or "").strip().upper() or None,
                    "instrument_token": _coerce_int(
                        h.get("instrument_token") or h.get("instrumentToken")
                    ),
                    "quantity": _coerce_int(h.get("quantity")),
                    "average_price": _coerce_float(h.get("average_price")),
                    "last_price": _coerce_float(h.get("last_price")),
                    "close_price": _coerce_float(h.get("close_price")),
                    "pnl": _coerce_float(h.get("pnl")),
                    "day_change": _coerce_float(h.get("day_change")),
                    "day_change_percentage": _coerce_float(h.get("day_change_percentage")),
                    "product": str(h.get("product") or "").strip().upper() or None,
                }
            )
        if holdings:
            break
    if not holdings:
        return None
    return {"holdings": holdings, "fetched_at": int(time.time())}


def _kite_positions_snapshot(envelope: dict[str, Any]) -> dict[str, Any] | None:
    """Build a positions snapshot ({net, day}) from a ``get_positions`` envelope."""
    if not isinstance(envelope, dict):
        return None

    def _rows(raw: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not isinstance(raw, list):
            return out
        for p in raw:
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

    for obj in _walk_json(envelope.get("data")):
        if not isinstance(obj, dict):
            continue
        if "net" in obj or "day" in obj:
            net = _rows(obj.get("net"))
            day = _rows(obj.get("day"))
            if net or day:
                return {"net": net, "day": day, "fetched_at": int(time.time())}
    return None


def _kite_open_orders(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a ``get_orders`` envelope into open-order rows (order_id + key fields)."""
    out: list[dict[str, Any]] = []
    if not isinstance(envelope, dict):
        return out
    rows: list[Any] | None = None
    data = envelope.get("data")
    if isinstance(data, list):
        rows = data
    else:
        for obj in _walk_json(data):
            if isinstance(obj, dict) and isinstance(obj.get("orders"), list):
                rows = obj["orders"]
                break
    if not rows:
        return out
    for o in rows:
        if not isinstance(o, dict):
            continue
        oid = str(o.get("order_id") or o.get("orderId") or "").strip()
        if not oid:
            continue
        out.append(
            {
                "order_id": oid,
                "status": str(o.get("status") or "").strip() or None,
                "tradingsymbol": str(o.get("tradingsymbol") or o.get("tradingSymbol") or "").strip().upper()
                or None,
                "exchange": str(o.get("exchange") or "").strip().upper() or None,
                "transaction_type": str(o.get("transaction_type") or "").strip().upper() or None,
                "quantity": _coerce_int(o.get("quantity")),
                "filled_quantity": _coerce_int(o.get("filled_quantity")),
                "pending_quantity": _coerce_int(o.get("pending_quantity")),
                "price": _coerce_float(o.get("price")),
                "average_price": _coerce_float(o.get("average_price")),
                "trigger_price": _coerce_float(o.get("trigger_price")),
                "order_type": str(o.get("order_type") or "").strip().upper() or None,
                "product": str(o.get("product") or "").strip().upper() or None,
                "variety": str(o.get("variety") or "").strip() or None,
                "order_timestamp": str(o.get("order_timestamp") or "").strip() or None,
            }
        )
    return out


def _kite_gtts(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a ``get_gtts`` envelope into GTT rows (trigger_id + condition)."""
    out: list[dict[str, Any]] = []
    if not isinstance(envelope, dict):
        return out
    rows: list[Any] | None = None
    data = envelope.get("data")
    if isinstance(data, list):
        rows = data
    else:
        for obj in _walk_json(data):
            if isinstance(obj, dict) and isinstance(obj.get("gtts"), list):
                rows = obj["gtts"]
                break
    if not rows:
        return out
    for g in rows:
        if not isinstance(g, dict):
            continue
        gid = str(g.get("id") or g.get("trigger_id") or g.get("triggerId") or "").strip()
        if not gid:
            continue
        cond = g.get("condition") if isinstance(g.get("condition"), dict) else {}
        out.append(
            {
                "trigger_id": gid,
                "status": str(g.get("status") or "").strip() or None,
                "tradingsymbol": str(cond.get("tradingsymbol") or "").strip().upper() or None,
                "exchange": str(cond.get("exchange") or "").strip().upper() or None,
                "trigger_values": cond.get("trigger_values"),
                "last_price": _coerce_float(cond.get("last_price")),
            }
        )
    return out


def _merge_recent(
    prior: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    *,
    key: str,
    cap: int,
) -> list[dict[str, Any]]:
    """LRU merge — most-recent items first, dedup by ``key``."""
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for src in (incoming, prior):
        for row in src or []:
            if not isinstance(row, dict):
                continue
            k = row.get(key)
            if not isinstance(k, str) or not k:
                continue
            if k in seen:
                continue
            seen.add(k)
            merged.append(row)
            if len(merged) >= cap:
                return merged
    return merged


def _harvest_kite_session(
    steps: list[dict[str, Any]],
    *,
    seed_instruments: list[dict[str, Any]] | None,
    seed_holdings: dict[str, Any] | None,
    seed_positions: dict[str, Any] | None,
    seed_open_orders: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Walk all step envelopes and return updated session indexes."""
    instruments = list(seed_instruments or [])
    holdings: dict[str, Any] | None = dict(seed_holdings) if isinstance(seed_holdings, dict) else None
    positions: dict[str, Any] | None = dict(seed_positions) if isinstance(seed_positions, dict) else None
    open_orders = list(seed_open_orders or [])

    for step in steps:
        if not isinstance(step, dict):
            continue
        tool = str(step.get("tool") or "").strip().split("(", 1)[0]
        envelope = step.get("result") if isinstance(step.get("result"), dict) else {}
        if tool == "search_instruments":
            new_rows = _kite_instruments_from_search(envelope)
            if new_rows:
                instruments = _merge_recent(
                    instruments, new_rows, key="tradingsymbol", cap=_MAX_REMEMBERED_INSTRUMENTS
                )
        elif tool in _QUOTE_TOOLS:
            new_rows = _kite_instruments_from_quotes(envelope)
            if new_rows:
                instruments = _merge_recent(
                    instruments, new_rows, key="tradingsymbol", cap=_MAX_REMEMBERED_INSTRUMENTS
                )
        elif tool == "get_holdings":
            snap = _kite_holdings_snapshot(envelope)
            if snap is not None:
                holdings = snap
        elif tool == "get_positions":
            snap = _kite_positions_snapshot(envelope)
            if snap is not None:
                positions = snap
        elif tool == "get_orders":
            new_orders = _kite_open_orders(envelope)
            if new_orders:
                open_orders = _merge_recent(
                    open_orders, new_orders, key="order_id", cap=_MAX_REMEMBERED_OPEN_ORDERS
                )

    return {
        "session_kite_instruments": instruments,
        "session_kite_holdings": holdings,
        "session_kite_positions": positions,
        "session_kite_open_orders": open_orders,
    }


def run_tool_plan_sync(
    tool_plan: list[dict[str, Any]],
    *,
    repo_root: Path | None = None,
    mcp_url: str | None = None,
    node_binary: str | None = None,
    user_id: str | None = None,
    session_placeholders: dict[str, str] | None = None,
    session_kite_instruments: list[dict[str, Any]] | None = None,
    session_kite_holdings: dict[str, Any] | None = None,
    session_kite_positions: dict[str, Any] | None = None,
    session_kite_open_orders: list[dict[str, Any]] | None = None,
    session_kite_order_ticket: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a Zerodha Kite MCP tool plan.

    Slim by design: substitute placeholders, coerce enums/types, run the plan,
    harvest session indexes. Never decides to place/modify/cancel an order —
    that is the planner's job, gated by two-step confirmation in the prompt.
    """
    root = repo_root or repo_root_from_settings()
    url = _kite_url(mcp_url)
    node = node_binary or os.getenv("NODE_BINARY", "node")

    # Seed placeholder values from prior-turn session_state. The planner copies
    # literal ids out of the session blocks directly, so these are a fallback
    # for the bare ``__KITE_*__`` placeholder shape only.
    placeholder_values: dict[str, Any] = {}
    if isinstance(session_placeholders, dict):
        for k, v in session_placeholders.items():
            if isinstance(k, str) and k in _PLACEHOLDERS:
                if isinstance(v, (str, int)) and str(v).strip():
                    placeholder_values[k] = str(v).strip()

    normalized = _normalize_plan(tool_plan, placeholder_values=placeholder_values)

    try:
        steps, diagnostics = asyncio.run(
            _run_kite_session(
                normalized,
                repo_root=root,
                mcp_url=url,
                node_binary=node,
                user_id=user_id,
                placeholder_values=placeholder_values,
            )
        )
        harvested = _harvest_kite_session(
            steps,
            seed_instruments=session_kite_instruments,
            seed_holdings=session_kite_holdings,
            seed_positions=session_kite_positions,
            seed_open_orders=session_kite_open_orders,
        )
        # Only echo non-empty indexes — keeps diagnostics tight.
        if harvested["session_kite_instruments"]:
            diagnostics["session_kite_instruments"] = harvested["session_kite_instruments"]
        if harvested["session_kite_holdings"] is not None:
            diagnostics["session_kite_holdings"] = harvested["session_kite_holdings"]
        if harvested["session_kite_positions"] is not None:
            diagnostics["session_kite_positions"] = harvested["session_kite_positions"]
        if harvested["session_kite_open_orders"]:
            diagnostics["session_kite_open_orders"] = harvested["session_kite_open_orders"]
        # The order ticket is managed entirely by the planner; the executor just
        # round-trips it unchanged so it survives to the next turn.
        if isinstance(session_kite_order_ticket, dict) and session_kite_order_ticket:
            diagnostics["session_kite_order_ticket"] = dict(session_kite_order_ticket)

        skipped = bool(diagnostics.get("skipped_tools"))
        return {
            "ok": not skipped,
            "error_detail": "tool_plan_incomplete" if skipped else None,
            "steps": steps,
            "diagnostics": diagnostics,
        }
    except BaseException as exc:
        logger.exception("Kite MCP execution failed for %s", url)
        return {
            "ok": False,
            "error_class": type(exc).__name__,
            "error_detail": _humanize_mcp_failure(root, exc, url),
            "steps": [],
            "diagnostics": {
                "mcp_remote_config_dir": str(
                    _mcp_remote_config_dir(root, user_id=user_id)
                ),
                "executor": "kite.mcp_executor",
            },
        }


def list_mcp_tools_sync(
    *,
    repo_root: Path | None = None,
    mcp_url: str | None = None,
    node_binary: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """List Kite MCP tools via the shared HTTP listing helper."""
    from app.services.zomato.mcp_executor import list_mcp_tools_sync as _shared

    root = repo_root or repo_root_from_settings()
    url = _kite_url(mcp_url)
    return _shared(
        repo_root=root,
        mcp_url=url,
        node_binary=node_binary,
        user_id=user_id,
    )
