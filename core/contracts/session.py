"""Typed session state.

Replaces the ~28 `session_*` kwargs and the 300-line merge loop with a root that
namespaces per-provider sub-state. Plugins own their slice and their `merge`
(provider session classes live in the plugins, so this contract stays free of
provider-specific shapes — slices are stored raw and parsed by the plugin).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProviderSessionState(BaseModel):
    """Base for a provider's namespaced session slice.

    Plugins subclass this (e.g. `ZomatoSession`) and override `merged` to fold a
    turn's tool results into the next state.
    """

    model_config = ConfigDict(extra="allow")

    def merged(self, results: list[Any]) -> ProviderSessionState:  # pragma: no cover - overridden
        return self


class SessionState(BaseModel):
    """The full per-conversation working state echoed to/from the client."""

    model_config = ConfigDict(extra="allow")

    # Cross-provider opaque IDs learned during the session (__CART_ID__, ...).
    placeholders: dict[str, str] = Field(default_factory=dict)
    # provider id -> its raw namespaced slice (clean-provider target, post-cutover).
    providers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # Flat legacy session bag (session_restaurants / session_menu / session_cart / ...)
    # used by the migration adapters and round-tripped to the frontend verbatim.
    legacy: dict[str, Any] = Field(default_factory=dict)
    pending_variant_choice: list[dict[str, Any]] = Field(default_factory=list)
    selected_address_id: str | None = None

    def slice_for(self, provider: str) -> dict[str, Any]:
        """Return the raw session slice for a provider (empty if none yet)."""
        return self.providers.get(provider, {})
