"""
Swiggy Instamart MCP tool execution.

Instamart shares the mcp-remote stdio transport with Swiggy Food and Zomato, but
its tool schemas have two quirks that this executor handles mechanically so the
LLM planner can stay declarative:

* ``update_cart`` takes ``selectedAddressId`` (not ``addressId``).
* ``track_order`` requires ``orderId``, ``lat`` and ``lng``; ``get_addresses``
  does not carry geo, but ``get_cart`` returns it on ``selectedAddressDetails``.

To keep the planner prompt free of mechanical hacks we expose three placeholders
the LLM can drop into any plan and the executor resolves on-the-fly:

* ``__ADDRESS_ID__``  → resolved via ``get_addresses`` (or the UI-selected id).
* ``__ADDRESS_LAT__`` / ``__ADDRESS_LNG__`` → resolved via ``get_cart`` (or
  populated from the picked address if a geo field happens to be present).

All other rule-shaped behaviour (when to confirm, when to retry a stale cart,
which payment method to choose) lives in the LLM prompt, not here.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
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

_DEFAULT_INSTAMART_MCP_URL = "https://mcp.swiggy.com/im"

_MCP_INIT_TIMEOUT_SECONDS = int(
    os.getenv("INSTAMART_MCP_INIT_TIMEOUT_SECONDS")
    or os.getenv("SWIGGY_MCP_INIT_TIMEOUT_SECONDS")
    or os.getenv("ZOMATO_MCP_INIT_TIMEOUT_SECONDS", "20")
)
_MCP_TOOL_TIMEOUT_SECONDS = int(
    os.getenv("INSTAMART_MCP_TOOL_TIMEOUT_SECONDS")
    or os.getenv("SWIGGY_MCP_TOOL_TIMEOUT_SECONDS")
    or os.getenv("ZOMATO_MCP_TOOL_TIMEOUT_SECONDS", "30")
)

# Instamart placeholder vocabulary the planner may use freely.
_ADDRESS_ID_PLACEHOLDER = "__ADDRESS_ID__"
_ADDRESS_LAT_PLACEHOLDER = "__ADDRESS_LAT__"
_ADDRESS_LNG_PLACEHOLDER = "__ADDRESS_LNG__"
_ORDER_ID_PLACEHOLDER = "__ORDER_ID__"
_GEO_PLACEHOLDERS = frozenset({_ADDRESS_LAT_PLACEHOLDER, _ADDRESS_LNG_PLACEHOLDER})
_PLACEHOLDERS = frozenset(
    {
        _ADDRESS_ID_PLACEHOLDER,
        _ADDRESS_LAT_PLACEHOLDER,
        _ADDRESS_LNG_PLACEHOLDER,
        _ORDER_ID_PLACEHOLDER,
    }
)

# Per-tool address argument key (Instamart's small but annoying divergence).
_ADDRESS_KEY_BY_TOOL: dict[str, str] = {
    "search_products": "addressId",
    "your_go_to_items": "addressId",
    "update_cart": "selectedAddressId",
    "checkout": "addressId",
}

# Tools whose schemas require a delivery address.
_NEEDS_ADDRESS_TOOLS = frozenset(_ADDRESS_KEY_BY_TOOL.keys())

# Tools that need lat/lng on top of an address.
_NEEDS_GEO_TOOLS = frozenset({"track_order"})

# Tools that take an ``orderId`` we can default to the user's most recent order.
# ``confirm_order``/``check_payment_status`` are intentionally excluded — those
# require ids surfaced by a specific ``checkout`` response and must not be
# silently swapped for a different order's id.
_NEEDS_ORDER_ID_TOOLS = frozenset({"track_order", "get_order_details"})


def _instamart_url(mcp_url: str | None) -> str:
    return (
        (mcp_url or "").strip()
        or os.getenv("SWIGGY_INSTAMART_MCP_URL", "").strip()
        or _DEFAULT_INSTAMART_MCP_URL
    )


def _collect_addresses(blob: Any) -> list[dict[str, Any]]:
    """Walk an MCP tool envelope for saved-address dictionaries."""
    addresses: list[dict[str, Any]] = []
    for obj in _walk_json(blob):
        if not isinstance(obj, dict):
            continue
        raw = (
            obj.get("addresses")
            or obj.get("user_addresses")
            or obj.get("saved_addresses")
        )
        if isinstance(raw, list):
            addresses.extend([a for a in raw if isinstance(a, dict)])
            if addresses:
                break
    return addresses


def _match_address(
    addresses: list[dict[str, Any]],
    *,
    target_id: str | None,
    location_text: str | None,
) -> dict[str, Any] | None:
    """Pick the best saved-address record: explicit id, then label, then first."""
    if not addresses:
        return None
    target_id_clean = (target_id or "").strip()
    if target_id_clean:
        for addr in addresses:
            for key in ("addressId", "address_id", "id"):
                if str(addr.get(key) or "").strip() == target_id_clean:
                    return addr
    location_clean = (location_text or "").strip().lower()
    if location_clean:
        for addr in addresses:
            for key in (
                "addressTag",
                "address_tag",
                "addressCategory",
                "address_category",
                "name",
                "label",
            ):
                if str(addr.get(key) or "").strip().lower() == location_clean:
                    return addr
        for addr in addresses:
            line = str(
                addr.get("addressLine") or addr.get("address_line") or ""
            ).strip().lower()
            if location_clean in line:
                return addr
    return addresses[0]


def _address_id_of(addr: dict[str, Any] | None) -> str | None:
    if not isinstance(addr, dict):
        return None
    for key in ("addressId", "address_id", "id"):
        val = str(addr.get(key) or "").strip()
        if val:
            return val
    return None


def _coords_of(addr: dict[str, Any] | None) -> tuple[float, float] | None:
    """Extract (lat, lng) from a saved-address record, tolerating key shapes."""
    if not isinstance(addr, dict):
        return None
    location = addr.get("location") if isinstance(addr.get("location"), dict) else {}
    candidates = (
        (location.get("latitude"), location.get("longitude")),
        (location.get("lat"), location.get("lng")),
        (addr.get("lat"), addr.get("lng")),
        (addr.get("latitude"), addr.get("longitude")),
    )
    for lat, lng in candidates:
        if lat is None or lng is None:
            continue
        try:
            return float(lat), float(lng)
        except (TypeError, ValueError):
            continue
    return None


def _coords_from_cart_payload(envelope: dict[str, Any]) -> tuple[float, float] | None:
    """Pull (lat, lng) from a ``get_cart``/``update_cart`` envelope."""
    if not isinstance(envelope, dict):
        return None
    for obj in _walk_json(envelope.get("data")):
        if not isinstance(obj, dict):
            continue
        coords = _coords_of(obj)
        if coords:
            return coords
        details = obj.get("selectedAddressDetails")
        if isinstance(details, dict):
            coords = _coords_of(details)
            if coords:
                return coords
    return None


def _normalize_plan(tool_plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Coerce planner output into Instamart-shaped arguments.

    * Picks the right address-key per tool so the LLM can stay declarative.
    * Adds ``lat``/``lng`` placeholders to ``track_order`` when the LLM didn't.
    """
    cleaned: list[dict[str, Any]] = []
    for raw in tool_plan:
        if not isinstance(raw, dict):
            continue
        tool = str(raw.get("tool") or "").strip()
        if not tool:
            continue
        args = dict(raw.get("arguments") or {})
        # Only remap the address key for tools whose schema we're explicitly
        # normalizing. For everything else (delete_address, confirm_order, etc.)
        # the LLM's literal ``addressId`` must be preserved exactly.
        if tool in _NEEDS_ADDRESS_TOOLS:
            addr_value = (
                args.pop("address_id", None)
                or args.pop("addressId", None)
                or args.pop("selectedAddressId", None)
                or args.pop("selected_address_id", None)
            )
            target_key = _ADDRESS_KEY_BY_TOOL[tool]
            args[target_key] = addr_value or _ADDRESS_ID_PLACEHOLDER
        if tool in _NEEDS_GEO_TOOLS:
            if args.get("lat") in ("", None):
                args["lat"] = _ADDRESS_LAT_PLACEHOLDER
            if args.get("lng") in ("", None):
                args["lng"] = _ADDRESS_LNG_PLACEHOLDER
        if tool in _NEEDS_ORDER_ID_TOOLS:
            if args.get("orderId") in ("", None):
                args["orderId"] = _ORDER_ID_PLACEHOLDER
        cleaned.append({"tool": tool, "arguments": args})
    return cleaned


def _collect_orders(blob: Any) -> list[dict[str, Any]]:
    """Walk an MCP envelope for the ``get_orders`` list of order records."""
    for obj in _walk_json(blob):
        if not isinstance(obj, dict):
            continue
        raw = obj.get("orders") or obj.get("order_list")
        if isinstance(raw, list) and raw:
            return [o for o in raw if isinstance(o, dict)]
    return []


def _pick_order_id(orders: list[dict[str, Any]]) -> str | None:
    """Prefer an active order, else the most recent record."""
    if not orders:
        return None
    for order in orders:
        if order.get("isActive") or order.get("active"):
            oid = order.get("orderId") or order.get("order_id") or order.get("id")
            if oid:
                return str(oid).strip() or None
    first = orders[0]
    oid = first.get("orderId") or first.get("order_id") or first.get("id")
    return str(oid).strip() if oid else None


def _mentions_instamart_placeholder(obj: Any) -> bool:
    if isinstance(obj, str):
        return obj in _PLACEHOLDERS
    if isinstance(obj, dict):
        return any(_mentions_instamart_placeholder(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_mentions_instamart_placeholder(i) for i in obj)
    return False


def _unresolved_instamart_placeholders(obj: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, str):
        if obj in _PLACEHOLDERS:
            found.add(obj)
        return found
    if isinstance(obj, dict):
        for value in obj.values():
            found |= _unresolved_instamart_placeholders(value)
    elif isinstance(obj, list):
        for value in obj:
            found |= _unresolved_instamart_placeholders(value)
    return found


async def _run_instamart_session(
    plan: list[dict[str, Any]],
    *,
    effective_address_id: str | None,
    repo_root: Path,
    mcp_url: str,
    node_binary: str,
    user_id: str | None,
    location_text: str | None,
    session_placeholders: dict[str, str] | None = None,
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

    # Seed with persisted IDs from prior turn (cart_id, order_id, etc.).
    # The current-turn address always wins over a stale seed.
    placeholder_values: dict[str, Any] = {}
    if isinstance(session_placeholders, dict):
        for k, v in session_placeholders.items():
            if isinstance(k, str) and k.startswith("__") and k.endswith("__"):
                if isinstance(v, (str, int)) and str(v).strip():
                    placeholder_values[k] = str(v).strip()
    if effective_address_id:
        placeholder_values[_ADDRESS_ID_PLACEHOLDER] = effective_address_id

    step_results: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {
        "requested_address_id": effective_address_id,
        "requested_location_text": location_text,
        "executor": "instamart.mcp_executor",
    }
    if placeholder_values:
        diagnostics["seeded_placeholders"] = sorted(placeholder_values.keys())

    plan_tools = {
        str(step.get("tool") or "").strip()
        for step in plan
        if isinstance(step, dict)
    }
    address_prefetched = "get_addresses" in plan_tools
    geo_prefetched = bool(plan_tools & {"get_cart", "update_cart"})
    orders_prefetched = "get_orders" in plan_tools

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

            async def prefetch_address() -> None:
                nonlocal address_prefetched, step_index
                if address_prefetched:
                    return
                address_prefetched = True
                envelope = await call_tool("get_addresses", {})
                step_results.append(
                    {
                        "tool": "get_addresses(auto)",
                        "arguments": {},
                        "step_index": step_index,
                        "result": envelope,
                    }
                )
                step_index += 1
                chosen = _match_address(
                    _collect_addresses(envelope.get("data")),
                    target_id=effective_address_id,
                    location_text=location_text,
                )
                aid = _address_id_of(chosen)
                if aid and _ADDRESS_ID_PLACEHOLDER not in placeholder_values:
                    placeholder_values[_ADDRESS_ID_PLACEHOLDER] = aid
                    diagnostics["resolved_address_id"] = aid
                coords = _coords_of(chosen)
                if coords:
                    placeholder_values[_ADDRESS_LAT_PLACEHOLDER] = coords[0]
                    placeholder_values[_ADDRESS_LNG_PLACEHOLDER] = coords[1]
                    diagnostics["resolved_geo"] = list(coords)
                if not aid:
                    diagnostics["auto_address_failure"] = (
                        "Could not extract address_id from get_addresses response."
                    )

            async def prefetch_geo() -> None:
                nonlocal geo_prefetched, step_index
                if geo_prefetched:
                    return
                geo_prefetched = True
                envelope = await call_tool("get_cart", {})
                step_results.append(
                    {
                        "tool": "get_cart(auto)",
                        "arguments": {},
                        "step_index": step_index,
                        "result": envelope,
                    }
                )
                step_index += 1
                coords = _coords_from_cart_payload(envelope)
                if coords:
                    placeholder_values[_ADDRESS_LAT_PLACEHOLDER] = coords[0]
                    placeholder_values[_ADDRESS_LNG_PLACEHOLDER] = coords[1]
                    diagnostics["resolved_geo"] = list(coords)
                else:
                    diagnostics["auto_geo_failure"] = (
                        "Could not extract lat/lng from get_cart response."
                    )

            async def prefetch_orders() -> None:
                nonlocal orders_prefetched, step_index
                if orders_prefetched:
                    return
                orders_prefetched = True
                # Default to active orders for tracking; fall back to all if none.
                envelope = await call_tool("get_orders", {"activeOnly": True})
                step_results.append(
                    {
                        "tool": "get_orders(auto)",
                        "arguments": {"activeOnly": True},
                        "step_index": step_index,
                        "result": envelope,
                    }
                )
                step_index += 1
                orders = _collect_orders(envelope.get("data"))
                if not orders:
                    envelope = await call_tool("get_orders", {})
                    step_results.append(
                        {
                            "tool": "get_orders(auto)",
                            "arguments": {},
                            "step_index": step_index,
                            "result": envelope,
                        }
                    )
                    step_index += 1
                    orders = _collect_orders(envelope.get("data"))
                oid = _pick_order_id(orders)
                if oid:
                    placeholder_values[_ORDER_ID_PLACEHOLDER] = oid
                    diagnostics["resolved_order_id"] = oid
                else:
                    diagnostics["auto_order_failure"] = (
                        "Could not extract orderId from get_orders response."
                    )

            for raw in plan:
                if not isinstance(raw, dict):
                    continue
                tool = str(raw.get("tool") or "").strip()
                if not tool:
                    continue
                base_args = copy.deepcopy(raw.get("arguments") or {})

                if tool == "get_addresses":
                    address_prefetched = True
                if tool in {"get_cart", "update_cart"}:
                    geo_prefetched = True
                if tool == "get_orders":
                    orders_prefetched = True

                exec_args = substitute_placeholders(
                    copy.deepcopy(base_args), placeholder_values
                )
                if _mentions_instamart_placeholder(exec_args):
                    unresolved = _unresolved_instamart_placeholders(exec_args)
                    if _ADDRESS_ID_PLACEHOLDER in unresolved or (
                        unresolved & _GEO_PLACEHOLDERS
                    ):
                        # ``get_addresses`` resolves both the id and coords in
                        # one shot, so try it first whenever any address-shaped
                        # placeholder is missing.
                        await prefetch_address()
                        exec_args = substitute_placeholders(
                            copy.deepcopy(base_args), placeholder_values
                        )
                        unresolved = _unresolved_instamart_placeholders(exec_args)
                    if unresolved & _GEO_PLACEHOLDERS:
                        await prefetch_geo()
                        exec_args = substitute_placeholders(
                            copy.deepcopy(base_args), placeholder_values
                        )
                        unresolved = _unresolved_instamart_placeholders(exec_args)
                    if _ORDER_ID_PLACEHOLDER in unresolved:
                        await prefetch_orders()
                        exec_args = substitute_placeholders(
                            copy.deepcopy(base_args), placeholder_values
                        )
                        unresolved = _unresolved_instamart_placeholders(exec_args)
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

                if tool == "get_addresses":
                    chosen = _match_address(
                        _collect_addresses(envelope.get("data")),
                        target_id=effective_address_id,
                        location_text=location_text,
                    )
                    aid = _address_id_of(chosen)
                    if aid and _ADDRESS_ID_PLACEHOLDER not in placeholder_values:
                        placeholder_values[_ADDRESS_ID_PLACEHOLDER] = aid
                        diagnostics["resolved_address_id"] = aid
                    coords = _coords_of(chosen)
                    if coords:
                        placeholder_values[_ADDRESS_LAT_PLACEHOLDER] = coords[0]
                        placeholder_values[_ADDRESS_LNG_PLACEHOLDER] = coords[1]
                        diagnostics["resolved_geo"] = list(coords)
                if tool in {"get_cart", "update_cart"}:
                    coords = _coords_from_cart_payload(envelope)
                    if coords:
                        placeholder_values[_ADDRESS_LAT_PLACEHOLDER] = coords[0]
                        placeholder_values[_ADDRESS_LNG_PLACEHOLDER] = coords[1]
                        diagnostics["resolved_geo"] = list(coords)
                if tool == "get_orders":
                    orders = _collect_orders(envelope.get("data"))
                    oid = _pick_order_id(orders)
                    if oid and _ORDER_ID_PLACEHOLDER not in placeholder_values:
                        placeholder_values[_ORDER_ID_PLACEHOLDER] = oid
                        diagnostics["resolved_order_id"] = oid

    diagnostics["resolved_placeholders"] = sorted(placeholder_values.keys())
    # Surface values so pipeline can hand them back as session_state for next turn.
    diagnostics["session_placeholders"] = {
        k: str(v) for k, v in placeholder_values.items() if v is not None
    }
    return step_results, diagnostics


# ---------------------------------------------------------------------------
# Session-state harvesting — Zepto-style LRUs of products / addresses / cart.
#
# Layered on top of the auto-prefetch executor without disturbing its
# placeholder-resolution logic. The harvesters walk completed step results,
# extract user-meaningful entities, and merge them into recency-bounded lists
# the pipeline can round-trip on every turn. The planner reads these on the
# next turn so it can:
#   * copy a literal ``spinId`` out of memory instead of re-searching, and
#   * answer "what's in my cart" without an extra ``get_cart`` call.
# ---------------------------------------------------------------------------

_MAX_REMEMBERED_PRODUCTS = 60
_MAX_REMEMBERED_ADDRESSES = 24


def _coerce_price_inr(price_obj: Any) -> tuple[float | None, float | None]:
    """Extract ``(offer_price_inr, mrp_inr)`` from a price dict."""
    if not isinstance(price_obj, dict):
        return (None, None)
    mrp = price_obj.get("mrp")
    offer = price_obj.get("offerPrice") or price_obj.get("offer_price") or mrp
    try:
        mrp_inr = float(mrp) if mrp is not None else None
    except (TypeError, ValueError):
        mrp_inr = None
    try:
        offer_inr = float(offer) if offer is not None else mrp_inr
    except (TypeError, ValueError):
        offer_inr = mrp_inr
    return (offer_inr, mrp_inr)


def _instamart_products_from_search(envelope: dict[str, Any], *, query: str | None) -> list[dict[str, Any]]:
    """Flatten a ``search_products`` envelope into one entry per variant.

    Instamart returns products with multiple ``variations`` (pack sizes).
    The card layer wants one card per variant since each is a unique
    ``spinId`` with its own price; the planner also needs spinId-keyed
    rows in memory so a follow-up "add the 1L pack" resolves cleanly.
    """
    if not isinstance(envelope, dict):
        return []
    data = envelope.get("data")
    if not isinstance(data, dict):
        return []
    products = data.get("products")
    if not isinstance(products, list):
        return []
    out: list[dict[str, Any]] = []
    for prod in products:
        if not isinstance(prod, dict):
            continue
        parent_name = str(prod.get("displayName") or "").strip()
        brand = str(prod.get("brand") or "").strip()
        product_id = str(prod.get("productId") or "").strip()
        parent_product_id = str(prod.get("parentProductId") or "").strip()
        is_promoted = bool(prod.get("isPromoted"))
        variations = prod.get("variations") if isinstance(prod.get("variations"), list) else []
        for var in variations:
            if not isinstance(var, dict):
                continue
            spin_id = str(var.get("spinId") or "").strip()
            if not spin_id:
                continue
            offer_inr, mrp_inr = _coerce_price_inr(var.get("price"))
            name = (
                str(var.get("displayName") or "").strip()
                or parent_name
            )
            row: dict[str, Any] = {
                "spin_id": spin_id,
                "product_id": product_id or None,
                "parent_product_id": parent_product_id or None,
                "name": name,
                "brand": str(var.get("brandName") or brand or "").strip() or None,
                "quantity_description": str(var.get("quantityDescription") or "").strip() or None,
                "price_inr": offer_inr,
                "mrp_inr": mrp_inr,
                "image_url": str(var.get("imageUrl") or "").strip() or None,
                "in_stock": bool(var.get("isInStockAndAvailable")),
                "is_promoted": is_promoted,
                "query": (query or None),
            }
            out.append(row)
    return out


def _instamart_addresses_from_envelope(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten ``get_addresses`` into the session LRU shape."""
    if not isinstance(envelope, dict):
        return []
    data = envelope.get("data")
    if not isinstance(data, dict):
        return []
    raw = data.get("addresses") or data.get("user_addresses") or []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for addr in raw:
        if not isinstance(addr, dict):
            continue
        aid = (
            str(addr.get("id") or "").strip()
            or str(addr.get("addressId") or "").strip()
            or str(addr.get("address_id") or "").strip()
        )
        if not aid:
            continue
        out.append(
            {
                "address_id": aid,
                "label": str(addr.get("addressTag") or "").strip() or None,
                "category": str(addr.get("addressCategory") or "").strip() or None,
                "address_line": str(addr.get("addressLine") or "").strip() or None,
                "phone_number": str(addr.get("phoneNumber") or "").strip() or None,
            }
        )
    return out


def _instamart_cart_snapshot(
    envelope: dict[str, Any],
    *,
    session_products: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Build a cart snapshot from a ``get_cart`` / ``update_cart`` envelope.

    Falls back gracefully when the cart is empty or the server returns a
    non-fatal error (Instamart's ``get_cart`` returns ``is_error=True`` on
    a brand-new cart — treat that as "empty"); the snapshot shape stays
    consistent so the UI can always render *something*.
    """
    if not isinstance(envelope, dict):
        return None
    by_spin: dict[str, dict[str, Any]] = {}
    for p in session_products or []:
        spin = p.get("spin_id")
        if isinstance(spin, str):
            by_spin[spin] = p

    data = envelope.get("data")
    is_err = bool(envelope.get("is_error"))
    summary_text: str | None = None
    if isinstance(data, str):
        summary_text = data.strip() or None

    if is_err and not isinstance(data, dict):
        # An empty / expired cart is the most common error here.
        return {
            "is_empty": True,
            "items": [],
            "grand_total_paise": 0,
            "summary_text": summary_text,
            "selected_address_id": None,
        }

    if not isinstance(data, dict):
        return None

    # Instamart's cart payload nests differently depending on the endpoint
    # variant; walk a few common keys for the line items.
    items_raw: list[Any] = []
    for cart_key in ("items", "cartItems", "cart_items"):
        v = data.get(cart_key)
        if isinstance(v, list):
            items_raw = v
            break
    if not items_raw:
        # Sometimes wrapped under ``cart`` / ``data``.
        inner = data.get("cart") if isinstance(data.get("cart"), dict) else None
        if inner:
            for cart_key in ("items", "cartItems", "cart_items"):
                v = inner.get(cart_key)
                if isinstance(v, list):
                    items_raw = v
                    break

    items: list[dict[str, Any]] = []
    computed_total_paise = 0
    has_priced = False
    for it in items_raw:
        if not isinstance(it, dict):
            continue
        spin = (
            str(it.get("spinId") or it.get("spin_id") or it.get("variantId") or "").strip()
            or None
        )
        try:
            qty = int(it.get("quantity") or it.get("qty") or 1)
        except (TypeError, ValueError):
            qty = 1
        offer_inr, mrp_inr = _coerce_price_inr(it.get("price"))
        # Some envelopes store price flat at the item level (in INR or paise).
        if offer_inr is None:
            for k in ("offerPrice", "offer_price", "price_inr", "price"):
                v = it.get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    offer_inr = float(v)
                    break
        sess = by_spin.get(spin) if isinstance(spin, str) else None
        name = (
            str(it.get("displayName") or "").strip()
            or str(it.get("name") or "").strip()
            or ((sess or {}).get("name") if sess else None)
        )
        image_url = (
            str(it.get("imageUrl") or "").strip()
            or ((sess or {}).get("image_url") if sess else None)
        )
        pack = (
            str(it.get("quantityDescription") or "").strip()
            or ((sess or {}).get("quantity_description") if sess else None)
        )
        price_final = offer_inr if offer_inr is not None else (sess or {}).get("price_inr") if sess else None
        if isinstance(price_final, (int, float)):
            computed_total_paise += int(round(float(price_final) * 100)) * qty
            has_priced = True
        items.append(
            {
                "spin_id": spin,
                "quantity": qty,
                "name": name,
                "brand": ((sess or {}).get("brand") if sess else None) or str(it.get("brand") or "").strip() or None,
                "price_inr": price_final,
                "mrp_inr": mrp_inr if mrp_inr is not None else ((sess or {}).get("mrp_inr") if sess else None),
                "image_url": image_url or None,
                "quantity_description": pack or None,
            }
        )

    # Pull the server's own total whenever it's surfaced; else use ours.
    grand_total_paise: int | None = None
    for src in (data, data.get("bill") if isinstance(data.get("bill"), dict) else None):
        if not isinstance(src, dict):
            continue
        for k in (
            "grandTotalPaise",
            "grand_total_paise",
            "billTotalPaise",
            "totalPaise",
        ):
            v = src.get(k)
            if isinstance(v, (int, float)):
                grand_total_paise = int(v)
                break
        if grand_total_paise is not None:
            break
        for k in ("grandTotal", "billTotal", "total", "subtotal"):
            v = src.get(k)
            if isinstance(v, (int, float)):
                # Heuristic: Instamart prices in this MCP are INR-denominated
                # (we saw mrp=72 for milk above), so treat plain numbers as INR.
                grand_total_paise = int(round(float(v) * 100))
                break
        if grand_total_paise is not None:
            break
    if grand_total_paise is None and has_priced:
        grand_total_paise = computed_total_paise

    selected_address_id: str | None = None
    sel = data.get("selectedAddressDetails")
    if isinstance(sel, dict):
        aid = sel.get("id") or sel.get("addressId") or sel.get("address_id")
        if aid:
            selected_address_id = str(aid).strip() or None

    return {
        "is_empty": not items,
        "items": items,
        "grand_total_paise": grand_total_paise,
        "summary_text": summary_text if not items else None,
        "selected_address_id": selected_address_id,
    }


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


def _harvest_instamart_session(
    steps: list[dict[str, Any]],
    *,
    seed_products: list[dict[str, Any]] | None,
    seed_addresses: list[dict[str, Any]] | None,
    seed_cart: dict[str, Any] | None,
) -> dict[str, Any]:
    """Walk all step envelopes and return updated session indexes."""
    products = list(seed_products or [])
    addresses = list(seed_addresses or [])
    cart: dict[str, Any] | None = dict(seed_cart) if isinstance(seed_cart, dict) else None

    for step in steps:
        if not isinstance(step, dict):
            continue
        tool = str(step.get("tool") or "").strip().split("(", 1)[0]  # strip "(auto)" suffix
        envelope = step.get("result") if isinstance(step.get("result"), dict) else {}
        if tool == "search_products" or tool == "your_go_to_items":
            args = step.get("arguments") or {}
            query = None
            if isinstance(args, dict):
                query = str(args.get("query") or "").strip() or None
            new_products = _instamart_products_from_search(envelope, query=query)
            if new_products:
                products = _merge_recent(
                    products, new_products, key="spin_id", cap=_MAX_REMEMBERED_PRODUCTS
                )
        elif tool == "get_addresses":
            new_addresses = _instamart_addresses_from_envelope(envelope)
            if new_addresses:
                addresses = _merge_recent(
                    addresses, new_addresses, key="address_id", cap=_MAX_REMEMBERED_ADDRESSES
                )
        elif tool in {"get_cart", "update_cart"}:
            snap = _instamart_cart_snapshot(envelope, session_products=products)
            if snap is not None:
                cart = snap
        elif tool == "clear_cart":
            # ``clear_cart`` has no response payload to parse, but a non-error
            # call unambiguously means the cart is now empty. Without this
            # the next turn would still echo the pre-clear cart from memory
            # and the assistant would contradict itself ("cleared" + the
            # cart card still showing items).
            if not envelope.get("is_error"):
                cart = {
                    "is_empty": True,
                    "items": [],
                    "grand_total_paise": 0,
                    "summary_text": None,
                    "selected_address_id": (cart or {}).get("selected_address_id"),
                }

    return {
        "session_instamart_products": products,
        "session_instamart_addresses": addresses,
        "session_instamart_cart": cart,
    }


def run_tool_plan_sync(
    tool_plan: list[dict[str, Any]],
    *,
    address_id: str | None = None,
    repo_root: Path | None = None,
    mcp_url: str | None = None,
    node_binary: str | None = None,
    user_id: str | None = None,
    location_text: str | None = None,
    session_placeholders: dict[str, str] | None = None,
    session_instamart_products: list[dict[str, Any]] | None = None,
    session_instamart_addresses: list[dict[str, Any]] | None = None,
    session_instamart_cart: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a Swiggy Instamart MCP tool plan."""
    root = repo_root or repo_root_from_settings()
    url = _instamart_url(mcp_url)
    node = node_binary or os.getenv("NODE_BINARY", "node")
    normalized = _normalize_plan(tool_plan)

    try:
        steps, diagnostics = asyncio.run(
            _run_instamart_session(
                normalized,
                effective_address_id=address_id.strip() if address_id else None,
                repo_root=root,
                mcp_url=url,
                node_binary=node,
                user_id=user_id,
                location_text=location_text,
                session_placeholders=session_placeholders,
            )
        )
        harvested = _harvest_instamart_session(
            steps,
            seed_products=session_instamart_products,
            seed_addresses=session_instamart_addresses,
            seed_cart=session_instamart_cart,
        )
        # Only echo non-empty indexes — keeps diagnostics tight.
        if harvested["session_instamart_products"]:
            diagnostics["session_instamart_products"] = harvested["session_instamart_products"]
        if harvested["session_instamart_addresses"]:
            diagnostics["session_instamart_addresses"] = harvested["session_instamart_addresses"]
        if harvested["session_instamart_cart"] is not None:
            diagnostics["session_instamart_cart"] = harvested["session_instamart_cart"]

        skipped = bool(diagnostics.get("skipped_tools"))
        return {
            "ok": not skipped,
            "error_detail": "tool_plan_incomplete" if skipped else None,
            "steps": steps,
            "diagnostics": diagnostics,
        }
    except BaseException as exc:
        logger.exception("Instamart MCP execution failed for %s", url)
        return {
            "ok": False,
            "error_class": type(exc).__name__,
            "error_detail": _humanize_mcp_failure(root, exc, url),
            "steps": [],
            "diagnostics": {
                "mcp_remote_config_dir": str(
                    _mcp_remote_config_dir(root, user_id=user_id)
                ),
                "executor": "instamart.mcp_executor",
            },
        }


def list_mcp_tools_sync(
    *,
    repo_root: Path | None = None,
    mcp_url: str | None = None,
    node_binary: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """List Instamart MCP tools via the shared HTTP listing helper."""
    from app.services.zomato.mcp_executor import list_mcp_tools_sync as _shared

    root = repo_root or repo_root_from_settings()
    url = _instamart_url(mcp_url)
    return _shared(
        repo_root=root,
        mcp_url=url,
        node_binary=node_binary,
        user_id=user_id,
    )
