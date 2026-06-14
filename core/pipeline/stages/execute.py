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


def _exec_ctx_for(
    ctx: PipelineContext,
    provider_id: str,
    budget: RepairBudget,
    geo_overrides: dict[str, dict[str, float]],
) -> ExecCtx:
    plan = ctx.plans.get(provider_id)
    tools = ctx.deps.registry.tools.for_provider(provider_id)
    user_geo: Any = geo_overrides.get(provider_id) or ctx.request.user_geo
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
        ctx.provider_results = await dispatcher.dispatch(
            allowed, ctx_for=lambda pid: _exec_ctx_for(ctx, pid, budget, geo_overrides)
        )
        provider_tools = [
            {
                "provider": r.provider,
                "ok": r.ok,
                "tools": [
                    {
                        "tool": tr.tool,
                        "ok": tr.ok,
                        "error": tr.error.value if tr.error else None,
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
