"""No-op boundary implementations for tests and local runs without KG infra."""

from __future__ import annotations

from core.contracts.profile import Profile
from core.contracts.turn_event import TurnEvent


class EmptyProfileService:
    async def get(self, user_id: str) -> Profile | None:
        return None


class NoopPublisher:
    def __init__(self) -> None:
        self.published: list[TurnEvent] = []

    async def publish(self, event: TurnEvent) -> None:
        self.published.append(event)


class NoopPersonalFactWriter:
    def __init__(self) -> None:
        self.captured: list[dict] = []

    async def capture(self, *, user_id, user_message, assistant_message, complete_json, llm) -> None:
        self.captured.append({"user_id": user_id, "user_message": user_message})
