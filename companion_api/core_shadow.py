"""Run a turn through the new core pipeline (shadow mode / cutover bridge).

Imported lazily by the web layer ONLY when `CORE_PIPELINE` is enabled, so the
live default path is completely untouched until cutover (Phase 8). Adapts the
FastAPI request payload to a core `TurnRequest`, runs the `PipelineRunner`, and
returns a legacy-shaped envelope. Use it in the live-fixture session to exercise
the core pipeline end-to-end against real providers.
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.app_factory import build_runner
from core.contracts.session import SessionState
from core.contracts.turn import TurnRequest, TurnResponse
from core.llm.config import resolve_llm_from_env

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
    "session_kite_instruments",
    "session_kite_holdings",
    "session_kite_positions",
    "session_kite_open_orders",
    "session_kite_order_ticket",
)

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


def _to_envelope(response: TurnResponse, chat_id: str | None) -> dict[str, Any]:
    session = response.session
    return {
        "status": response.status,
        "assistant_message": response.assistant_message,
        "task_type": response.task_type,
        "routed_provider": response.routed_provider,
        "attachments": [a.to_wire() for a in response.attachments],
        # Flat session_* + session_state round-tripped to the frontend verbatim.
        "session_state": session.placeholders,
        **session.legacy,
        "observable_trace": response.observable_trace,
        "chat_id": chat_id,
        "pipeline": "core",
    }


async def _run(data: dict[str, Any], user: str, reply_language: str | None) -> dict[str, Any]:
    llm, reason = resolve_llm_from_env(secret_or_env("FOOD_ROUTER_PROVIDER"))
    if llm is None:
        return {"status": "error", "assistant_message": "", "error": reason, "pipeline": "core"}
    request = _to_request(data, user, reply_language)
    response = await _get_runner().run(request, llm)
    return _to_envelope(response, request.chat_id)


def run_core_turn_sync(
    payload: Any, user: str, *, reply_language: str | None = None
) -> dict[str, Any]:
    """Sync entry for the (sync) web handlers; drives the async pipeline once."""
    return asyncio.run(_run(_payload_dict(payload), user, reply_language))
