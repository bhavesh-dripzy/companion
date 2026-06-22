"""The three LLM role functions (Sarvam, JSON mode). Each degrades gracefully if the LLM is down.

route()    : message + recent history → {domain, action, provider_hint, item_refs}
resolve()  : provider catalog + requests → {resolved, ambiguous, unresolved}  (refs only)
narrate()  : structured turn result → assistant_message
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.agent.llm.prompts import NARRATOR_SYSTEM, RESOLVER_SYSTEM, ROUTER_SYSTEM
from core.llm.client import complete_json_object
from core.llm.config import resolve_llm_from_env

logger = logging.getLogger(__name__)


async def _complete(system: str, payload: Any) -> dict[str, Any]:
    llm, reason = resolve_llm_from_env()
    if llm is None:
        logger.warning("agent LLM unavailable: %s", reason)
        return {}
    user = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, ensure_ascii=False, default=str)
    )
    try:
        out = await complete_json_object(user_message=user, system_prompt=system, llm=llm)
        return out if isinstance(out, dict) else {}
    except Exception:
        logger.exception("agent LLM call failed")
        return {}


async def route(message: str, history: list[dict[str, str]]) -> dict[str, Any]:
    payload = {"history": history[-6:], "message": message}
    out = await _complete(ROUTER_SYSTEM, payload)
    return {
        "domain": out.get("domain") or "chitchat",
        "action": out.get("action") or "chitchat",
        "provider_hint": out.get("provider_hint"),
        "item_refs": out.get("item_refs") or [],
        "filters": out.get("filters") if isinstance(out.get("filters"), dict) else None,
    }


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
