"""The conversation-turn event published across the KG seam.

`Persist` hands one of these to a `TurnEventPublisher`; the SQS implementation
serializes it to the leadgen queue. Defined in `core` so the pipeline never
imports the leadgen package (ADR-0004).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class TurnEvent(BaseModel):
    user_id: str
    turn_id: str
    user_message: str
    assistant_message: str
    task_type: str | None = None
    routed_provider: str | None = None
    session_addresses: list[dict[str, Any]] | None = None
    geo: dict[str, Any] | None = None
    phone: str | None = None
    member_id: str | None = None
    ts: str | None = None
