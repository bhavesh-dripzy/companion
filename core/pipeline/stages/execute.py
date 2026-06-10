"""Stage 4 — Execute: confirmation-gate the plan, then fan out to providers.

Mutating / two-step tools are held back unless the turn is confirmed (the gate is
a safety net independent of the planner prompt). Surviving calls are dispatched
concurrently per provider via the middleware Dispatcher (ADR-0003).
"""

from __future__ import annotations

from core.contracts.tool import ConfirmPolicy
from core.middleware.confirmation import partition
from core.middleware.dispatcher import Dispatcher
from core.middleware.repair import RepairBudget
from core.pipeline.context import PipelineContext
from core.providers.base import ExecCtx
from core.providers.mcp.placeholders import provider_view


def _exec_ctx_for(ctx: PipelineContext, provider_id: str, budget: RepairBudget) -> ExecCtx:
    plan = ctx.plans.get(provider_id)
    tools = ctx.deps.registry.tools.for_provider(provider_id)
    return ExecCtx(
        user_id=ctx.request.user_id,
        address_id=ctx.session.selected_address_id,
        user_geo=ctx.request.user_geo,
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
        ctx.provider_results = await dispatcher.dispatch(
            allowed, ctx_for=lambda pid: _exec_ctx_for(ctx, pid, budget)
        )
        ctx.tracer.event(
            "execute",
            providers=[r.provider for r in ctx.provider_results],
            blocked=len(ctx.blocked),
        )
