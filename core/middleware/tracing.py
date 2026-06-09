"""Lightweight execution tracing.

Records ordered events (and timed spans) for the observable trace returned in the
response envelope and for debugging. Replaces the scattered `debug_trace.trace`
calls; the per-stage trace assembly lives in the Collect stage (Phase 3).
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any


class Tracer:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def event(self, name: str, **fields: Any) -> None:
        self.events.append({"event": name, **fields})

    @contextmanager
    def span(self, name: str, **fields: Any):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.events.append(
                {"event": name, "ms": round((time.perf_counter() - start) * 1000, 1), **fields}
            )

    def to_list(self) -> list[dict[str, Any]]:
        return list(self.events)


# A no-op tracer for paths that don't need tracing.
class NullTracer(Tracer):
    def event(self, name: str, **fields: Any) -> None:  # pragma: no cover - trivial
        pass
