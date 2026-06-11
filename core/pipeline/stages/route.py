"""Stage 2 — Route: orchestrator LLM -> RoutingDecision (task + providers).

Stickiness parity: frames the message with the previous provider; a malformed
orchestrator response sticks with that provider rather than defaulting.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from core.pipeline.context import PipelineContext
from core.pipeline.orchestration import (
    expand_agnostic,
    extract_previous_provider,
    fallback_routing,
    frame_message,
    parse_routing,
)

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "orchestrator.system.md"


@lru_cache(maxsize=1)
def _orchestrator_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8").strip()


class Route:
    name = "route"

    def __init__(self, prompt: str | None = None) -> None:
        self._prompt = prompt or _orchestrator_prompt()

    async def run(self, ctx: PipelineContext) -> None:
        previous = extract_previous_provider(ctx.request.history)
        framed = frame_message(ctx.request.message, previous)
        try:
            raw = await ctx.deps.complete_json(
                user_message=framed, system_prompt=self._prompt, llm=ctx.llm
            )
            ctx.routing = parse_routing(raw, previous)
        except Exception:  # orchestrator unparseable/unavailable -> stick with previous
            ctx.routing = fallback_routing(previous)
        if os.getenv("AGNOSTIC_FANOUT", "true").strip().lower() == "true":
            ctx.routing = expand_agnostic(
                ctx.routing,
                ctx.request.message,
                enabled=True,
                allowed=ctx.request.enabled_providers,
            )
        ctx.tracer.event("route", task=ctx.routing.task_type, providers=list(ctx.routing.providers))
