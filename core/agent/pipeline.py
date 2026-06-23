"""Turn pipeline — the conductor behind POST /api/agent/turn.

load state → Router(LLM) → DomainHandler → Narrator(LLM) → save → TurnResponse. The LLM only
routes/narrates; the DomainHandler + CartStrategies do the deterministic work. A `client_action`
(UI tap) bypasses the Router (carries exact ids).
"""

from __future__ import annotations

from typing import Any

from core.agent.contracts import fmt_minor
from core.agent.debug import log, short
from core.agent.domains.grocery import HANDLER as GROCERY
from core.agent.errors import user_message
from core.agent.events import Emit, noop, say
from core.agent.llm import narrate, route
from core.agent.state import STORE, ConversationState

_GROCERY_ACTIONS = {"search", "add", "set_qty", "remove", "show_cart", "switch_context", "checkout"}


async def run_turn(
    *,
    user_id: str,
    chat_id: str,
    message: str,
    mode: str = "text",
    client_action: dict[str, Any] | None = None,
    address_ids: dict[str, str] | None = None,
    emit: Emit | None = None,
) -> dict[str, Any]:
    emit = emit or noop  # streaming endpoint passes a real sink; plain endpoint runs identically
    state = STORE.load(user_id, chat_id)
    _apply_address_ids(state, address_ids)  # FE-selected address (e.g. sidebar) is authoritative
    log.debug(
        "── TURN user=%s chat=%s mode=%s %s=%r",
        user_id,
        chat_id,
        mode,
        "action" if client_action else "msg",
        client_action or short(message, 90),
    )

    if client_action:
        intent = _intent_from_action(client_action, state)
        log.debug("INTENT (tap) %s", short(intent))
    else:
        state.add_history("user", message)
        intent = await route(message, state.recent_history())
        intent["raw_query"] = message
        log.debug(
            "ROUTER → domain=%s action=%s provider_hint=%s item_refs=%s",
            intent.get("domain"),
            intent.get("action"),
            intent.get("provider_hint"),
            short(intent.get("item_refs")),
        )

    # The handler narrates its own progress (per-tool `say` + `cards`, in completion order) via emit.
    result = await _dispatch(state, intent, emit)
    log.debug(
        "RESULT status=%s error=%s cards=%d attachments=%d pending=%s",
        result.get("status"),
        result.get("error"),
        len(result.get("cards", [])),
        len(result.get("attachments", [])),
        bool(result.get("pending_action")),
    )
    assistant = await narrate(_for_narrator(intent, result, message), state.recent_history(), mode)
    log.debug("NARRATOR → %s", short(assistant, 120))
    state.add_history("assistant", assistant)
    if intent["domain"] in ("grocery", "food"):
        state.active_domain = intent["domain"]
    STORE.save(state)
    await emit(say(assistant))  # the closing line types out last (after any cards have landed)
    return _response(state, chat_id, assistant, intent, result)


def _apply_address_ids(state: ConversationState, address_ids: dict[str, str] | None) -> None:
    """Honor the FE-selected delivery address per provider (the user can change it from the sidebar,
    which the agent otherwise can't see). The address the user sees is authoritative for cart ops."""
    for pid, aid in (address_ids or {}).items():
        if not aid:
            continue
        ps = state.provider_session.setdefault(pid, {})
        if ps.get("address_id") != aid:
            ps["address_id"] = aid
            ps.pop("lat", None)  # re-derived from the new address when needed
            ps.pop("lng", None)
            ps.pop("label", None)  # stale label for the old address
            log.debug("ADDRESS override %s → %s (from FE)", pid, aid)


async def _dispatch(
    state: ConversationState, intent: dict[str, Any], emit: Emit = noop
) -> dict[str, Any]:
    domain = intent["domain"]
    if domain == "grocery":
        return await GROCERY.handle(state, intent, emit)
    if domain == "chitchat":
        return {"domain": "chitchat", "status": "ok", "cards": [], "error": None}
    return {
        "domain": domain,
        "status": "unsupported",
        "cards": [],
        "error": None,
        "message": f"{domain} abhi available nahi hai — filhaal grocery (Instamart/Zepto) try karein.",
    }


def _intent_from_action(action: dict[str, Any], state: ConversationState) -> dict[str, Any]:
    """A UI tap → an Intent without the LLM. Carries exact provider + item_id/ref."""
    t = action.get("type")
    payload = action.get("payload", {})
    mapping = {"set_qty": "set_qty", "add": "add", "remove": "remove", "show_cart": "show_cart"}
    return {
        "domain": payload.get("domain", "grocery"),
        "action": mapping.get(t, t or "show_cart"),
        "provider_hint": payload.get("provider"),
        # Carry the tapped item's full identity (id + secondary_id + name) so the add/qty change is
        # deterministic and catalog-independent — it targets the exact variant even after a restart.
        "item_refs": (
            [
                {
                    "phrase": payload.get("ref") or payload.get("item_id"),
                    "qty": payload.get("qty", 1),
                    "item_id": payload.get("item_id"),
                    "secondary_id": payload.get("secondary_id"),
                    "name": payload.get("name"),
                }
            ]
            if payload.get("ref") or payload.get("item_id")
            else []
        ),
        "raw_query": "",
        "from_tap": True,
    }


def _for_narrator(intent: dict[str, Any], result: dict[str, Any], message: str) -> dict[str, Any]:
    # IMPORTANT: never hand the raw provider error prose (Report IDs etc.) to the narrator.
    # Pass a clean canonical `error_hint` instead.
    out = {k: result.get(k) for k in ("status", "error", "cards", "domain")}
    out["action"] = intent.get(
        "action"
    )  # what HAPPENED this turn (add/remove/clear/…), even for taps
    if result.get("error"):
        out["error_hint"] = user_message(result["error"])
    if result.get("note"):
        out["note"] = result["note"]
    if intent["domain"] == "chitchat":
        out["user_said"] = message
    # Deterministic cart truth: the EXACT count + total the UI shows this turn, so the narrator
    # echoes real numbers and never recomputes/recalls them from history (which holds stale totals).
    cart = next((c for c in (result.get("cards") or []) if c.get("type") == "cart"), None)
    if cart is not None:
        out["cart"] = {
            "provider": cart.get("provider"),
            "item_count": sum(int(i.get("qty", 0)) for i in cart.get("items", [])),
            "total": cart.get("total"),
            "empty": bool(cart.get("empty")),
        }
    return out


def _response(state, chat_id, assistant, intent, result) -> dict[str, Any]:
    carts_ui = {
        pid: {"item_count": sum(i.qty for i in c.items), "total": fmt_minor(c.total_minor)}
        for pid, c in state.carts.items()
        if c.items
    }
    out = {
        "chat_id": chat_id,
        "assistant_message": assistant,
        "domain": intent["domain"],
        "cards": result.get("cards", []),
        # Old-shaped cards for the existing FE renderers (Instamart/Zepto product + cart UI).
        "attachments": result.get("attachments", []),
        "ui_state": {
            "active_domain": state.active_domain,
            "active_provider": state.active_provider,
            "carts": carts_ui,
            "selected_context": state.selected_context,
        },
        "pending_action": result.get("pending_action"),
        "status": result.get("status", "ok"),
        "error": (
            {"kind": result.get("error"), "message": result.get("message", "")}
            if result.get("error")
            else None
        ),
    }
    # Selected delivery address ids the existing FE address bar consumes.
    for k in ("selected_instamart_address_id", "selected_zepto_address_id"):
        if k in result:
            out[k] = result[k]
    return out
