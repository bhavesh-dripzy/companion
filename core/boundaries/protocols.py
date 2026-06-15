"""The KG / leadgen seam — the only contract the pipeline knows about (#28).

`Personalize` depends on `ProfileReadService`; `Persist` depends on
`TurnEventPublisher`. The pipeline imports neither `profile_cache`, `leadgen_queue`,
nor `graph_client` — those live only in the adapters here (ADR-0004).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from core.contracts.profile import Profile
from core.contracts.turn_event import TurnEvent
from core.llm.config import ResolvedLlm


@runtime_checkable
class ProfileReadService(Protocol):
    async def get(self, user_id: str) -> Profile | None:
        """Return the user's profile projection, or None (fail-open)."""
        ...


@runtime_checkable
class PersonalFactWriter(Protocol):
    async def capture(
        self,
        *,
        user_id: str,
        user_message: str,
        assistant_message: str,
        complete_json: Callable[..., Awaitable[dict[str, Any]]],
        llm: ResolvedLlm,
    ) -> None:
        """Extract durable personal facts from this turn and write them to the
        profile cache immediately (Tier-1, fire-and-forget, fail-open)."""
        ...


@runtime_checkable
class TurnEventPublisher(Protocol):
    async def publish(self, event: TurnEvent) -> None:
        """Publish a conversation turn for async KG extraction (fire-and-forget)."""
        ...


@runtime_checkable
class GeocodeService(Protocol):
    async def geocode(self, text: str) -> tuple[float, float] | None:
        """Resolve a free-text locality to (lat, lng), or None (fail-open)."""
        ...
