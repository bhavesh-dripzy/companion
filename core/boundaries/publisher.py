"""Turn-event publisher adapter — wraps the SQS leadgen queue, fire-and-forget.

Errors are swallowed so a queue hiccup never breaks the user's reply (the legacy
`send_turn_event` already does this; we preserve it). The blocking send runs in a
thread; the import is lazy so `core.boundaries` needs no AWS in tests.
"""

from __future__ import annotations

import asyncio
import contextlib

from core.contracts.turn_event import TurnEvent


class SqsTurnEventPublisher:
    async def publish(self, event: TurnEvent) -> None:
        # fire-and-forget; never block or fail the turn
        with contextlib.suppress(Exception):
            await asyncio.to_thread(self._send, event)

    @staticmethod
    def _send(event: TurnEvent) -> None:
        from companion_api.leadgen_queue import send_turn_event

        send_turn_event(
            user_id=event.user_id,
            message_id=event.turn_id,
            user_message=event.user_message,
            assistant_message=event.assistant_message,
            task_type=event.task_type,
            routed_provider=event.routed_provider,
            session_addresses=event.session_addresses,
            user_geo=event.geo,
            phone=event.phone,
            member_id=event.member_id,
        )
