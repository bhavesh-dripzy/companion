"""Stage 1 — Personalize: read the profile and resolve the delivery address.

Address rule (parity): an explicit request address wins; otherwise home/office/work
keywords resolve to a saved address ONLY via its `provider_address_id` — free-text
addresses are never used as an id (they become geo elsewhere).
"""

from __future__ import annotations

from core.middleware.selection import resolve_selections
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


def _resolve_selections(ctx: PipelineContext) -> None:
    """Commit any saved entity the user switched to by name ("deliver to Work").

    Generic across every provider that declares `selectables` — the resolved id is
    written to the provider's committed session key, which its before_run reads."""
    legacy = ctx.session.legacy if isinstance(ctx.session.legacy, dict) else {}
    selectables = [s for p in ctx.deps.registry.all() for s in getattr(p, "selectables", ())]
    if not selectables:
        return
    commits = resolve_selections(ctx.request.message, legacy, selectables)
    if commits:
        legacy.update(commits)
        ctx.session.legacy = legacy
        ctx.tracer.event("select", committed=list(commits))


class Personalize:
    name = "personalize"

    async def run(self, ctx: PipelineContext) -> None:
        ctx.session = ctx.request.session
        if ctx.request.user_id:
            ctx.profile = await ctx.deps.profile_service.get(ctx.request.user_id)
        _resolve_address(ctx)
        _resolve_selections(ctx)
        ctx.tracer.event(
            "personalize",
            has_profile=ctx.profile is not None,
            address_id=ctx.session.selected_address_id,
        )
