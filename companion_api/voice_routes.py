"""Voice tool execution bridge for Gemini Live mode."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import Depends
from pydantic import BaseModel

from companion_api.auth import get_current_user
from companion_api.leadgen_queue import send_turn_event
from companion_api.messages_repo import (
    ChatRepository,
    MessageRepository,
    derive_chat_title,
)
from companion_api.secrets import secret_or_env
from core.llm.config import resolve_llm_from_env

logger = logging.getLogger(__name__)


def _persist_and_enqueue_voice_turn(
    *,
    user: str,
    chat_id: str | None,
    user_text: str,
    assistant_text: str,
    task_type: str | None = None,
    routed_provider: str | None = None,
    session_addresses: Any = None,
) -> str | None:
    """Persist a voice turn + enqueue the leadgen event, mirroring /api/send-message.

    Used by both the tool-call path and the conversational log-turn path so
    voice conversations show up in chat history AND feed the knowledge-graph /
    leadgen pipeline exactly like text chats. Best-effort: any Dynamo/SQS error
    is swallowed so it can never break the live voice reply.

    Returns the resolved chat_id (looked up or freshly created).
    """
    resolved_chat_id = (chat_id or "").strip() or None
    try:
        chat_repo = ChatRepository()
        existing = chat_repo.get(user, resolved_chat_id) if resolved_chat_id else None
        created = False
        if existing is None:
            resolved_chat_id = resolved_chat_id or str(uuid.uuid4())
            chat_repo.create(
                member_id=user,
                chat_id=resolved_chat_id,
                title=derive_chat_title(user_text or "Voice chat"),
            )
            created = True

        turn_id = str(uuid.uuid4())
        msg_repo = MessageRepository()
        if user_text:
            msg_repo.create(
                member_id=user,
                chat_id=resolved_chat_id,
                message_id=f"{turn_id}-user",
                role="user",
                text=user_text,
                task_type=task_type,
                provider=routed_provider,
            )
        msg_repo.create(
            member_id=user,
            chat_id=resolved_chat_id,
            message_id=f"{turn_id}-assistant",
            role="assistant",
            text=assistant_text,
            task_type=task_type,
            provider=routed_provider,
        )
        chat_repo.touch(
            member_id=user,
            chat_id=resolved_chat_id,
            last_message_preview=(assistant_text or "").strip(),
            title=derive_chat_title(user_text) if created else None,
            increment_count=2 if user_text else 1,
        )
    except Exception:
        logger.exception("voice turn persistence failed (user=%s)", user)

    # Enqueue leadgen independent of persistence (matches the text path).
    try:
        send_turn_event(
            user_id=user,
            message_id=str(uuid.uuid4()),
            user_message=user_text,
            assistant_message=assistant_text,
            task_type=task_type,
            routed_provider=routed_provider,
            session_addresses=session_addresses,
            user_geo=None,
            member_id=user,
        )
    except Exception:
        logger.exception("voice turn leadgen enqueue failed (user=%s)", user)

    return resolved_chat_id


class VoiceToolExecuteRequest(BaseModel):
    intent: str
    history: list[dict] = []
    session_state: dict = {}
    chat_id: str | None = None
    # Live GPS captured by the browser. The executor uses this when the user
    # did NOT name a place ("find me restaurants near me"). When the user does
    # name a place ("near Saket"), the planner emits requested_location_text
    # and the executor geocodes that instead, ignoring this field — same
    # precedence as the text-chat path.
    geo: dict | None = None
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
    session_menu: list[dict] = []
    # Rich cards (menu_cards / restaurant_cards / cart_card) produced by the
    # turn, so the chat UI can render them live during a voice conversation.
    attachments: list[dict] = []
    chat_id: str | None = None


class VoiceLogTurnRequest(BaseModel):
    user_text: str = ""
    assistant_text: str = ""
    chat_id: str | None = None


class VoiceLogTurnResponse(BaseModel):
    success: bool
    chat_id: str | None = None


def _clean_session_index(raw: Any, *, id_key: str) -> list[dict[str, Any]] | None:
    if not isinstance(raw, list) or not raw:
        return None
    cleaned: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        ident = entry.get(id_key)
        name = entry.get("name")
        if isinstance(ident, str) and ident.strip() and isinstance(name, str) and name.strip():
            cleaned.append({**entry, id_key: ident.strip(), "name": name.strip()})
    return cleaned or None


def _sanitise_session_state(raw: dict | None) -> dict[str, str] | None:
    if not isinstance(raw, dict) or not raw:
        return None
    return {k: str(v) for k, v in raw.items() if isinstance(k, str) and isinstance(v, (str, int))}


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


def _sanitise_user_geo(raw: dict | None) -> dict[str, Any] | None:
    """Validate the browser-supplied lat/lng before forwarding to executors."""
    if not isinstance(raw, dict):
        return None
    lat = raw.get("latitude", raw.get("lat"))
    lng = raw.get("longitude", raw.get("lng"))
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat_f <= 90.0) or not (-180.0 <= lng_f <= 180.0):
        return None
    out: dict[str, Any] = {"latitude": lat_f, "longitude": lng_f}
    acc = raw.get("accuracy_meters", raw.get("accuracyMeters"))
    if acc is not None:
        try:
            out["accuracy_meters"] = float(acc)
        except (TypeError, ValueError):
            pass
    return out


def _sanitise_session_reservation(raw: dict | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    bid = raw.get("booking_id")
    if isinstance(bid, str) and bid.strip():
        return {**raw, "booking_id": bid.strip()}
    return None


def voice_tool_execute_endpoint(
    request: VoiceToolExecuteRequest,
    user: str = Depends(get_current_user),
) -> VoiceToolExecuteResponse:
    # NOTE: this MUST stay a sync `def`, not `async def`. run_agent_turn calls
    # the Zomato/Swiggy MCP executors, which call asyncio.run() internally.
    # asyncio.run() cannot run inside an already-running event loop, so an
    # async handler makes every food tool call fail with
    # "asyncio.run() cannot be called from a running event loop". As a sync
    # def, FastAPI runs this in a threadpool (no running loop), matching the
    # text-chat endpoints (/api/send-message, /api/agent/query) which work.
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
        user_geo = _sanitise_user_geo(request.geo)

        # Voice shares the same core pipeline as text chat. Voice persona speaks
        # romanized Hinglish, so force reply_language.
        from companion_api.core_shadow import run_core_turn_sync

        result = run_core_turn_sync(
            {
                "message": request.intent.strip(),
                "history": recent_history,
                "chat_id": request.chat_id,
                "execute_tools": True,
                # Live browser GPS — used when the user didn't name a place;
                # named places ("near Saket") are geocoded and override this.
                "user_geo": user_geo,
                "session_state": session_state,
                "session_restaurants": session_restaurants,
                "session_addresses": session_addresses,
                "session_cart": session_cart,
                "session_menu": session_menu,
                "session_movies": session_movies,
                "session_theatres": session_theatres,
                "session_events": session_events,
                "session_venues": session_venues,
                "session_artists": session_artists,
                "session_reservation": session_reservation,
            },
            user,
            reply_language="hinglish",
        )

        assistant_text = result.get("assistant_message", "")
        task_type = result.get("intent_routing", {}).get("task_type") or result.get("task_type")
        # Persist this turn + feed the leadgen pipeline, just like text chat.
        resolved_chat_id = _persist_and_enqueue_voice_turn(
            user=user,
            chat_id=request.chat_id,
            user_text=request.intent.strip(),
            assistant_text=assistant_text,
            task_type=task_type,
            routed_provider=result.get("routed_provider"),
            session_addresses=result.get("session_addresses"),
        )

        attachments = result.get("attachments")
        return VoiceToolExecuteResponse(
            success=True,
            result=assistant_text,
            session_state=result.get("session_state", {}),
            session_cart=result.get("session_cart"),
            session_restaurants=result.get("session_restaurants", []),
            session_menu=result.get("session_menu", []),
            attachments=attachments if isinstance(attachments, list) else [],
            chat_id=resolved_chat_id or request.chat_id,
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
        "reasoning": {"effort": "minimal"},
        "instructions": (
            "You are Companion, a helpful personal assistant for users in India. "
            "ALWAYS reply in Hinglish — conversational Hindi written in the Roman/Latin "
            "alphabet (e.g. 'Theek hai, ho gaya'). NEVER use Devanagari script; romanize "
            "everything. English words are fine where natural. "
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
        "tools": [
            {
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
            }
        ],
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


# ---------------------------------------------------------------------------
# Gemini Live ephemeral token endpoint
# Frontend calls this to get a short-lived token for a direct WebSocket
# connection to the Gemini Live API (BidiGenerateContentConstrained). The
# long-lived GEMINI_API_KEY never leaves the server. The token is single-use
# (uses=1) and model-constrained, so a leaked token can only start one Live
# session against our chosen model before it expires.
#
# IMPORTANT: the BidiGenerateContentConstrained endpoint only honours the
# session config that is LOCKED INTO THE TOKEN. Tools / system instruction /
# transcription sent by the browser in its `setup` message are silently
# ignored (verified against the live API). So the full session config —
# including the companion_execute function declaration — MUST be baked into
# the token's live_connect_constraints.config here, or voice tool-calling
# silently never fires.
# ---------------------------------------------------------------------------

GEMINI_LIVE_MODEL_DEFAULT = "gemini-2.5-flash-native-audio-latest"

GEMINI_LIVE_SYSTEM_INSTRUCTION = (
    "You are Companion, a warm, funny voice assistant for users in India. "
    "Always reply in HINGLISH — conversational Hindi written in the Roman/Latin "
    "alphabet (e.g. 'Theek hai, pizza order ho gaya hai'). NEVER use the Devanagari "
    "script in your words or transcript — romanize everything. English words are "
    "welcome where natural. Keep replies short and natural, like a dost. "
    "When the user wants to do something actionable — order food, book a cab, find events, "
    "manage reminders, or anything needing live data — you MUST call the companion_execute "
    "function with a complete natural-language intent. Do NOT answer such requests from your "
    "own knowledge; always route them through companion_execute. "
    # companion_execute is NON_BLOCKING, so you can keep talking while it runs.
    "IMPORTANT: the moment you call companion_execute, FIRST say ONE short, natural Hinglish "
    "status line so the user is never left in silence while it works — e.g. 'Ek second, check "
    "kar raha hoon...', 'Zomato pe dekh raha hoon...', 'Order laga raha hoon...'. Keep it to "
    "one short line, tailored to what they asked. Do NOT invent specific progress or fake "
    "results — just a brief 'on it' cue. When the result comes back, speak the actual answer."
)


def _voice_profile_summary(user_id: str) -> str:
    """Build a compact, personalized profile block for the Live system prompt.

    The conversational layer (Gemini speaking directly) otherwise has no idea
    who the user is — name, city, saved home/office addresses, interests. We
    bake a short summary into the system instruction so the assistant greets
    by name, knows what "home"/"office" mean, and personalizes chit-chat.
    Tool-driven turns get the full profile separately via run_agent_turn.
    Best-effort: returns "" if the profile cache is empty or unavailable.
    """
    try:
        from companion_api.profile_cache import ProfileCacheRepository

        profile = ProfileCacheRepository().get(user_id)
    except Exception:
        logger.debug("voice profile summary fetch failed", exc_info=True)
        return ""
    if not isinstance(profile, dict) or not profile:
        return ""

    lines: list[str] = []
    basics = []
    if profile.get("name"):
        basics.append(f"name is {profile['name']}")
    if profile.get("city"):
        basics.append(f"city is {profile['city']}")
    if profile.get("language"):
        basics.append(f"preferred language is {profile['language']}")
    if basics:
        lines.append("The user's " + ", ".join(basics) + ".")

    addresses = profile.get("addresses")
    if isinstance(addresses, dict) and addresses:
        addr_bits = []
        for label, details in addresses.items():
            if isinstance(details, dict) and details.get("address_line"):
                addr_bits.append(f"{label}: {details['address_line']}")
        if addr_bits:
            lines.append("Saved addresses — " + "; ".join(addr_bits) + ".")

    interests = profile.get("interests")
    if isinstance(interests, list) and interests:
        cats = [
            str(i.get("category"))
            for i in interests[:5]
            if isinstance(i, dict) and i.get("category")
        ]
        if cats:
            lines.append("Interests: " + ", ".join(cats) + ".")

    if not lines:
        return ""
    return (
        "\n\nWhat you know about this user (use it naturally; greet them by name; "
        "when they say 'home'/'office' you know where they mean):\n" + "\n".join(lines)
    )


async def voice_gemini_token_endpoint(
    request: VoiceTokenRequest = None,
    user: str = Depends(get_current_user),
) -> dict:
    """Mint a single-use ephemeral Gemini Live token for client-side WebSocket use.

    The companion_execute tool + system instruction + transcription are baked
    into the token because the constrained Live endpoint ignores client-supplied
    setup config.
    """
    gemini_key = secret_or_env("GEMINI_API_KEY") or secret_or_env("GOOGLE_API_KEY")
    if not gemini_key:
        return {"error": "GEMINI_API_KEY not configured", "token": ""}

    model = secret_or_env("GEMINI_LIVE_MODEL", GEMINI_LIVE_MODEL_DEFAULT)

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=gemini_key, http_options={"api_version": "v1alpha"})

        companion_tool = types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name="companion_execute",
                    # NON_BLOCKING keeps the live conversation alive while the
                    # tool runs (run_agent_turn can take 10-30s), so the model
                    # can speak a status line instead of leaving dead air. The
                    # client sends the result back with scheduling=INTERRUPT.
                    behavior=types.Behavior.NON_BLOCKING,
                    description=(
                        "Execute actions for the user: order food, book cabs, find events, "
                        "manage reminders, answer questions, and more."
                    ),
                    parameters=types.Schema(
                        type="OBJECT",
                        properties={
                            "intent": types.Schema(
                                type="STRING",
                                description="Full natural-language intent with all relevant details.",
                            )
                        },
                        required=["intent"],
                    ),
                )
            ]
        )
        system_instruction = GEMINI_LIVE_SYSTEM_INSTRUCTION + _voice_profile_summary(user)
        live_config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=types.Content(parts=[types.Part(text=system_instruction)]),
            tools=[companion_tool],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )

        # Defaults give ~1 min to start the session and ~30 min to use it.
        token = client.auth_tokens.create(
            config=types.CreateAuthTokenConfig(
                uses=1,
                live_connect_constraints=types.LiveConnectConstraints(
                    model=model, config=live_config
                ),
            )
        )
        if not token or not token.name:
            return {"error": "Gemini token mint returned no token", "token": ""}
        return {"token": token.name, "model": model}
    except Exception as e:
        logger.exception("voice_gemini_token_endpoint")
        return {"error": str(e), "token": ""}


async def voice_log_turn_endpoint(
    request: VoiceLogTurnRequest,
    user: str = Depends(get_current_user),
) -> VoiceLogTurnResponse:
    """Persist + leadgen-enqueue a conversational voice turn (no tool call).

    Tool-call turns are already persisted by voice_tool_execute_endpoint; this
    captures the chit-chat turns that otherwise never reach the backend, so the
    full voice conversation lands in history and the leadgen pipeline.
    """
    user_text = (request.user_text or "").strip()
    assistant_text = (request.assistant_text or "").strip()
    if not user_text and not assistant_text:
        return VoiceLogTurnResponse(success=False, chat_id=request.chat_id)
    chat_id = _persist_and_enqueue_voice_turn(
        user=user,
        chat_id=request.chat_id,
        user_text=user_text,
        assistant_text=assistant_text,
        task_type="voice_chat",
    )
    return VoiceLogTurnResponse(success=True, chat_id=chat_id or request.chat_id)
