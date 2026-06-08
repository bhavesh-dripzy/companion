"""Legacy planner delegation (migration bridge).

A clean `McpProvider` ports execution + card-building first; porting its planner
*prompt* to the generated-prompt path is a separate, later task. Until then the
clean provider delegates planning to the tuned legacy domain agent so plan
quality matches the legacy exactly — the same bridge `LegacyMcpAdapter` uses.
Shared here so the two paths don't drift while both exist (deleted at Phase 8).
"""

from __future__ import annotations

import asyncio
from typing import Any

# session_* kwargs the legacy `_format_routing_message` context builder accepts.
FORMAT_SESSION_KEYS = frozenset(
    {
        "session_restaurants",
        "session_addresses",
        "session_menu",
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
    }
)


async def legacy_plan(provider_id: str, ctx: Any) -> Any:
    """Plan via the provider's tuned legacy domain agent; None to fall back.

    Reuses `_format_routing_message` (session context) and `_plan_for_provider`
    (the domain agent), then normalizes the raw plan against the registry so the
    result is a validated `ToolPlan` the Plan stage can use directly.
    """
    raw = await asyncio.to_thread(_plan_sync, provider_id, ctx)
    if not isinstance(raw, dict):
        return None
    from core.middleware.validator import normalize_plan

    plan, _dropped = normalize_plan(raw, provider=provider_id, tools=ctx.deps.registry.tools)
    return plan


def _plan_sync(provider_id: str, ctx: Any) -> dict[str, Any] | None:
    from app.services.agents.pipeline import _format_routing_message, _plan_for_provider

    session = ctx.session
    profile = ctx.profile.model_dump() if ctx.profile else None
    bag = {k: v for k, v in (session.legacy or {}).items() if k in FORMAT_SESSION_KEYS}
    routing_message = _format_routing_message(
        ctx.request.message,
        ctx.request.history,
        session_state_keys=list(session.placeholders.keys()),
        selected_address_id=session.selected_address_id,
        pending_variant_choice=session.pending_variant_choice or None,
        user_profile=profile,
        **bag,
    )
    return _plan_for_provider(
        provider_id, routing_message=routing_message, llm=ctx.llm, user_id=ctx.request.user_id or ""
    )
