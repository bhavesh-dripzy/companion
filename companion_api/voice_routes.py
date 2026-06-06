"""Voice tool execution bridge for Gemini Live mode."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends
from pydantic import BaseModel

from app.services.agents.pipeline import run_agent_turn
from app.services.llm_config import resolve_llm_from_env
from companion_api.auth import get_current_user
from companion_api.secrets import secret_or_env

logger = logging.getLogger(__name__)


class VoiceToolExecuteRequest(BaseModel):
    intent: str
    history: list[dict] = []
    session_state: dict = {}
    chat_id: str | None = None
    session_restaurants: list[dict] | None = None
    session_addresses: list[dict] | None = None
    session_cart: dict | None = None
    session_menu: list[dict] | None = None
    session_movies: list[dict] | None = None
    session_theatres: list[dict] | None = None
    session_events: list[dict] | None = None
    session_venues: list[dict] | None = None
    session_artists: list[dict] | None = None
    session_reservation: dict | None = None


class VoiceToolExecuteResponse(BaseModel):
    success: bool
    result: str
    error: str | None = None
    session_state: dict = {}
    session_cart: dict | None = None
    session_restaurants: list[dict] = []
    chat_id: str | None = None


def _clean_session_index(
    raw: Any, *, id_key: str
) -> list[dict[str, Any]] | None:
    if not isinstance(raw, list) or not raw:
        return None
    cleaned: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        ident = entry.get(id_key)
        name = entry.get("name")
        if (
            isinstance(ident, str)
            and ident.strip()
            and isinstance(name, str)
            and name.strip()
        ):
            cleaned.append({**entry, id_key: ident.strip(), "name": name.strip()})
    return cleaned or None


def _sanitise_session_state(raw: dict | None) -> dict[str, str] | None:
    if not isinstance(raw, dict) or not raw:
        return None
    return {
        k: str(v)
        for k, v in raw.items()
        if isinstance(k, str) and isinstance(v, (str, int))
    }


def _sanitise_session_restaurants(raw: list[dict] | None) -> list[dict[str, Any]] | None:
    if not isinstance(raw, list) or not raw:
        return None
    cleaned: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        rid = entry.get("res_id")
        name = entry.get("name")
        if isinstance(rid, str) and rid.strip() and isinstance(name, str) and name.strip():
            cleaned.append({**entry, "res_id": rid.strip(), "name": name.strip()})
    return cleaned or None


def _sanitise_session_addresses(raw: list[dict] | None) -> list[dict[str, Any]] | None:
    if not isinstance(raw, list) or not raw:
        return None
    cleaned: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        aid = entry.get("address_id") or entry.get("addressId") or entry.get("id")
        if aid is None:
            continue
        sid = str(aid).strip()
        if not sid:
            continue
        cleaned.append({**entry, "address_id": sid})
    return cleaned or None


def _sanitise_session_cart(raw: dict | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    cid = raw.get("cart_id")
    items = raw.get("items")
    if isinstance(cid, str) and cid.strip() and isinstance(items, list) and items:
        return {**raw, "cart_id": cid.strip()}
    return None


def _sanitise_session_menu(raw: list[dict] | None) -> list[dict[str, Any]] | None:
    if not isinstance(raw, list) or not raw:
        return None
    cleaned: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        vid = entry.get("variant_id")
        name = entry.get("item_name") or entry.get("name")
        if not isinstance(vid, str) or not vid.strip():
            continue
        if not isinstance(name, str) or not name.strip():
            continue
        cleaned.append({**entry, "variant_id": vid.strip(), "item_name": name.strip()})
    return cleaned or None


def _sanitise_session_reservation(raw: dict | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    bid = raw.get("booking_id")
    if isinstance(bid, str) and bid.strip():
        return {**raw, "booking_id": bid.strip()}
    return None


async def voice_tool_execute_endpoint(
    request: VoiceToolExecuteRequest,
    user: str = Depends(get_current_user),
) -> VoiceToolExecuteResponse:
    try:
        llm_cfg, err = resolve_llm_from_env(secret_or_env("FOOD_ROUTER_PROVIDER"))
        if err or not llm_cfg:
            return VoiceToolExecuteResponse(
                success=False,
                result="",
                error=err or "LLM not configured.",
            )

        recent_history: list[dict[str, str]] = [
            item
            for item in request.history[-8:]
            if isinstance(item, dict)
            and item.get("role") in {"user", "assistant"}
            and item.get("content")
        ]

        session_state = _sanitise_session_state(request.session_state)
        session_restaurants = _sanitise_session_restaurants(request.session_restaurants)
        session_addresses = _sanitise_session_addresses(request.session_addresses)
        session_cart = _sanitise_session_cart(request.session_cart)
        session_menu = _sanitise_session_menu(request.session_menu)
        session_movies = _clean_session_index(request.session_movies, id_key="movie_id")
        session_theatres = _clean_session_index(request.session_theatres, id_key="theatre_id")
        session_events = _clean_session_index(request.session_events, id_key="event_id")
        session_venues = _clean_session_index(request.session_venues, id_key="venue_id")
        session_artists = _clean_session_index(request.session_artists, id_key="artist_id")
        session_reservation = _sanitise_session_reservation(request.session_reservation)

        result = run_agent_turn(
            user_message=request.intent.strip(),
            address_id="",
            execute_tools=True,
            user_id=user,
            llm=llm_cfg,
            conversation_history=recent_history,
            user_geo=None,
            session_state=session_state,
            session_restaurants=session_restaurants,
            session_addresses=session_addresses,
            session_movies=session_movies,
            session_theatres=session_theatres,
            session_events=session_events,
            session_venues=session_venues,
            session_artists=session_artists,
            session_reservation=session_reservation,
            session_menu=session_menu,
            session_cart=session_cart,
            pending_variant_choice=None,
        )

        return VoiceToolExecuteResponse(
            success=True,
            result=result.get("assistant_message", ""),
            session_state=result.get("session_state", {}),
            session_cart=result.get("session_cart"),
            session_restaurants=result.get("session_restaurants", []),
            chat_id=request.chat_id,
        )

    except Exception as e:
        logger.exception("voice_tool_execute_endpoint failed for user=%s", user)
        return VoiceToolExecuteResponse(
            success=False,
            result="",
            error=str(e),
        )


# ---------------------------------------------------------------------------
# OpenAI Realtime ephemeral token endpoint
# Frontend calls this to get a short-lived token for WebRTC connection to OpenAI
# ---------------------------------------------------------------------------


class VoiceTokenRequest(BaseModel):
    pass  # No body needed - uses server-side API key


async def voice_token_endpoint(
    request: VoiceTokenRequest = None,
    user: str = Depends(get_current_user),
) -> dict:
    """Mint an ephemeral OpenAI Realtime token for WebRTC voice sessions."""
    openai_key = secret_or_env("OPENAI_API_KEY")
    if not openai_key:
        return {"error": "OPENAI_API_KEY not configured", "token": ""}

    import requests

    session_config = {
        "type": "realtime",
        "model": "gpt-realtime-2",
        "output_modalities": ["audio"],
        "instructions": (
            "You are Companion, a helpful personal assistant for users in India. "
            "ALWAYS speak in Hindi (Hinglish is okay when needed). "
            "Be warm, friendly, and natural — like a helpful dost. "
            "Answer clearly and concisely. Stay factual; say when you are unsure. "
            "You can help with food ordering (Zomato/Swiggy), cab booking, "
            "finding events, managing reminders, and answering questions. "
            "When the user asks you to perform an action, use the companion_execute function. "
            "Keep responses brief and conversational. Sound natural, not scripted."
        ),
        "audio": {
            "input": {
                "noise_reduction": {"type": "near_field"},
                "transcription": {"model": "gpt-4o-mini-transcribe", "language": "hi"},
                "turn_detection": {
                    "type": "semantic_vad",
                    "eagerness": "high",
                    "create_response": True,
                    "interrupt_response": True,
                },
            },
            "output": {"voice": "alloy", "speed": 1.0},
        },
        "tools": [{
            "type": "function",
            "name": "companion_execute",
            "description": (
                "Execute actions for the user: order food, book cabs, find events, "
                "manage reminders, answer questions, and more. Call this when the user "
                "wants to perform an action or needs information beyond conversational knowledge."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "description": "The full natural language intent of what the user wants to accomplish. Include all relevant details.",
                    }
                },
                "required": ["intent"],
            },
        }],
    }

    try:
        resp = requests.post(
            "https://api.openai.com/v1/realtime/client_secrets",
            headers={
                "Authorization": f"Bearer {openai_key}",
                "Content-Type": "application/json",
            },
            json={"session": session_config},
            timeout=10,
        )
        if not resp.ok:
            logger.error("OpenAI token mint failed: %s", resp.text)
            return {"error": f"OpenAI returned {resp.status_code}", "token": ""}

        data = resp.json()
        token = data.get("value") or data.get("client_secret", {}).get("value", "")
        return {"token": token, "model": "gpt-realtime-2", "voice": "alloy"}

    except Exception as e:
        logger.exception("voice_token_endpoint")
        return {"error": str(e), "token": ""}
