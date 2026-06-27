"""Legacy LLM roles still used by the deterministic (tap) path + the food domain:

resolve()  : provider catalog + requests → {resolved, ambiguous, unresolved}  (refs only)
narrate()  : structured turn result → assistant_message

The grocery TEXT path no longer uses these — see router.py, grocery_brain.py, grocery_response.py.
"""

from __future__ import annotations

from typing import Any

from core.agent.llm.base import _complete
from core.agent.llm.prompts import NARRATOR_SYSTEM, RESOLVER_SYSTEM


async def resolve(products: list[dict[str, Any]], requests: list[dict[str, Any]]) -> dict[str, Any]:
    if not requests:
        return {"resolved": [], "ambiguous": [], "unresolved": []}
    out = await _complete(RESOLVER_SYSTEM, {"products": products, "requests": requests})
    return {
        "resolved": out.get("resolved") or [],
        "ambiguous": out.get("ambiguous") or [],
        "unresolved": out.get("unresolved") or [],
    }


async def narrate(
    result: dict[str, Any],
    history: list[dict[str, str]] | None = None,
    mode: str = "text",
) -> str:
    payload = {"mode": mode, "history": (history or [])[-8:], "result": result}
    out = await _complete(NARRATOR_SYSTEM, payload)
    msg = out.get("message")
    # Guard: even if the LLM ignores instructions, never surface a Report ID / raw error code.
    if isinstance(msg, str) and msg.strip() and "report id" not in msg.lower():
        return msg.strip()
    # Deterministic fallback so the turn always replies — and NEVER fakes success when nothing
    # happened (the old blanket "Ho gaya." lied on asks/errors).
    if result.get("note"):
        return str(result["note"])
    if result.get("error_hint"):
        return str(result["error_hint"])
    if result.get("error"):
        return "Sorry, kuch dikkat aa gayi. Dobara try karein?"
    if result.get("status") in ("need_provider", "ambiguous"):
        return "Kaunse app mein — Instamart ya Zepto?"
    return "Ho gaya."
