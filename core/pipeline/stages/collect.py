"""Stage 5 — Collect: lift renderable attachments from each provider's results.

Providers are visited in a fixed priority order so attachments render
deterministically; each plugin orders its own attachments internally (e.g.
payment -> menu -> restaurant -> cart). Unlisted providers follow, in result order.
"""

from __future__ import annotations

from core.pipeline.context import PipelineContext

# Fixed render priority across providers (parity with the legacy hoist order).
_PROVIDER_ORDER = (
    "zomato",
    "swiggy_food",
    "swiggy_instamart",
    "district",
    "zepto",
    "kite",
)


def _ordered_results(ctx: PipelineContext):
    by_priority = {pid: i for i, pid in enumerate(_PROVIDER_ORDER)}
    return sorted(
        ctx.provider_results,
        key=lambda r: (by_priority.get(r.provider, len(_PROVIDER_ORDER)), r.provider),
    )


class Collect:
    name = "collect"

    async def run(self, ctx: PipelineContext) -> None:
        for result in _ordered_results(ctx):
            provider = ctx.deps.registry.get(result.provider)
            if provider is None:
                continue
            ctx.attachments.extend(provider.build_attachments(result.results))
        ctx.tracer.event("collect", attachments=len(ctx.attachments))
