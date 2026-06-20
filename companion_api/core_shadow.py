"""Run a turn through the new core pipeline (shadow mode / cutover bridge).

Imported lazily by the web layer ONLY when `CORE_PIPELINE` is enabled, so the
live default path is completely untouched until cutover (Phase 8). Adapts the
FastAPI request payload to a core `TurnRequest`, runs the `PipelineRunner`, and
returns a legacy-shaped envelope. Use it in the live-fixture session to exercise
the core pipeline end-to-end against real providers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from core.app_factory import build_runner
from core.contracts.session import SessionState
from core.contracts.tool_result import ProviderResult
from core.contracts.turn import TurnRequest, TurnResponse
from core.llm.config import resolve_llm_from_env
from core.middleware.tracing import Tracer

try:
    from companion_api.secrets import secret_or_env
except Exception:  # pragma: no cover
    import os

    def secret_or_env(name: str, default: str = "") -> str:
        return (os.getenv(name) or default).strip()


# Flat legacy session_* keys round-tripped between the frontend and the pipeline.
_SESSION_KEYS = (
    "session_restaurants",
    "session_addresses",
    "session_menu",
    "session_cart",
    "session_movies",
    "session_theatres",
    "session_events",
    "session_venues",
    "session_artists",
    "session_reservation",
    "session_zepto_products",
    "session_zepto_addresses",
    "session_zepto_cart",
    # The committed Zepto delivery location {latitude, longitude, ...} — kept for
    # back-compat / optional GPS onboarding hint (see _SESSION_ZEPTO_LOCATION).
    "selected_zepto_location",
    # The saved address the user picked for Zepto this session — the source of
    # truth that select_saved_address binds the store + order to (mirrors
    # Instamart). Must survive the round-trip so cart/order reuse it.
    "selected_zepto_address_id",
    # The committed District discovery location {latitude, longitude, ...} —
    # District's source of truth (lat/lng; no saved-address concept). See
    # _SESSION_DISTRICT_LOCATION.
    "selected_district_location",
    "session_instamart_products",
    "session_instamart_addresses",
    "session_instamart_cart",
    # NOTE: _SESSION_SELECTED_ADDRESS (below) is also part of this tuple.
    # The address the user picked for Instamart this session — must survive the
    # round-trip so cart/checkout reuse it instead of re-picking the nearest
    # (which may be a non-serviceable address) every turn.
    "selected_instamart_address_id",
    "session_kite_instruments",
    "session_kite_holdings",
    "session_kite_positions",
    "session_kite_open_orders",
    "session_kite_order_ticket",
    "session_serp_flights",
    "session_serp_products",
    "session_serp_places",
    "session_serp_hotels",
)

# The committed Instamart delivery address key (a member of _SESSION_KEYS above).
_SESSION_SELECTED_ADDRESS = "selected_instamart_address_id"
# The committed Zepto delivery location key (a member of _SESSION_KEYS above).
_SESSION_ZEPTO_LOCATION = "selected_zepto_location"
# The committed Zepto delivery address key (a member of _SESSION_KEYS above).
_SESSION_ZEPTO_ADDRESS = "selected_zepto_address_id"
# The committed District discovery location key (a member of _SESSION_KEYS above).
_SESSION_DISTRICT_LOCATION = "selected_district_location"
# The committed District discovery location key (a member of _SESSION_KEYS above).
_SESSION_DISTRICT_LOCATION = "selected_district_location"

_runner = None


def _get_runner():
    global _runner
    if _runner is None:
        _runner = build_runner()
    return _runner


def _payload_dict(payload: Any) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump()
    return dict(payload) if isinstance(payload, dict) else {}


def _to_request(data: dict[str, Any], user: str, reply_language: str | None) -> TurnRequest:
    history = data.get("history") if isinstance(data.get("history"), list) else []
    legacy = {k: data[k] for k in _SESSION_KEYS if data.get(k) is not None}
    # Pin the user's saved Instamart default address (DynamoDB source of truth)
    # when the client didn't carry one this turn. This anchors search/cart to a
    # known-good address instead of the executor falling back to the nearest
    # saved address (which may be non-serviceable). The live cart's address still
    # wins inside the provider when a cart already has items.
    if not str(legacy.get(_SESSION_SELECTED_ADDRESS) or "").strip():
        try:
            from companion_api.instamart_address import get_default_instamart_address

            default = get_default_instamart_address(user)
            if default and default.get("address_id"):
                legacy[_SESSION_SELECTED_ADDRESS] = default["address_id"]
        except Exception:  # pragma: no cover - never block a turn on prefs lookup
            pass
    # Pin the user's saved Zepto delivery ADDRESS (source of truth — mirrors
    # Instamart). select_saved_address binds the store + order to it; the provider
    # uses it over drifting GPS. Falls back to the legacy GPS location as an
    # onboarding hint so the needs_location guard never blocks a known-where turn.
    user_geo = data.get("user_geo") or data.get("geo")
    if not str(legacy.get(_SESSION_ZEPTO_ADDRESS) or "").strip():
        try:
            from companion_api.zepto_address import get_default_zepto_address

            addr = get_default_zepto_address(user)
            if addr and addr.get("address_id"):
                legacy[_SESSION_ZEPTO_ADDRESS] = addr["address_id"]
        except Exception:  # pragma: no cover - never block a turn on prefs lookup
            pass
    if not isinstance(legacy.get(_SESSION_ZEPTO_LOCATION), dict):
        try:
            from companion_api.zepto_location import get_default_zepto_location

            loc = get_default_zepto_location(user)
            if loc and loc.get("latitude") is not None and loc.get("longitude") is not None:
                legacy[_SESSION_ZEPTO_LOCATION] = loc
                if not user_geo:
                    user_geo = {"latitude": loc["latitude"], "longitude": loc["longitude"]}
        except Exception:  # pragma: no cover - never block a turn on prefs lookup
            pass
    # Pin the user's saved District discovery location (source of truth — District
    # search tools take {lat,lng} and have no saved-address concept). The provider
    # uses it over drifting GPS; it also seeds user_geo so the needs_location guard
    # never blocks a turn for a user who set a location during onboarding.
    if not isinstance(legacy.get(_SESSION_DISTRICT_LOCATION), dict):
        try:
            from companion_api.district_location import get_default_district_location

            dloc = get_default_district_location(user)
            if dloc and dloc.get("latitude") is not None and dloc.get("longitude") is not None:
                legacy[_SESSION_DISTRICT_LOCATION] = dloc
                if not user_geo:
                    user_geo = {"latitude": dloc["latitude"], "longitude": dloc["longitude"]}
        except Exception:  # pragma: no cover - never block a turn on prefs lookup
            pass
    session = SessionState(placeholders=data.get("session_state") or {}, legacy=legacy)
    return TurnRequest(
        message=str(data.get("message") or ""),
        user_id=user,
        address_id=(data.get("address_id") or None),
        history=history,
        chat_id=data.get("chat_id"),
        session=session,
        execute_tools=data.get("execute_tools") if data.get("execute_tools") is not None else True,
        reply_language=reply_language,
        user_geo=user_geo,
        enabled_providers=data.get("enabled_providers"),
    )


def _persist_instamart_default(user: str, legacy: dict[str, Any]) -> None:
    """Sync the user's saved Instamart default address with the address the turn
    committed to. When the user switches delivery address mid-session (e.g. via
    the product-card "Change" picker), the new ``selected_instamart_address_id``
    becomes the DB source of truth so it sticks for the next turn / fresh session,
    and the in-process prefs cache is invalidated. No-op when unchanged."""
    aid = str((legacy or {}).get(_SESSION_SELECTED_ADDRESS) or "").strip()
    if not aid:
        return
    try:
        from companion_api.instamart_address import (
            get_default_instamart_address,
            save_default_instamart_address,
        )

        current = get_default_instamart_address(user) or {}
        if str(current.get("address_id") or "").strip() == aid:
            return  # already the default — nothing to persist / invalidate
        tag = line = None
        for r in legacy.get("session_instamart_addresses") or []:
            if isinstance(r, dict) and str(r.get("address_id") or "").strip() == aid:
                tag = (
                    str(r.get("tag") or r.get("addressTag") or r.get("label") or "").strip() or None
                )
                line = (
                    str(
                        r.get("line")
                        or r.get("address_line")
                        or r.get("addressLine")
                        or r.get("location_name")
                        or ""
                    ).strip()
                    or None
                )
                break
        save_default_instamart_address(user, address_id=aid, tag=tag, line=line)
    except Exception:  # pragma: no cover - never block a turn on prefs persistence
        pass


def _persist_zepto_default(user: str, legacy: dict[str, Any]) -> None:
    """Sync the user's saved Zepto default address with the address the turn
    committed to. When the user switches delivery address mid-session (via the
    card "Change" picker OR an explicit "deliver to office"), the new
    ``selected_zepto_address_id`` becomes the DB source of truth so it sticks for
    the next turn / fresh session, and the prefs cache is invalidated. Mirrors
    ``_persist_instamart_default``. No-op when unchanged."""
    aid = str((legacy or {}).get(_SESSION_ZEPTO_ADDRESS) or "").strip()
    if not aid:
        return
    try:
        from companion_api.zepto_address import (
            get_default_zepto_address,
            save_default_zepto_address,
        )

        current = get_default_zepto_address(user) or {}
        if str(current.get("address_id") or "").strip() == aid:
            return  # already the default — nothing to persist / invalidate
        tag = line = None
        for r in legacy.get("session_zepto_addresses") or []:
            if isinstance(r, dict) and str(r.get("address_id") or "").strip() == aid:
                tag = str(r.get("tag") or r.get("label") or "").strip() or None
                line = str(r.get("line") or r.get("address_line") or "").strip() or None
                break
        save_default_zepto_address(user, address_id=aid, tag=tag, line=line)
    except Exception:  # pragma: no cover - never block a turn on prefs persistence
        pass


def _reconcile_zepto_stale_default(user: str, provider_results: list[ProviderResult]) -> None:
    """Clear the DB default Zepto address when the provider determined (from this
    turn's LIVE saved-address list) that the committed address was deleted/renamed
    on the Zepto app. Driven by the provider's own ``stale_committed_address``
    diagnostic — a high-confidence signal (full fetched list), so no false positive
    from a partial round-trip — and only clears when the DB default still equals the
    flagged id. Otherwise the dead id would be re-pinned every turn."""
    stale = ""
    for r in provider_results:
        if r.provider == "zepto":
            stale = str((r.diagnostics or {}).get("stale_committed_address") or "").strip()
            if stale:
                break
    if not stale:
        return
    try:
        from companion_api.zepto_address import (
            clear_default_zepto_address,
            get_default_zepto_address,
        )

        current = str((get_default_zepto_address(user) or {}).get("address_id") or "").strip()
        if current == stale:
            clear_default_zepto_address(user)
    except Exception:  # pragma: no cover - never block a turn on prefs reconcile
        pass


def _tool_execution(results: list[ProviderResult]) -> dict[str, Any]:
    """Legacy ``{ok, error?, error_code?}`` summary for the frontend.

    error_code prefers a provider's granular code (e.g. ``needs_location``) and
    falls back to the coarse ErrorCode enum. location_needed_by names the provider
    that failed with needs_location, and reauth_needed_by lists providers whose
    OAuth login expired — so the frontend can retry / prompt reconnect for just
    those, while still showing the results other providers returned this turn."""
    from core.contracts.tool_result import ErrorCode

    failed = [r for r in results if not r.ok]
    if not failed:
        return {"ok": True}
    first = failed[0]
    error_code: str | None = None
    error_msg = first.error_detail
    location_needed_by: str | None = None
    reauth_needed_by: list[str] = []
    for r in results:
        if r.error == ErrorCode.NEEDS_LOCATION and location_needed_by is None:
            location_needed_by = r.provider
        needs_reauth = r.error == ErrorCode.AUTH_REQUIRED or any(
            tr.error == ErrorCode.AUTH_REQUIRED for tr in r.results
        )
        if needs_reauth and r.provider not in reauth_needed_by:
            reauth_needed_by.append(r.provider)
    for tr in first.results:
        if tr.ok:
            continue
        if error_code is None and isinstance(tr.data, dict) and tr.data.get("error_code"):
            error_code = str(tr.data["error_code"])
        if error_msg is None and tr.error_detail:
            error_msg = tr.error_detail
    if error_code is None and first.error is not None:
        error_code = first.error.value
    out: dict[str, Any] = {
        "ok": False,
        "error": error_msg or "Tool execution failed.",
        "error_code": error_code,
    }
    if location_needed_by:
        out["location_needed_by"] = location_needed_by
    if reauth_needed_by:
        out["reauth_needed_by"] = reauth_needed_by
    return out


def _to_envelope(response: TurnResponse, chat_id: str | None) -> dict[str, Any]:
    session = response.session
    return {
        "status": response.status,
        "assistant_message": response.assistant_message,
        "task_type": response.task_type,
        "routed_provider": response.routed_provider,
        "selected_tool_providers": list(response.selected_tool_providers),
        "tool_execution": _tool_execution(response.provider_results),
        "attachments": [a.to_wire() for a in response.attachments],
        # Flat session_* + session_state round-tripped to the frontend verbatim.
        "session_state": session.placeholders,
        **session.legacy,
        "observable_trace": response.observable_trace,
        "chat_id": chat_id,
        "pipeline": "core",
    }


async def _run(
    data: dict[str, Any],
    user: str,
    reply_language: str | None,
    trace_listener: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    llm, reason = resolve_llm_from_env()
    if llm is None:
        return {"status": "error", "assistant_message": "", "error": reason, "pipeline": "core"}
    request = _to_request(data, user, reply_language)
    if trace_listener:
        tracer = Tracer(listener=trace_listener)
        response = await _get_runner().run(request, llm, tracer=tracer)
    else:
        response = await _get_runner().run(request, llm)
    _persist_instamart_default(user, response.session.legacy)
    _persist_zepto_default(user, response.session.legacy)
    _reconcile_zepto_stale_default(user, response.provider_results)
    return _to_envelope(response, request.chat_id)


def run_core_turn_sync(
    payload: Any,
    user: str,
    *,
    reply_language: str | None = None,
    trace_listener: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Sync entry for the (sync) web handlers; drives the async pipeline once."""
    return asyncio.run(_run(_payload_dict(payload), user, reply_language, trace_listener))
