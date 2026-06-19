"""Stage 4 — Execute: confirmation-gate the plan, then fan out to providers.

Mutating / two-step tools are held back unless the turn is confirmed (the gate is
a safety net independent of the planner prompt). Surviving calls are dispatched
concurrently per provider via the middleware Dispatcher (ADR-0003).

Location rule (all providers): a locality the user explicitly named this turn
(`plan.constraints.requested_location_text`) is geocoded and OVERRIDES ambient
GPS in `ExecCtx.user_geo` — "events in Delhi" must search Delhi even when the
phone sits in Dubai. Resolved once here so every geo-consuming provider
(district, serp places, future ones) inherits the behavior.
"""

from __future__ import annotations

from typing import Any

from core.contracts.tool import ConfirmPolicy
from core.middleware.attestation import attest
from core.middleware.confirmation import partition
from core.middleware.dispatcher import Dispatcher
from core.middleware.repair import RepairBudget
from core.pipeline.context import PipelineContext
from core.providers.base import ExecCtx
from core.providers.mcp.placeholders import provider_view


async def _named_location_geo(ctx: PipelineContext) -> dict[str, dict[str, float]]:
    """Per-provider user_geo overrides from each plan's user-named locality."""
    if ctx.deps.geocode is None:
        return {}
    overrides: dict[str, dict[str, float]] = {}
    resolved: dict[str, tuple[float, float] | None] = {}
    for provider_id, plan in ctx.plans.items():
        text = (plan.constraints.requested_location_text or "").strip()
        if not text:
            continue
        if text not in resolved:
            try:
                resolved[text] = await ctx.deps.geocode.geocode(text)
            except Exception:
                resolved[text] = None
        point = resolved[text]
        if point is not None:
            overrides[provider_id] = {"latitude": point[0], "longitude": point[1]}
    return overrides


def _valid_geo(geo: Any) -> dict[str, float] | None:
    """Normalize a geo dict to {latitude, longitude} floats, or None if absent."""
    if not isinstance(geo, dict):
        return None
    lat = geo.get("latitude", geo.get("lat"))
    lng = geo.get("longitude", geo.get("lng"))
    if lat is None or lng is None:
        return None
    try:
        return {"latitude": float(lat), "longitude": float(lng)}
    except (TypeError, ValueError):
        return None


def _committed_address_text(legacy: dict[str, Any]) -> str | None:
    """The user's committed delivery address line (Instamart selection first,
    then any saved Instamart/Zepto address) for geocoding a fallback geo."""
    selected = str(legacy.get("selected_instamart_address_id") or "").strip()
    sources = [legacy.get("session_instamart_addresses"), legacy.get("session_zepto_addresses")]
    for i, rows in enumerate(sources):
        if not isinstance(rows, list):
            continue
        if i == 0 and selected:
            for row in rows:
                if isinstance(row, dict) and str(row.get("address_id") or "") == selected:
                    line = str(row.get("address_line") or "").strip()
                    if line:
                        return line
        for row in rows:
            if isinstance(row, dict):
                line = str(row.get("address_line") or "").strip()
                if line:
                    return line
    return None


async def _fallback_geo(ctx: PipelineContext) -> dict[str, float] | None:
    """Turn geo for providers that need a location but got none named this turn.

    Prefers the turn's GPS; when the client withheld it, geocodes the user's
    committed delivery address so a store-locating provider (Zepto) resolves
    instead of fail-closing — the asymmetry that made grocery comparisons (one
    side serviceable, one side "needs your location") flaky. Generic: every
    geo-consuming provider inherits the same fallback.
    """
    turn = _valid_geo(ctx.request.user_geo)
    if turn or ctx.deps.geocode is None:
        return turn
    legacy = ctx.session.legacy if isinstance(ctx.session.legacy, dict) else {}
    text = _committed_address_text(legacy)
    if not text:
        return None
    try:
        point = await ctx.deps.geocode.geocode(text)
    except Exception:
        return None
    return {"latitude": point[0], "longitude": point[1]} if point else None


def _exec_ctx_for(
    ctx: PipelineContext,
    provider_id: str,
    budget: RepairBudget,
    geo_overrides: dict[str, dict[str, float]],
    fallback_geo: dict[str, float] | None,
) -> ExecCtx:
    plan = ctx.plans.get(provider_id)
    tools = ctx.deps.registry.tools.for_provider(provider_id)
    user_geo: Any = geo_overrides.get(provider_id) or fallback_geo
    return ExecCtx(
        user_id=ctx.request.user_id,
        address_id=ctx.session.selected_address_id,
        user_geo=user_geo,
        # Each provider sees only its own placeholder slice — a __RESTAURANT_ID__
        # learned on one platform must never substitute into another's tools.
        placeholders=provider_view(ctx.session.placeholders, provider_id),
        session=dict(ctx.session.legacy),  # the flat legacy bag the adapters read
        llm=ctx.llm,
        requested_location_text=plan.constraints.requested_location_text if plan else None,
        complete_json=ctx.deps.complete_json,
        tool_schemas={t.name: t.params for t in tools},
        intent_summary=(plan.summary if plan else None) or ctx.request.message,
        repair_budget=budget,
        idempotent_tools=frozenset(t.name for t in tools if t.confirm is ConfirmPolicy.NEVER),
    )


class Execute:
    name = "execute"

    async def run(self, ctx: PipelineContext) -> None:
        if not ctx.calls or not ctx.request.execute_tools:
            return
        allowed = []
        for provider_id, plan in ctx.plans.items():
            ok_calls, blocked = partition(
                plan, provider=provider_id, tools=ctx.deps.registry.tools, confirmed=ctx.confirmed
            )
            allowed.extend(
                c.model_copy(update={"provider": c.provider or provider_id}) for c in ok_calls
            )
            ctx.blocked.extend(blocked)
        if not allowed:
            ctx.tracer.event("execute", ran=0, blocked=len(ctx.blocked))
            return
        dispatcher = Dispatcher(ctx.deps.registry)
        budget = RepairBudget()  # shared across this turn's provider fan-out
        geo_overrides = await _named_location_geo(ctx)
        fallback_geo = await _fallback_geo(ctx)
        ctx.provider_results = await dispatcher.dispatch(
            allowed,
            ctx_for=lambda pid: _exec_ctx_for(ctx, pid, budget, geo_overrides, fallback_geo),
        )
        attest(ctx.provider_results, ctx.deps.registry.tools)
        provider_tools = [
            {
                "provider": r.provider,
                "ok": r.ok,
                "tools": [
                    {
                        "tool": tr.tool,
                        "ok": tr.ok,
                        "error": tr.error.value if tr.error else None,
                        "effect": tr.effect.value if tr.effect else None,
                    }
                    for tr in r.results
                ],
            }
            for r in ctx.provider_results
        ]
        ctx.tracer.event(
            "execute",
            providers=[r.provider for r in ctx.provider_results],
            provider_tools=provider_tools,
            blocked=len(ctx.blocked),
        )
