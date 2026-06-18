"""POC: probe LIVE Swiggy Instamart MCP for the architecture-critical behaviors.

Raw tool calls only (no pipeline code). Writes every call's input + raw output
to swiggy_updated_outputs/poc/ and prints a findings summary. Cart is cleared
before and after. NO checkout / order placement.

Scenarios:
  A. Serviceability — classify each saved address (search + dummy add) so we
     learn the reliable "is this address serviceable?" signal.
  B. Address switch with an existing cart — what update_cart with a new
     selectedAddressId does (move? error? split?), incl. item-not-available.
  C. Quantity cap — probe a low-cap and a high-cap item; does the error reveal
     the max? does the response carry a max field?
  D. Variant disambiguation — search shapes for ambiguous queries (sooji, atta).

Usage: ./.venv/bin/python scripts/poc_swiggy_instamart.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "swiggy_updated_outputs" / "poc"
MCP_URL = os.environ.get("SWIGGY_INSTAMART_MCP_URL", "https://mcp.swiggy.com/im")
USER = os.environ.get("DUMP_USER", "local_admin")

# Candidate saved addresses (from get_addresses). Gurugram vs Delhi → different
# stores → good for cross-store inventory tests.
ADDR = {
    "Starrrs_GGN": "d8ji0f8ukngg9o52c900",
    "Holiday_GGN": "d8lr39c1d96q3mvl1nu0",
    "Room_DEL": "360938440",
    "Work_DEL": "275412200",
    "Home_DEL": "363294542",
}

_n = 0
FINDINGS: list[str] = []


def raw_payload(result: Any) -> dict[str, Any]:
    content = [{"type": getattr(b, "type", None), "text": getattr(b, "text", None)} for b in getattr(result, "content", []) or []]
    return {
        "is_error": bool(getattr(result, "isError", False)),
        "structured_content": getattr(result, "structuredContent", None),
        "content": content,
    }


def err_text(raw: dict[str, Any]) -> str:
    for b in raw.get("content") or []:
        if b.get("text"):
            return b["text"].split("\nReport ID")[0].strip()
    return ""


def write(label: str, tool: str, args: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    global _n
    _n += 1
    (OUT / f"{_n:02d}_{label}.json").write_text(
        json.dumps({"tool": tool, "input": args, "raw": raw}, indent=2, default=str, ensure_ascii=False)
    )
    tag = "ERROR: " + err_text(raw) if raw.get("is_error") else "ok"
    print(f"  [{_n:02d}] {label}: {tag[:90]}")
    return raw


def walk_find(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj and obj[key] not in (None, "", []):
            return obj[key]
        for v in obj.values():
            f = walk_find(v, key)
            if f is not None:
                return f
    elif isinstance(obj, list):
        for v in obj:
            f = walk_find(v, key)
            if f is not None:
                return f
    return None


def top_spin(raw: dict[str, Any]) -> str | None:
    return walk_find(raw.get("structured_content"), "spinId")


async def main() -> None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    proxy = REPO / "node_modules/mcp-remote/dist/proxy.js"
    env = {**os.environ, "MCP_REMOTE_CONFIG_DIR": str(REPO / f".mcp-auth/users/{USER}")}
    params = StdioServerParameters(command="node", args=[str(proxy), MCP_URL], cwd=str(REPO), env=env)
    OUT.mkdir(parents=True, exist_ok=True)

    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            async def call(tool, args):
                res = await asyncio.wait_for(session.call_tool(tool, arguments=args), timeout=90)
                return raw_payload(res)

            async def clear():
                try:
                    await call("clear_cart", {})
                except Exception:
                    pass

            await clear()

            # ── A. SERVICEABILITY ────────────────────────────────────────────
            print("\n[A] Serviceability per address (search shows products? add succeeds?)")
            # harvest a spin from a known-good search at Starrrs
            base = write("A_search_milk_Starrrs", "search_products", {"query": "milk", "addressId": ADDR["Starrrs_GGN"]}, await call("search_products", {"query": "milk", "addressId": ADDR["Starrrs_GGN"]}))
            spin = top_spin(base)
            for name, aid in ADDR.items():
                s = write(f"A_search_milk_{name}", "search_products", {"query": "milk", "addressId": aid}, await call("search_products", {"query": "milk", "addressId": aid}))
                sc = s.get("structured_content") or {}
                prods = sc.get("products") if isinstance(sc, dict) else None
                n_prod = len(prods) if isinstance(prods, list) else 0
                # any in-stock variation?
                instock = bool(walk_find(sc, "isInStockAndAvailable"))
                # dummy add to test serviceability definitively, then clear
                add = write(f"A_add_{name}", "update_cart", {"items": [{"spinId": spin, "quantity": 1}], "selectedAddressId": aid}, await call("update_cart", {"items": [{"spinId": spin, "quantity": 1}], "selectedAddressId": aid}))
                await clear()
                verdict = "SERVICEABLE" if not add.get("is_error") else f"NOT-serviceable ({err_text(add)[:40]})"
                FINDINGS.append(f"A {name}: search_products n={n_prod} instock={instock} | add -> {verdict}")

            # ── B. ADDRESS SWITCH WITH EXISTING CART ─────────────────────────
            print("\n[B] Address switch with an existing cart")
            await clear()
            write("B1_add_X_Starrrs", "update_cart", {"items": [{"spinId": spin, "quantity": 1}], "selectedAddressId": ADDR["Starrrs_GGN"]}, await call("update_cart", {"items": [{"spinId": spin, "quantity": 1}], "selectedAddressId": ADDR["Starrrs_GGN"]}))
            g = write("B2_get_cart", "get_cart", {}, await call("get_cart", {}))
            cart_addr_before = walk_find(g.get("structured_content"), "selectedAddress") or walk_find(g.get("structured_content"), "selectedAddressDetails")
            FINDINGS.append(f"B cart after add @Starrrs: addr={json.dumps(cart_addr_before, default=str)[:80]}")
            # switch to a non-serviceable address (if A found one) — else Holiday
            sw = write("B3_update_at_Holiday", "update_cart", {"items": [{"spinId": spin, "quantity": 1}], "selectedAddressId": ADDR["Holiday_GGN"]}, await call("update_cart", {"items": [{"spinId": spin, "quantity": 1}], "selectedAddressId": ADDR["Holiday_GGN"]}))
            g2 = write("B4_get_cart_after_holiday", "get_cart", {}, await call("get_cart", {}))
            FINDINGS.append(f"B switch->Holiday: update {'ERROR:'+err_text(sw)[:40] if sw.get('is_error') else 'ok'} | cart now addr={json.dumps(walk_find(g2.get('structured_content'),'selectedAddress'), default=str)[:60]}")
            # switch to a DIFFERENT serviceable city (Delhi) with the SAME spin (Gurugram product) — tests cross-store availability
            sw2 = write("B5_update_same_spin_at_DEL", "update_cart", {"items": [{"spinId": spin, "quantity": 1}], "selectedAddressId": ADDR["Work_DEL"]}, await call("update_cart", {"items": [{"spinId": spin, "quantity": 1}], "selectedAddressId": ADDR["Work_DEL"]}))
            g3 = write("B6_get_cart_after_DEL", "get_cart", {}, await call("get_cart", {}))
            FINDINGS.append(f"B switch->Work_DEL same-spin: update {'ERROR:'+err_text(sw2)[:50] if sw2.get('is_error') else 'ok'} | cart={json.dumps((g3.get('structured_content') or {}).get('items'), default=str)[:80]}")
            # the 'clear then re-add at new address' approach with a fresh search at the new address
            await clear()
            del_search = write("B7_search_milk_at_DEL", "search_products", {"query": "milk", "addressId": ADDR["Work_DEL"]}, await call("search_products", {"query": "milk", "addressId": ADDR["Work_DEL"]}))
            del_spin = top_spin(del_search)
            write("B8_add_DELspin_at_DEL", "update_cart", {"items": [{"spinId": del_spin, "quantity": 1}], "selectedAddressId": ADDR["Work_DEL"]}, await call("update_cart", {"items": [{"spinId": del_spin, "quantity": 1}], "selectedAddressId": ADDR["Work_DEL"]}))
            FINDINGS.append(f"B re-add fresh DEL spin @Work_DEL: {'ok' if del_spin else 'no-spin'}")
            await clear()

            # ── C. QUANTITY CAP ──────────────────────────────────────────────
            print("\n[C] Quantity cap probing")
            await clear()
            for q in (1, 2, 3, 5, 10, 20):
                res = write(f"C_milk_qty{q}", "update_cart", {"items": [{"spinId": spin, "quantity": q}], "selectedAddressId": ADDR["Starrrs_GGN"]}, await call("update_cart", {"items": [{"spinId": spin, "quantity": q}], "selectedAddressId": ADDR["Starrrs_GGN"]}))
                if res.get("is_error"):
                    FINDINGS.append(f"C milk cap: qty {q} -> ERROR '{err_text(res)[:60]}'")
                    break
                else:
                    got = walk_find(res.get("structured_content"), "quantity")
                    FINDINGS.append(f"C milk qty {q} -> ok (cart qty={got})")
            await clear()
            # high-cap item: atta
            atta = write("C_search_atta", "search_products", {"query": "aashirvaad atta 5kg", "addressId": ADDR["Starrrs_GGN"]}, await call("search_products", {"query": "aashirvaad atta 5kg", "addressId": ADDR["Starrrs_GGN"]}))
            atta_spin = top_spin(atta)
            for q in (3, 6, 10):
                res = write(f"C_atta_qty{q}", "update_cart", {"items": [{"spinId": atta_spin, "quantity": q}], "selectedAddressId": ADDR["Starrrs_GGN"]}, await call("update_cart", {"items": [{"spinId": atta_spin, "quantity": q}], "selectedAddressId": ADDR["Starrrs_GGN"]}))
                if res.get("is_error"):
                    FINDINGS.append(f"C atta cap: qty {q} -> ERROR '{err_text(res)[:60]}'")
                    break
                FINDINGS.append(f"C atta qty {q} -> ok")
            await clear()

            # ── D. VARIANT DISAMBIGUATION ────────────────────────────────────
            print("\n[D] Variant / disambiguation shapes")
            for q in ("sooji", "atta", "milk"):
                s = write(f"D_search_{q}", "search_products", {"query": q, "addressId": ADDR["Starrrs_GGN"]}, await call("search_products", {"query": q, "addressId": ADDR["Starrrs_GGN"]}))
                sc = s.get("structured_content") or {}
                prods = sc.get("products") or []
                nv = sum(len(p.get("variations") or []) for p in prods if isinstance(p, dict))
                FINDINGS.append(f"D '{q}': {len(prods)} products, {nv} total variations")

            await clear()

    (OUT / "_findings.txt").write_text("\n".join(FINDINGS))
    print("\n================ FINDINGS ================")
    print("\n".join(FINDINGS))
    print(f"\nFiles in {OUT.relative_to(REPO)}/ (cart cleared).")


if __name__ == "__main__":
    asyncio.run(main())
