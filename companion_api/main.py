from __future__ import annotations

import logging
import os
from html import escape
from datetime import datetime
from typing import Any
from pathlib import Path
from urllib.parse import quote

import boto3
from botocore.exceptions import ClientError
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response as StarletteResponse
from mangum import Mangum
from pydantic import BaseModel

from companion_api.auth import (
    cognito_enabled,
    create_access_token,
    get_current_user,
    validate_admin_login,
)
from companion_api.secrets import secret_or_env
from companion_api.voice_routes import (
    voice_gemini_token_endpoint,
    voice_log_turn_endpoint,
    voice_tool_execute_endpoint,
    voice_token_endpoint,
)

from app.services.food_llm_router import route_food_query
from app.services.food_response_builder import saved_address_label_from_mcp_item
from app.services.agents.pipeline import CustomMcpNeedsDiscoveryError, run_agent_turn
from app.services.calendar import execute_reminder_calendar
from app.services.llm_config import resolve_llm_from_env
from app.services.tool_runtime import (
    delete_custom_tool_provider,
    get_tool_provider,
    list_tool_providers,
    save_custom_tool_provider,
    update_tool_provider_tools,
)
from companion_api import settings
import uuid
from companion_api.messages_repo import (
    ChatRepository,
    MessageRepository,
    derive_chat_title,
)
from companion_api.leadgen_queue import send_turn_event
from companion_api.dynamodb import ReminderRepository
from companion_api.models import (
    AgentQueryRequest,
    CustomMcpProviderRequest,
    FoodQueryRequest,
    GoogleCalendarReminderRequest,
    MapsRouteRequest,
    ReminderCreateRequest,
    ReminderEvent,
    ReminderResponseRequest,
    SendMessageRequest,
    ZapierMcpProviderRequest,
)
from companion_api.reminders import ReminderScheduler
from companion_api.google_calendar import (
    complete_google_calendar_oauth,
    google_calendar_status,
    start_google_calendar_oauth,
)
from companion_api.google_maps import (
    GoogleMapsError,
    GeoPoint,
    compute_route,
    google_maps_configured,
)
from app.services.whatsapp.handler import handle_whatsapp_webhook_event

logger = logging.getLogger(__name__)

app = FastAPI(title="Companion API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

# Cache-control headers for the SPA shell (`index.html`). MUST be
# no-cache because this file is the *only* place that references the
# current build's hashed asset filenames (e.g. /assets/index-Cv8krTmH.js).
# Without this, browsers (and any intermediate proxy) heuristically
# cache `index.html` for hours, keep loading the *old* hashed asset
# names, and the user never sees UI changes — even after we redeploy.
# `must-revalidate` ensures even stale caches phone home before serving.
_SPA_SHELL_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


class ImmutableStaticFiles(StaticFiles):
    """``StaticFiles`` that tags every successful response with
    ``Cache-Control: public, max-age=31536000, immutable``. Suitable
    *only* for content-hashed asset directories (e.g. Vite's
    ``dist/assets/``) where the filename itself changes whenever the
    bytes change — caching forever is safe and a year-long edge cache
    is the modern best practice for SPA bundles.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if isinstance(response, StarletteResponse) and response.status_code < 400:
            response.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable"
            )
        return response


@app.get("/", response_class=HTMLResponse)
@app.get("/food/", response_class=HTMLResponse)
def food_ui():
    """Serve the new React frontend SPA shell.

    Always returned with no-cache headers — see ``_SPA_SHELL_HEADERS``
    above for the rationale. The cost is a single revalidation request
    per page load (the assets it pulls in are immutable and cached
    forever).
    """
    index_path = _FRONTEND_DIST / "index.html"
    if not index_path.exists():
        # Fallback to legacy UI if frontend isn't built
        legacy_path = Path(__file__).resolve().parent.parent / "app" / "templates" / "app" / "food_ui.html"
        if legacy_path.exists():
            return HTMLResponse(legacy_path.read_text(), headers=_SPA_SHELL_HEADERS)
        return HTMLResponse(
            "Frontend not found. Run 'npm run build' in the frontend directory.",
            status_code=404,
            headers=_SPA_SHELL_HEADERS,
        )
    return HTMLResponse(index_path.read_text(), headers=_SPA_SHELL_HEADERS)


@app.get("/favicon.svg")
def favicon():
    fav_path = _FRONTEND_DIST / "favicon.svg"
    if fav_path.exists():
        return HTMLResponse(fav_path.read_text(), media_type="image/svg+xml")
    return HTMLResponse("", status_code=404)


# Mount static assets (JS, CSS, images) from the React build with
# year-long immutable caching — the filenames are content-hashed by
# Vite so a new build produces new filenames and old ones can be
# safely cached forever by browsers + CDNs.
if (_FRONTEND_DIST / "assets").exists():
    app.mount(
        "/assets",
        ImmutableStaticFiles(directory=str(_FRONTEND_DIST / "assets")),
        name="assets",
    )
# Mount public folder files (favicon, etc.). These are NOT hashed, so
# we keep them on the default (no explicit cache headers — browser
# heuristics, which is fine for static brand assets).
if _FRONTEND_DIST.exists():
    app.mount("/static", StaticFiles(directory=str(_FRONTEND_DIST)), name="static")


from companion_api.web_utils import oauth_result_page as _oauth_result_page  # noqa: E402
from companion_api.web_utils import public_url as _public_url  # noqa: E402




# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------










# ---------------------------------------------------------------------------
# MCP status
# ---------------------------------------------------------------------------






























# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


@app.post("/api/send-message/")
def send_whatsapp_message(
    payload: SendMessageRequest,
    _user: str = Depends(get_current_user),
) -> dict[str, Any]:
    message = payload.message.strip()
    whatsapp_url = (
        f"https://wa.me/{settings.COMPANION_WHATSAPP_CC_NUMBER}?text={quote(message)}"
    )

    llm_cfg, err_detail = resolve_llm_from_env(secret_or_env("FOOD_ROUTER_PROVIDER"))
    if err_detail:
        llm_payload: dict[str, Any] = {
            "status": "skipped" if payload.llm_optional else "error",
            "reason": err_detail,
        }
    elif not llm_cfg:
        llm_payload = {
            "status": "skipped" if payload.llm_optional else "error",
            "reason": "Could not select an LLM provider.",
        }
    else:
        try:
            llm_payload = {
                "status": "success",
                "provider": llm_cfg.provider,
                "food_routing_plan": route_food_query(
                    user_message=message,
                    api_key=llm_cfg.api_key,
                    model=llm_cfg.model,
                    provider=llm_cfg.provider,
                    base_url=llm_cfg.base_url,
                ),
            }
        except ValueError as e:
            llm_payload = {"status": "error", "reason": str(e)}
        except Exception as e:
            llm_payload = {"status": "error", "reason": f"{type(e).__name__}: {e}"}

    if llm_payload["status"] != "success" and not payload.llm_optional:
        status = "partial"
    else:
        status = "success"

    response: dict[str, Any] = {"status": status, "llm": llm_payload}
    if payload.include_whatsapp_link:
        response["whatsapp_url"] = whatsapp_url
    return response






def _companion_agent_turn(
    payload: FoodQueryRequest | AgentQueryRequest,
    user: str,
) -> dict[str, Any]:
    # The redesigned core pipeline is the default path. Set LEGACY_PIPELINE=1 to
    # fall back to the legacy monolith below (kept as a safety hatch until the
    # core path is validated end-to-end and the legacy code is removed at cutover).
    if os.getenv("LEGACY_PIPELINE", "").strip().lower() not in ("1", "true", "yes"):
        from companion_api.core_shadow import run_core_turn_sync

        return run_core_turn_sync(payload, user)

    provider_override = (payload.provider or "").strip()
    llm_cfg, err = resolve_llm_from_env(
        provider_override or secret_or_env("FOOD_ROUTER_PROVIDER")
    )
    if err or not llm_cfg:
        raise HTTPException(status_code=503, detail=err or "LLM not configured.")

    history = payload.history if isinstance(payload.history, list) else []
    recent_history = [
        item
        for item in history[-8:]
        if isinstance(item, dict)
        and item.get("role") in {"user", "assistant"}
        and item.get("content")
    ]

    execute_tools = (
        payload.execute_tools
        if payload.execute_tools is not None
        else (
            payload.execute_zomato
            if payload.execute_zomato is not None
            else settings.EXECUTE_TOOLS_BY_DEFAULT
        )
    )

    user_geo: dict[str, Any] | None = None
    if payload.geo is not None:
        user_geo = payload.geo.model_dump(exclude_none=True)

    # Pass session_state through as opaque IDs (executor validates keys).
    session_state: dict[str, str] | None = None
    if isinstance(payload.session_state, dict) and payload.session_state:
        session_state = {
            k: str(v)
            for k, v in payload.session_state.items()
            if isinstance(k, str) and isinstance(v, (str, int))
        }

    # Multi-restaurant memory carried forward from prior turns. Frontends
    # echo back whatever ``session_restaurants`` they received in the last
    # response; we sanitize it here so the executor only sees well-formed
    # entries (defensive: an old browser could send junk).
    session_restaurants: list[dict[str, Any]] | None = None
    raw_restaurants = getattr(payload, "session_restaurants", None)
    if isinstance(raw_restaurants, list) and raw_restaurants:
        clean: list[dict[str, Any]] = []
        for entry in raw_restaurants:
            if not isinstance(entry, dict):
                continue
            rid = entry.get("res_id")
            name = entry.get("name")
            if isinstance(rid, str) and rid.strip() and isinstance(name, str) and name.strip():
                clean.append({**entry, "res_id": rid.strip(), "name": name.strip()})
        session_restaurants = clean or None

    # Saved-address memory carried forward from prior turns. Same shape as
    # ``session_restaurants`` — sanitize so the executor only sees rows with
    # a real address_id; everything else (label, address_line, …) is
    # passed through opaquely for the planner's nudge context.
    session_addresses: list[dict[str, Any]] | None = None
    raw_addresses = getattr(payload, "session_addresses", None)
    if isinstance(raw_addresses, list) and raw_addresses:
        clean_addr: list[dict[str, Any]] = []
        for entry in raw_addresses:
            if not isinstance(entry, dict):
                continue
            aid = entry.get("address_id") or entry.get("addressId") or entry.get("id")
            if aid is None:
                continue
            sid = str(aid).strip()
            if not sid:
                continue
            clean_addr.append({**entry, "address_id": sid})
        session_addresses = clean_addr or None

    # Variant-choice deferral state — only ever set when the previous
    # turn skipped a Swiggy ``update_food_cart`` for a multi-option item.
    pending_variant_choice: list[dict[str, Any]] | None = None
    raw_pvc = getattr(payload, "pending_variant_choice", None)
    if isinstance(raw_pvc, list) and raw_pvc:
        clean_pvc: list[dict[str, Any]] = []
        for entry in raw_pvc:
            if not isinstance(entry, dict):
                continue
            iid = entry.get("item_id")
            if not isinstance(iid, str) or not iid.strip():
                continue
            clean_pvc.append(entry)
        pending_variant_choice = clean_pvc or None

    session_movies: list[dict[str, Any]] | None = None
    raw_movies = getattr(payload, "session_movies", None)
    if isinstance(raw_movies, list) and raw_movies:
        clean_movies: list[dict[str, Any]] = []
        for entry in raw_movies:
            if not isinstance(entry, dict):
                continue
            mid = entry.get("movie_id")
            name = entry.get("name")
            if isinstance(mid, str) and mid.strip() and isinstance(name, str) and name.strip():
                clean_movies.append({**entry, "movie_id": mid.strip(), "name": name.strip()})
        session_movies = clean_movies or None

    session_theatres: list[dict[str, Any]] | None = None
    raw_theatres = getattr(payload, "session_theatres", None)
    if isinstance(raw_theatres, list) and raw_theatres:
        clean_theatres: list[dict[str, Any]] = []
        for entry in raw_theatres:
            if not isinstance(entry, dict):
                continue
            tid = entry.get("theatre_id")
            name = entry.get("name")
            if isinstance(tid, str) and tid.strip() and isinstance(name, str) and name.strip():
                clean_theatres.append({**entry, "theatre_id": tid.strip(), "name": name.strip()})
        session_theatres = clean_theatres or None

    # District event-surface seeds. Same shape contract as movies / theatres —
    # each row must carry (id, name); everything else rides along verbatim
    # because it was emitted by our own executor on a previous turn.
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

    session_events = _clean_session_index(
        getattr(payload, "session_events", None), id_key="event_id"
    )
    session_venues = _clean_session_index(
        getattr(payload, "session_venues", None), id_key="venue_id"
    )
    session_artists = _clean_session_index(
        getattr(payload, "session_artists", None), id_key="artist_id"
    )

    # District reservation snapshot — drop anything without a real
    # ``booking_id``; the executor treats those as "no prior reservation"
    # and only stamps a fresh one when a booking call succeeds.
    session_reservation: dict[str, Any] | None = None
    raw_reservation = getattr(payload, "session_reservation", None)
    if isinstance(raw_reservation, dict):
        bid = raw_reservation.get("booking_id")
        if isinstance(bid, str) and bid.strip():
            session_reservation = {**raw_reservation, "booking_id": bid.strip()}

    # ``session_menu`` — sanitize so the executor only sees rows with a real
    # ``variant_id`` + ``item_name``. Everything else (addon_groups, variants,
    # price, etc.) is passed through verbatim because it was emitted by our
    # own executor on a previous turn and we trust its shape.
    session_menu: list[dict[str, Any]] | None = None
    raw_menu = getattr(payload, "session_menu", None)
    if isinstance(raw_menu, list) and raw_menu:
        clean_menu: list[dict[str, Any]] = []
        for entry in raw_menu:
            if not isinstance(entry, dict):
                continue
            vid = entry.get("variant_id")
            name = entry.get("item_name") or entry.get("name")
            if not isinstance(vid, str) or not vid.strip():
                continue
            if not isinstance(name, str) or not name.strip():
                continue
            clean_menu.append({**entry, "variant_id": vid.strip(), "item_name": name.strip()})
        session_menu = clean_menu or None

    # ``session_cart`` — single-restaurant snapshot. Drop anything missing
    # the cart_id or with no items; the executor treats those as "no prior
    # cart" and starts fresh, which is the correct behaviour.
    session_cart: dict[str, Any] | None = None
    raw_cart = getattr(payload, "session_cart", None)
    if isinstance(raw_cart, dict):
        cid = raw_cart.get("cart_id")
        items = raw_cart.get("items")
        if isinstance(cid, str) and cid.strip() and isinstance(items, list) and items:
            session_cart = {**raw_cart, "cart_id": cid.strip()}

    # Zepto session-state sanitization. Products / addresses keep the
    # same LRU shape as other providers (each entry needs an id pair +
    # a name). Cart is a single dict — we keep it even when empty so the
    # frontend can render the empty-cart state.
    session_zepto_products: list[dict[str, Any]] | None = None
    raw_zprod = getattr(payload, "session_zepto_products", None)
    if isinstance(raw_zprod, list) and raw_zprod:
        clean_zprod: list[dict[str, Any]] = []
        for entry in raw_zprod:
            if not isinstance(entry, dict):
                continue
            pvid = entry.get("product_variant_id")
            spid = entry.get("store_product_id")
            name = entry.get("name")
            if (
                isinstance(pvid, str)
                and pvid.strip()
                and isinstance(spid, str)
                and spid.strip()
                and isinstance(name, str)
                and name.strip()
            ):
                clean_zprod.append(
                    {
                        **entry,
                        "product_variant_id": pvid.strip(),
                        "store_product_id": spid.strip(),
                        "name": name.strip(),
                    }
                )
        session_zepto_products = clean_zprod or None

    session_zepto_addresses: list[dict[str, Any]] | None = None
    raw_zaddr = getattr(payload, "session_zepto_addresses", None)
    if isinstance(raw_zaddr, list) and raw_zaddr:
        clean_zaddr: list[dict[str, Any]] = []
        for entry in raw_zaddr:
            if not isinstance(entry, dict):
                continue
            aid = entry.get("address_id")
            label = entry.get("label")
            if (
                isinstance(aid, str)
                and aid.strip()
                and isinstance(label, str)
                and label.strip()
            ):
                clean_zaddr.append(
                    {**entry, "address_id": aid.strip(), "label": label.strip()}
                )
        session_zepto_addresses = clean_zaddr or None

    session_zepto_cart: dict[str, Any] | None = None
    raw_zcart = getattr(payload, "session_zepto_cart", None)
    if isinstance(raw_zcart, dict):
        zitems = raw_zcart.get("items")
        if isinstance(zitems, list) or raw_zcart.get("is_empty"):
            session_zepto_cart = dict(raw_zcart)

    # Instamart session-state sanitization. Same shape as Zepto's blocks
    # above but keyed on Instamart's ``spin_id`` (per-variant cart key).
    session_instamart_products: list[dict[str, Any]] | None = None
    raw_iprod = getattr(payload, "session_instamart_products", None)
    if isinstance(raw_iprod, list) and raw_iprod:
        clean_ip: list[dict[str, Any]] = []
        for entry in raw_iprod:
            if not isinstance(entry, dict):
                continue
            spin = entry.get("spin_id")
            name = entry.get("name")
            if (
                isinstance(spin, str)
                and spin.strip()
                and isinstance(name, str)
                and name.strip()
            ):
                clean_ip.append({**entry, "spin_id": spin.strip(), "name": name.strip()})
        session_instamart_products = clean_ip or None

    session_instamart_addresses: list[dict[str, Any]] | None = None
    raw_iaddr = getattr(payload, "session_instamart_addresses", None)
    if isinstance(raw_iaddr, list) and raw_iaddr:
        clean_ia: list[dict[str, Any]] = []
        for entry in raw_iaddr:
            if not isinstance(entry, dict):
                continue
            aid = entry.get("address_id")
            if isinstance(aid, str) and aid.strip():
                clean_ia.append({**entry, "address_id": aid.strip()})
        session_instamart_addresses = clean_ia or None

    session_instamart_cart: dict[str, Any] | None = None
    raw_icart = getattr(payload, "session_instamart_cart", None)
    if isinstance(raw_icart, dict):
        iitems = raw_icart.get("items")
        if isinstance(iitems, list) or raw_icart.get("is_empty"):
            session_instamart_cart = dict(raw_icart)

    # Kite (Zerodha) session-state sanitization — REAL-MONEY trading surface.
    # Instruments are keyed on ``tradingsymbol``; open orders on ``order_id``;
    # holdings / positions / order ticket are single dicts.
    session_kite_instruments: list[dict[str, Any]] | None = None
    raw_kinst = getattr(payload, "session_kite_instruments", None)
    if isinstance(raw_kinst, list) and raw_kinst:
        clean_kinst: list[dict[str, Any]] = []
        for entry in raw_kinst:
            if not isinstance(entry, dict):
                continue
            sym = entry.get("tradingsymbol")
            if isinstance(sym, str) and sym.strip():
                clean_kinst.append({**entry, "tradingsymbol": sym.strip().upper()})
        session_kite_instruments = clean_kinst or None

    session_kite_holdings: dict[str, Any] | None = None
    raw_khold = getattr(payload, "session_kite_holdings", None)
    if isinstance(raw_khold, dict) and raw_khold:
        session_kite_holdings = dict(raw_khold)

    session_kite_positions: dict[str, Any] | None = None
    raw_kpos = getattr(payload, "session_kite_positions", None)
    if isinstance(raw_kpos, dict) and raw_kpos:
        session_kite_positions = dict(raw_kpos)

    session_kite_open_orders: list[dict[str, Any]] | None = None
    raw_koo = getattr(payload, "session_kite_open_orders", None)
    if isinstance(raw_koo, list) and raw_koo:
        clean_koo: list[dict[str, Any]] = []
        for entry in raw_koo:
            if not isinstance(entry, dict):
                continue
            oid = entry.get("order_id")
            if isinstance(oid, str) and oid.strip():
                clean_koo.append({**entry, "order_id": oid.strip()})
        session_kite_open_orders = clean_koo or None

    session_kite_order_ticket: dict[str, Any] | None = None
    raw_kticket = getattr(payload, "session_kite_order_ticket", None)
    if isinstance(raw_kticket, dict) and raw_kticket:
        session_kite_order_ticket = dict(raw_kticket)

    # Resolve the chat this turn belongs to. Three cases:
    #   * client sent a non-empty ``chat_id`` AND that chat exists → use it.
    #   * client sent a ``chat_id`` we don't recognise (deleted, wrong user,
    #     stale localStorage) → treat as missing and lazy-create a new
    #     chat so the user isn't silently writing into a ghost.
    #   * client sent nothing → lazy-create a new chat, auto-titled from
    #     the opening message.
    # Errors talking to Dynamo (e.g. local dev with no AWS creds) are
    # tolerated — we fall back to a synthetic chat_id so the turn still
    # works, and just skip persistence below.
    incoming_chat_id = (
        str(getattr(payload, "chat_id", "") or "").strip() or None
    )
    chat_repo: ChatRepository | None = None
    chat_id: str | None = incoming_chat_id
    chat_was_created = False
    try:
        chat_repo = ChatRepository()
        existing_chat = None
        if incoming_chat_id:
            existing_chat = chat_repo.get(user, incoming_chat_id)
        if existing_chat is None:
            chat_id = incoming_chat_id or str(uuid.uuid4())
            chat_repo.create(
                member_id=user,
                chat_id=chat_id,
                title=derive_chat_title(payload.message.strip()),
            )
            chat_was_created = True
    except Exception:
        # Dynamo unavailable — keep going with a best-effort id so the
        # turn itself succeeds; persistence will be skipped further down.
        logging.getLogger(__name__).warning(
            "Chat lookup/create failed for user=%s — turn will not be persisted.",
            user,
            exc_info=True,
        )
        chat_repo = None
        chat_id = incoming_chat_id or str(uuid.uuid4())

    try:
        result = run_agent_turn(
            user_message=payload.message.strip(),
            address_id=payload.address_id,
            execute_tools=execute_tools,
            user_id=user,
            llm=llm_cfg,
            conversation_history=recent_history,
            user_geo=user_geo,
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
            session_zepto_products=session_zepto_products,
            session_zepto_addresses=session_zepto_addresses,
            session_zepto_cart=session_zepto_cart,
            session_instamart_products=session_instamart_products,
            session_instamart_addresses=session_instamart_addresses,
            session_instamart_cart=session_instamart_cart,
            session_kite_instruments=session_kite_instruments,
            session_kite_holdings=session_kite_holdings,
            session_kite_positions=session_kite_positions,
            session_kite_open_orders=session_kite_open_orders,
            session_kite_order_ticket=session_kite_order_ticket,
            pending_variant_choice=pending_variant_choice,
        )

        # Echo the chat_id back so the client can pin it for subsequent
        # turns (and persist it in localStorage for reload-restore).
        result["chat_id"] = chat_id

        # Async Extraction & Message Logging Hook
        try:
            turn_id = str(uuid.uuid4())
            task_type = result.get("intent_routing", {}).get("task_type") or result.get("task_type")
            routed_provider = result.get("routed_provider")

            if chat_repo is not None:
                msg_repo = MessageRepository()
                # 1. Persist user message
                msg_repo.create(
                    member_id=user,
                    chat_id=chat_id,
                    message_id=f"{turn_id}-user",
                    role="user",
                    text=payload.message.strip(),
                    task_type=task_type,
                    provider=routed_provider,
                )
                # 2. Persist assistant message
                msg_repo.create(
                    member_id=user,
                    chat_id=chat_id,
                    message_id=f"{turn_id}-assistant",
                    role="assistant",
                    text=result.get("assistant_message", ""),
                    task_type=task_type,
                    provider=routed_provider,
                )
                # 3. Bump chat metadata so the sidebar sorts this chat to
                # the top and shows the latest assistant reply as preview.
                preview = (result.get("assistant_message") or "").strip()
                title_override = (
                    derive_chat_title(payload.message.strip())
                    if chat_was_created
                    else None
                )
                chat_repo.touch(
                    member_id=user,
                    chat_id=chat_id,
                    last_message_preview=preview,
                    title=title_override,
                    increment_count=2,
                )
            # 4. Enqueue turn event to SQS (always — independent of
            # persistence, so downstream extraction still fires even if
            # the Dynamo write was skipped for the dev path).
            send_turn_event(
                user_id=user,
                message_id=turn_id,
                user_message=payload.message.strip(),
                assistant_message=result.get("assistant_message", ""),
                task_type=task_type,
                routed_provider=routed_provider,
                session_addresses=result.get("session_addresses"),
                user_geo=user_geo,
                member_id=user,
            )
        except Exception as hooks_exc:
            logging.getLogger(__name__).exception(
                f"Failed to log message or enqueue turn event: {hooks_exc}"
            )

        return result
    except CustomMcpNeedsDiscoveryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc


@app.get("/api/hero/greeting/")
def hero_greeting(user: str = Depends(get_current_user)) -> dict[str, Any]:
    """A short, catchy, personalized welcome line for the empty/hero state.

    Built from the user's profile (name/city/top interest), current weather at
    their saved address (Open-Meteo, keyless), and the time of day, then phrased
    by an LLM (DeepSeek with fallback). Best-effort: always returns a usable
    greeting — degrades to a static time-of-day line if anything is missing.
    """
    from app.services.hero_greeting import build_hero_greeting

    try:
        return build_hero_greeting(user)
    except Exception:
        logger.debug("hero greeting failed", exc_info=True)
        return {"greeting": "", "source": "error", "time_of_day": None, "weather": None}


def _message_requires_delivery_address(message: str) -> bool:
    text = f" {(message or '').strip().lower()} "
    delivery_words = {
        " order ",
        " buy ",
        " deliver ",
        " delivery ",
        " restaurant ",
        " restaurants ",
        " menu ",
        " cart ",
        " pizza ",
        " biryani ",
        " burger ",
        " dosa ",
        " milk ",
        " bread ",
        " eggs ",
        " atta ",
        " groceries ",
        " grocery ",
        " instamart ",
        " zepto ",
        " swiggy ",
        " zomato ",
    }
    return any(word in text for word in delivery_words)


@app.post("/api/agent/query/")
def agent_query_endpoint(
    payload: AgentQueryRequest,
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    return _companion_agent_turn(payload, user)


@app.post("/api/food/query/")
def food_agent_full(
    payload: FoodQueryRequest,
    user: str = Depends(get_current_user),
) -> dict[str, Any]:
    if not payload.address_id.strip() and _message_requires_delivery_address(payload.message):
        raise HTTPException(status_code=422, detail="address_id is required for delivery requests")
    return _companion_agent_turn(payload, user)


def scheduled_reminder_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    payload = ReminderEvent.model_validate(event)
    reminder = ReminderScheduler().mark_triggered(
        payload.member_id,
        payload.reminder_id,
    )
    
    if reminder:
        # Fetch member to get phone number for WhatsApp notification
        member = MemberRepository().get(payload.member_id)
        if member and member.get("phone_number"):
            from app.services.whatsapp.client import WhatsAppClient
            title = reminder.get("title", "Reminder")
            description = reminder.get("description", "")
            msg = f"🔔 *{title}*"
            if description:
                msg += f"\n{description}"
            
            # If it's a confirmation-required reminder, add quick reply buttons/instructions
            if reminder.get("requires_confirmation"):
                msg += "\n\nReply 'yes' to complete or 'no' to snooze."

            WhatsAppClient().send_text_message(member["phone_number"], msg)

    return {
        "status": "ok",
        "triggered": bool(reminder),
        "triggered_at": datetime.utcnow().isoformat() + "Z",
    }




app.post("/api/voice/tool-execute/")(voice_tool_execute_endpoint)
app.post("/api/voice/token/")(voice_token_endpoint)
app.post("/api/voice/gemini-token/")(voice_gemini_token_endpoint)
app.post("/api/voice/log-turn/")(voice_log_turn_endpoint)

# Routers extracted from this module (#29 — main.py decomposition).
from companion_api.routers.auth import router as auth_router  # noqa: E402
from companion_api.routers.calendar import router as calendar_router  # noqa: E402
from companion_api.routers.chats import router as chats_router  # noqa: E402
from companion_api.routers.maps import router as maps_router  # noqa: E402
from companion_api.routers.mcp import router as mcp_router  # noqa: E402
from companion_api.routers.profile import router as profile_router  # noqa: E402
from companion_api.routers.reminders import router as reminders_router  # noqa: E402
from companion_api.routers.tools import router as tools_router  # noqa: E402
from companion_api.routers.whatsapp import router as whatsapp_router  # noqa: E402

app.include_router(chats_router)
app.include_router(reminders_router)
app.include_router(profile_router)
app.include_router(maps_router)
app.include_router(whatsapp_router)
app.include_router(calendar_router)
app.include_router(tools_router)
app.include_router(auth_router)
app.include_router(mcp_router)


handler = Mangum(app, lifespan="off")
