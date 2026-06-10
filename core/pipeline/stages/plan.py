"""Stage 3 — Plan: each routed provider's planner LLM -> a validated ToolPlan.

The planner prompt is generated from the provider's registered tools (no markdown
lists). A plugin may supply a behavioural `planner_preamble`; otherwise a generic
one is used. Plans are validated (alias + drop-unknown) and merged into one call
list, each call tagged with its provider for dispatch.
"""

from __future__ import annotations

from core.middleware.planner_prompt import build_planner_prompt
from core.middleware.validator import normalize_plan
from core.pipeline.clock import clock_line
from core.pipeline.context import PipelineContext

_GENERIC_PREAMBLE = (
    "You translate the user's message into a machine-ready tool plan.\n"
    "Respond with ONLY a JSON object shaped exactly like:\n"
    '{"intent": "<snake_case>", "summary": "<one sentence>", '
    '"tool_plan": [{"tool": "<exact tool name>", "arguments": {}}], '
    '"constraints": {"requires_address_resolution": false}}\n'
    "Use the smallest useful chain of tools. Use placeholders like "
    '"__ADDRESS_ID__" / "__CART_ID__" only when a prior step supplies them.\n'
    "{{TOOLS}}"
)


def _format_input(ctx: PipelineContext) -> str:
    # Every planner gets the clock — LLMs otherwise fill date arguments from
    # their training-era prior and date-validated upstreams reject the call.
    lines: list[str] = [clock_line()]
    for turn in ctx.request.history[-8:]:
        role = str(turn.get("role") or "user")
        content = str(turn.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    lines.append(f"user: {ctx.request.message}")
    if ctx.session.selected_address_id:
        lines.append(f"(resolved address_id: {ctx.session.selected_address_id})")
    return "\n".join(lines)


class Plan:
    name = "plan"

    async def run(self, ctx: PipelineContext) -> None:
        if not ctx.routing or not ctx.routing.providers:
            return
        framed = _format_input(ctx)
        for provider_id in ctx.routing.providers:
            provider = ctx.deps.registry.get(provider_id)
            # A provider may supply its own tuned planner (the MCP adapters delegate
            # to the legacy domain agents); otherwise use the generated-prompt planner.
            plan = await self._delegated_plan(provider, ctx)
            if plan is None:
                plan = await self._generic_plan(provider, provider_id, framed, ctx)
            ctx.plans[provider_id] = plan
            ctx.tracer.event("plan", provider=provider_id, tools=len(plan.calls))

        ctx.calls = [
            call.model_copy(update={"provider": call.provider or provider_id})
            for provider_id, plan in ctx.plans.items()
            for call in plan.calls
        ]

    @staticmethod
    async def _delegated_plan(provider, ctx):
        planner = getattr(provider, "plan", None)
        if not callable(planner):
            return None
        try:
            return await planner(ctx)
        except Exception:
            return None

    @staticmethod
    async def _generic_plan(provider, provider_id, framed, ctx):
        tools = ctx.deps.registry.tools.for_provider(provider_id)
        preamble = getattr(provider, "planner_preamble", None) or _GENERIC_PREAMBLE
        system = build_planner_prompt(preamble, tools)
        # A plugin may prepend its own session brief (cached restaurants/cart/etc.)
        # so the planner reuses ids instead of re-searching.
        context_fn = getattr(provider, "planner_context", None)
        brief = context_fn(ctx) if callable(context_fn) else ""
        user_message = f"{brief}\n\n{framed}" if brief else framed
        try:
            raw = await ctx.deps.complete_json(
                user_message=user_message, system_prompt=system, llm=ctx.llm
            )
        except Exception:
            raw = {}
        plan, _dropped = normalize_plan(raw, provider=provider_id, tools=ctx.deps.registry.tools)
        return plan
