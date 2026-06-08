"""Rich attachments rendered inline in chat (cards, carts, payment QR).

A plugin's `build_attachments` emits these; the `Collect` stage concatenates them
in a fixed priority order so the frontend renders them deterministically. Each
attachment carries a `kind` plus arbitrary kind-specific fields.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class Attachment(BaseModel):
    """One renderable attachment. `kind` selects the frontend renderer."""

    model_config = ConfigDict(extra="allow")

    kind: str

    def to_wire(self) -> dict[str, Any]:
        """Flatten to the dict shape the frontend expects."""
        return self.model_dump(exclude_none=True)
