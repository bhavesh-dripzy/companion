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
        user_geo=data.get("user_geo") or data.get("geo"),
        enabled_providers=data.get("enabled_providers"),
    )


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
    out: dict[str, Any] = {"ok": False, "error": error_msg or "Tool execution failed.", "error_code": error_code}
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
