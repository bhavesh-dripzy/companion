"""Stage 4 — Execute: confirmation-gate the plan, then fan out to providers.

Mutating / two-step tools are held back unless the turn is confirmed (the gate is
a safety net independent of the planner prompt). Surviving calls are dispatched
concurrently per provider via the middleware Dispatcher (ADR-0003).
"""

from __future__ import annotations

from core.middleware.confirmation import partition
from core.middleware.dispatcher import Dispatcher
from core.pipeline.context import PipelineContext
from core.providers.base import ExecCtx


def _exec_ctx_for(ctx: PipelineContext, provider_id: str) -> ExecCtx:
    return ExecCtx(
        user_id=ctx.request.user_id,
        address_id=ctx.session.selected_address_id,
        user_geo=ctx.request.user_geo,
        placeholders=dict(ctx.session.placeholders),
        session=dict(ctx.session.slice_for(provider_id)),
        llm=ctx.llm,
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
        ctx.provider_results = await dispatcher.dispatch(
            allowed, ctx_for=lambda pid: _exec_ctx_for(ctx, pid)
        )
        ctx.tracer.event(
            "execute",
            providers=[r.provider for r in ctx.provider_results],
            blocked=len(ctx.blocked),
        )
