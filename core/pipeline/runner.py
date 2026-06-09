"""PipelineRunner — threads the request through the staged main pipeline.

`run(TurnRequest, llm) -> TurnResponse`. Chat and voice share this exact runner;
voice only differs by `reply_language` on the request. Stages are injectable so a
custom pipeline (or a test harness) can swap any of them.
"""

from __future__ import annotations

from collections.abc import Sequence

from core.contracts.turn import TurnRequest, TurnResponse
from core.llm.config import ResolvedLlm
from core.pipeline.context import PipelineContext, PipelineDeps
from core.pipeline.stage import Stage
from core.pipeline.stages.collect import Collect
from core.pipeline.stages.execute import Execute
from core.pipeline.stages.narrate import Narrate
from core.pipeline.stages.persist import Persist
from core.pipeline.stages.personalize import Personalize
from core.pipeline.stages.plan import Plan
from core.pipeline.stages.route import Route

_CONFIRM_WORDS = (
    "confirm",
    "yes go ahead",
    "place the order",
    "place order",
    "go ahead",
    "proceed",
    "book it",
    "haan",
    "kar do",
)


def default_stages() -> list[Stage]:
    return [Personalize(), Route(), Plan(), Execute(), Collect(), Narrate(), Persist()]


def _is_confirmation(request: TurnRequest) -> bool:
    extra = request.model_extra or {}
    if extra.get("confirmed") is True:
        return True
    message = (request.message or "").lower().strip()
    return any(word in message for word in _CONFIRM_WORDS)


def _build_response(ctx: PipelineContext) -> TurnResponse:
    routing = ctx.routing
    providers = list(routing.providers) if routing and routing.providers else []
    return TurnResponse(
        assistant_message=ctx.assistant_message,
        task_type=routing.task_type if routing else None,
        routed_provider=(providers[0] if providers else None),
        selected_tool_providers=providers,
        provider_results=ctx.provider_results,
        session=ctx.session,
        attachments=ctx.attachments,
        status="ok",
        observable_trace=ctx.tracer.to_list(),
    )


class PipelineRunner:
    def __init__(self, deps: PipelineDeps, stages: Sequence[Stage] | None = None) -> None:
        self.deps = deps
        self.stages: list[Stage] = list(stages) if stages is not None else default_stages()

    async def run(self, request: TurnRequest, llm: ResolvedLlm) -> TurnResponse:
        ctx = PipelineContext(request=request, llm=llm, deps=self.deps)
        ctx.confirmed = _is_confirmation(request)
        for stage in self.stages:
            await stage.run(ctx)
        return _build_response(ctx)
