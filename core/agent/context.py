"""ContextService — builds a provider's ExecCtx, seeding the per-provider default on first use.

(v1: seeds from the provider's own saved addresses via `adapter.default_context`. The durable,
serviceability-validated DefaultContext + in-chat change flow plug in here next — §5c.)
"""

from __future__ import annotations

from core.agent.providers.base import ExecCtx
from core.agent.state import ConversationState


class ContextService:
    async def exec_ctx(self, adapter, session, state: ConversationState) -> ExecCtx:
        ps = state.provider_session.setdefault(adapter.id, {})
        if not ps.get("address_id"):
            ps.update(await adapter.default_context(session, state.user_id) or {})
        # mirror a human label into selected_context for the card header
        for d in adapter.domains:
            state.selected_context.setdefault(
                d, {"type": "address", "provider": adapter.id, "address_id": ps.get("address_id")}
            )
        return ExecCtx(
            user_id=state.user_id,
            address_id=ps.get("address_id"),
            lat=ps.get("lat"),
            lng=ps.get("lng"),
            device_id=ps.get("device_id"),
            store_id=ps.get("store_id"),
        )


CONTEXT = ContextService()
