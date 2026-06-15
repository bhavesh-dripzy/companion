"""Stage 7 — Persist: merge session state and publish the turn (side-channel).

Each provider folds its own results into its session slice (`provider.merge`).
Learned placeholders are merged centrally. The turn event goes to the KG seam
publisher fire-and-forget — it must never block or fail the reply (ADR-0004).
"""

from __future__ import annotations

import asyncio
import uuid

from core.contracts.turn_event import TurnEvent
from core.pipeline.context import PipelineContext
from core.providers.mcp.placeholders import fold_learned


def _routed_provider(ctx: PipelineContext) -> str | None:
    if ctx.routing and ctx.routing.providers:
        return ctx.routing.providers[0]
    return None


class Persist:
    name = "persist"

    async def run(self, ctx: PipelineContext) -> None:
        for result in ctx.provider_results:
            provider = ctx.deps.registry.get(result.provider)
            if provider is not None:
                # Adapters fold their session_* into the flat legacy bag; each
                # provider only touches its own keys, so chaining is safe.
                ctx.session.legacy = provider.merge(ctx.session.legacy, result)
            for tool_result in result.results:
                fold_learned(
                    ctx.session.placeholders, result.provider, tool_result.learned_placeholders
                )

        if ctx.request.user_id:
            event = TurnEvent(
                user_id=ctx.request.user_id,
                turn_id=str(uuid.uuid4()),
                user_message=ctx.request.message,
                assistant_message=ctx.assistant_message,
                task_type=ctx.routing.task_type if ctx.routing else None,
                routed_provider=_routed_provider(ctx),
                geo=ctx.request.user_geo,
            )
            # Async KG propagation (off-path) and the Tier-1 immediate personal-fact
            # write run concurrently; both are fail-open and must not block the reply.
            await asyncio.gather(
                ctx.deps.publisher.publish(event),
                self._capture_personal_facts(ctx),
            )
        ctx.tracer.event("persist", providers=len(ctx.provider_results))

    @staticmethod
    async def _capture_personal_facts(ctx: PipelineContext) -> None:
        writer = ctx.deps.personal_fact_writer
        if writer is None:
            return
        await writer.capture(
            user_id=ctx.request.user_id,
            user_message=ctx.request.message,
            assistant_message=ctx.assistant_message,
            complete_json=ctx.deps.complete_json,
            llm=ctx.llm,
        )
