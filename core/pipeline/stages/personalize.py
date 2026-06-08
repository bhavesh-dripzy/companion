"""Stage 1 — Personalize: read the profile and resolve the delivery address.

Address rule (parity): an explicit request address wins; otherwise home/office/work
keywords resolve to a saved address ONLY via its `provider_address_id` — free-text
addresses are never used as an id (they become geo elsewhere).
"""

from __future__ import annotations

from core.pipeline.context import PipelineContext

_HOME_WORDS = ("home", "ghar")
_OFFICE_WORDS = ("office", "work", "kaam", "daftar")


def _resolve_address(ctx: PipelineContext) -> None:
    if ctx.request.address_id:
        ctx.session.selected_address_id = ctx.request.address_id
        return
    if ctx.session.selected_address_id or ctx.profile is None:
        return
    message = (ctx.request.message or "").lower()
    target = None
    if any(word in message for word in _OFFICE_WORDS):
        target = "office"
    elif any(word in message for word in _HOME_WORDS):
        target = "home"
    if not target:
        return
    address = ctx.profile.addresses.get(target)
    if address and address.provider_address_id:
        ctx.session.selected_address_id = address.provider_address_id


class Personalize:
    name = "personalize"

    async def run(self, ctx: PipelineContext) -> None:
        ctx.session = ctx.request.session
        if ctx.request.user_id:
            ctx.profile = await ctx.deps.profile_service.get(ctx.request.user_id)
        _resolve_address(ctx)
        ctx.tracer.event(
            "personalize",
            has_profile=ctx.profile is not None,
            address_id=ctx.session.selected_address_id,
        )
