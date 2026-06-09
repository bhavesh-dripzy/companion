"""The Stage protocol: each pipeline stage is a small unit that mutates the context."""

from __future__ import annotations

from typing import Protocol

from core.pipeline.context import PipelineContext


class Stage(Protocol):
    name: str

    async def run(self, ctx: PipelineContext) -> None:
        """Advance the turn by mutating `ctx` in place."""
        ...
