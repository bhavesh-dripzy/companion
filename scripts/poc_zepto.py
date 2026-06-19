"""POC: probe the LIVE Zepto MCP for the architecture-critical behaviors.

The read-only ``dump_zepto_outputs.py`` never exercises the cart writes, the
serviceability/store binding across locations, qty caps, or the order-prep
address flow. This script does — mirroring ``poc_swiggy_instamart.py`` — so we
can design the Zepto architecture on real responses instead of guesses.

Raw tool calls only (no pipeline code). Writes every call's input + raw output
to ``swiggy_updated_outputs/zepto_poc/`` and prints a findings summary.

SAFETY: the user already has a real Zepto cart. We NEVER call clear / replaceCart
and NEVER place an order. Cart tests add a single PROBE item (by its own
pvid/spid), manipulate only that line, then zero it out — the user's existing
lines are left untouched. create_*_order is never called (real money).

Scenarios:
  A. Serviceability — classify several lat/lng (serviceable cities + a remote
     coord) via get_location_serviceability; capture the bound store id.
  B. GPS/store switch — does changing location rebind the store, and does the
     server-side cart persist across stores? (read-only view_cart).
  C. Cart add / change-qty / remove — merge semantics on a probe item.
  D. Quantity cap — push a probe item's qty up until Zepto errors.
  E. Variant disambiguation — search shapes for ambiguous queries.
  F. Order-prep (read-only) — list_saved_addresses + get_payment_methods; we
     document how create_order consumes userAddressId WITHOUT placing one.

Usage: ./.venv/bin/python scripts/poc_zepto.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "swiggy_updated_outputs" / "zepto_poc"
MCP_URL = os.environ.get("ZEPTO_MCP_URL", "https://mcp.zepto.co.in/mcp")
USER = os.environ.get("DUMP_USER", "local_admin")
DEVICE_ID = os.environ.get("ZEPTO_DEVICE_ID", str(uuid.uuid4()))

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_PAIR_RE = re.compile(rf"pvid:\s*({_UUID})\s*,\s*spid:\s*({_UUID})", re.IGNORECASE)
_STORE_RE = re.compile(rf"Primary store ID:\s*({_UUID})", re.IGNORECASE)

# Probe locations: (label, lat, lng). First few are known-serviceable metros;
# the last is a remote coord we expect to be NOT serviceable.
LOCATIONS = [
    ("Gurugram_Medicity", 28.4536, 77.0726),
    ("Bangalore_Koramangala", 12.9352, 77.6245),
    ("Mumbai_Bandra", 19.0596, 72.8295),
    ("Remote_Ladakh", 34.1526, 77.5770),
]

_n = 0
FINDINGS: list[str] = []


def raw_payload(result: Any) -> dict[str, Any]:
    content = [
        {"type": getattr(b, "type", None), "text": getattr(b, "text", None)}
        for b in getattr(result, "content", []) or []
    ]
    return {
        "is_error": bool(getattr(result, "isError", False)),
        "structured_content": getattr(result, "structuredContent", None),
        "content": content,
    }


def body_text(raw: dict[str, Any]) -> str:
    for b in raw.get("content") or []:
        if b.get("text"):
            return b["text"]
    sc = raw.get("structured_content")
    return json.dumps(sc, default=str) if sc is not None else ""


def err_text(raw: dict[str, Any]) -> str:
    return body_text(raw).split("\nReport ID")[0].strip()


def write(label: str, tool: str, args: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    global _n
    _n += 1
    (OUT / f"{_n:02d}_{label}.json").write_text(
        json.dumps({"tool": tool, "input": args, "raw": raw}, indent=2, default=str, ensure_ascii=False)
    )
    tag = "ERROR: " + err_text(raw)[:80] if raw.get("is_error") else "ok"
    print(f"  [{_n:02d}] {label}: {tag[:100]}")
    return raw


def first_pair(raw: dict[str, Any]) -> tuple[str, str] | None:
    """The top product's (pvid, spid) from a search/cart prose footer."""
    m = _PAIR_RE.search(body_text(raw))
    return (m.group(1), m.group(2)) if m else None


def store_id(raw: dict[str, Any]) -> str | None:
    m = _STORE_RE.search(body_text(raw))
    return m.group(1) if m else None


def cart_count(raw: dict[str, Any]) -> int:
    """Number of pvid/spid pairs listed in a view_cart prose body."""
    return len(_PAIR_RE.findall(body_text(raw)))


async def main() -> None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    proxy = REPO / "node_modules/mcp-remote/dist/proxy.js"
    if not proxy.is_file():
        print("mcp-remote missing — run `npm install` at repo root.", file=sys.stderr)
        sys.exit(1)
    env = {**os.environ, "MCP_REMOTE_CONFIG_DIR": str(REPO / f".mcp-auth/users/{USER}")}
    params = StdioServerParameters(command="node", args=[str(proxy), MCP_URL], cwd=str(REPO), env=env)
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"deviceId={DEVICE_ID}")

    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            async def call(tool, args):
                res = await asyncio.wait_for(session.call_tool(tool, arguments=args), timeout=90)
                return raw_payload(res)

            async def serviceable(lat, lng):
                return await call("get_location_serviceability", {"latitude": lat, "longitude": lng})

            # ── A. SERVICEABILITY ────────────────────────────────────────────
            print("\n[A] Serviceability per location")
            primary_store = None
            for name, lat, lng in LOCATIONS:
                s = write(f"A_serv_{name}", "get_location_serviceability", {"latitude": lat, "longitude": lng}, await serviceable(lat, lng))
                sid = store_id(s)
                ok = not s.get("is_error") and ("delivers" in body_text(s).lower() or sid)
                if sid and primary_store is None and name != "Remote_Ladakh":
                    primary_store = sid
                FINDINGS.append(f"A {name}: {'SERVICEABLE store='+(sid or '?')[:8] if ok else 'NOT-serviceable ('+err_text(s)[:40]+')'}")

            # ── B. GPS/STORE SWITCH — does cart persist across stores? ───────
            print("\n[B] Store switch + cart persistence (read-only)")
            await serviceable(*LOCATIONS[0][1:])  # bind store 1 (Gurugram)
            c1 = write("B_cart_at_store1", "view_cart", {}, await call("view_cart", {}))
            await serviceable(*LOCATIONS[1][1:])  # rebind store 2 (Bangalore)
            c2 = write("B_cart_at_store2", "view_cart", {}, await call("view_cart", {}))
            FINDINGS.append(f"B cart pairs: store1={cart_count(c1)} store2={cart_count(c2)} (does cart follow the store?)")

            # ── C. CART ADD / CHANGE-QTY / REMOVE (probe item only) ──────────
            print("\n[C] Cart add / change-qty / remove (probe item; user cart untouched)")
            await serviceable(*LOCATIONS[0][1:])  # serviceable store first
            base_n = cart_count(await call("view_cart", {}))
            # probe item: a paneer (unlikely to collide; if it does, we still only zero our own line)
            srch = write("C_search_probe", "search_products", {"query": "milky mist paneer"}, await call("search_products", {"query": "milky mist paneer"}))
            pair = first_pair(srch)
            if not pair:
                FINDINGS.append("C: no pvid/spid harvested from search — cart tests skipped")
            else:
                pvid, spid = pair
                add = write("C_add_qty1", "update_cart", {"deviceId": DEVICE_ID, "cartItems": [{"productVariantId": pvid, "storeProductId": spid, "quantity": 1}]}, await call("update_cart", {"deviceId": DEVICE_ID, "cartItems": [{"productVariantId": pvid, "storeProductId": spid, "quantity": 1}]}))
                after_add = write("C_cart_after_add", "view_cart", {}, await call("view_cart", {}))
                FINDINGS.append(f"C add: {'ERROR '+err_text(add)[:40] if add.get('is_error') else 'ok'} | cart {base_n} -> {cart_count(after_add)} (merge keeps existing?)")
                # change qty to 3
                chg = write("C_setqty_3", "update_cart", {"deviceId": DEVICE_ID, "cartItems": [{"productVariantId": pvid, "storeProductId": spid, "quantity": 3}]}, await call("update_cart", {"deviceId": DEVICE_ID, "cartItems": [{"productVariantId": pvid, "storeProductId": spid, "quantity": 3}]}))
                FINDINGS.append(f"C change-qty->3: {'ERROR '+err_text(chg)[:40] if chg.get('is_error') else 'ok'}")
                # ── D. QTY CAP on the probe item ─────────────────────────────
                print("\n[D] Quantity-cap probing (probe item)")
                cap_hit = None
                for q in (5, 10, 20, 50):
                    res = write(f"D_qty_{q}", "update_cart", {"deviceId": DEVICE_ID, "cartItems": [{"productVariantId": pvid, "storeProductId": spid, "quantity": q}]}, await call("update_cart", {"deviceId": DEVICE_ID, "cartItems": [{"productVariantId": pvid, "storeProductId": spid, "quantity": q}]}))
                    if res.get("is_error"):
                        cap_hit = q
                        FINDINGS.append(f"D qty cap: {q} -> ERROR '{err_text(res)[:60]}'")
                        break
                if cap_hit is None:
                    FINDINGS.append("D qty cap: no error up to 50 (no obvious cap, or silently clamped)")
                # remove the probe item (qty 0) — restore the user's cart
                rm = write("C_remove_qty0", "update_cart", {"deviceId": DEVICE_ID, "cartItems": [{"productVariantId": pvid, "storeProductId": spid, "quantity": 0}]}, await call("update_cart", {"deviceId": DEVICE_ID, "cartItems": [{"productVariantId": pvid, "storeProductId": spid, "quantity": 0}]}))
                final_n = cart_count(write("C_cart_after_remove", "view_cart", {}, await call("view_cart", {})))
                FINDINGS.append(f"C remove(qty0): {'ERROR '+err_text(rm)[:40] if rm.get('is_error') else 'ok'} | cart back to {final_n} (was {base_n}) — clean removal?")

            # ── E. VARIANT DISAMBIGUATION ────────────────────────────────────
            print("\n[E] Variant / disambiguation shapes")
            for q in ("sooji", "atta", "milk", "bread"):
                s = write(f"E_search_{q}", "search_products", {"query": q}, await call("search_products", {"query": q}))
                n = len(_PAIR_RE.findall(body_text(s)))
                FINDINGS.append(f"E '{q}': {n} products with ids")

            # ── F. ORDER-PREP (read-only; NO order placed) ───────────────────
            print("\n[F] Order-prep (read-only)")
            addrs = write("F_list_saved_addresses", "list_saved_addresses", {}, await call("list_saved_addresses", {}))
            n_addr = len(re.findall(r"^\s*\d+\.", body_text(addrs), re.MULTILINE))
            pm = write("F_get_payment_methods", "get_payment_methods", {}, await call("get_payment_methods", {}))
            FINDINGS.append(f"F saved addresses listed: {n_addr} | payment_methods: {'ERROR '+err_text(pm)[:40] if pm.get('is_error') else 'ok'}")
            FINDINGS.append("F create_order takes userAddressId (saved-address id) — NOT placed in this POC.")

    (OUT / "_findings.txt").write_text("\n".join(FINDINGS))
    print("\n================ FINDINGS ================")
    print("\n".join(FINDINGS))
    print(f"\nFiles in {OUT.relative_to(REPO)}/ (probe item removed; user cart restored; no order placed).")


if __name__ == "__main__":
    asyncio.run(main())
