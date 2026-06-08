"""The KG / leadgen seam — the only contract the pipeline knows about (#28).

`Personalize` depends on `ProfileReadService`; `Persist` depends on
`TurnEventPublisher`. The pipeline imports neither `profile_cache`, `leadgen_queue`,
nor `graph_client` — those live only in the adapters here (ADR-0004).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from core.contracts.profile import Profile
from core.contracts.turn_event import TurnEvent


@runtime_checkable
class ProfileReadService(Protocol):
    async def get(self, user_id: str) -> Profile | None:
        """Return the user's profile projection, or None (fail-open)."""
        ...


@runtime_checkable
class TurnEventPublisher(Protocol):
    async def publish(self, event: TurnEvent) -> None:
        """Publish a conversation turn for async KG extraction (fire-and-forget)."""
        ...
