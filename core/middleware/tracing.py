"""Lightweight execution tracing.

Records ordered events (and timed spans) for the observable trace returned in the
response envelope and for debugging. Replaces the scattered `debug_trace.trace`
calls; the per-stage trace assembly lives in the Collect stage (Phase 3).

An optional ``listener`` receives every event as it happens (plus notify-only
``progress`` markers that are never recorded), so callers can stream
human-facing progress — e.g. the async turn worker writing rolling status
lines — without changing the envelope's observable_trace shape.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import contextmanager, suppress
from typing import Any


class Tracer:
    def __init__(self, listener: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.events: list[dict[str, Any]] = []
        self.listener = listener

    def _notify(self, payload: dict[str, Any]) -> None:
        if self.listener is None:
            return
        with suppress(Exception):
            self.listener(payload)

    def event(self, name: str, **fields: Any) -> None:
        entry = {"event": name, **fields}
        self.events.append(entry)
        self._notify(entry)

    def progress(self, event_name: str, **fields: Any) -> None:
        """Notify-only marker (e.g. a stage starting); never recorded."""
        self._notify({"event": event_name, "progress": True, **fields})

    @contextmanager
    def span(self, name: str, **fields: Any):
        start = time.perf_counter()
        try:
            yield
        finally:
            entry = {
                "event": name,
                "ms": round((time.perf_counter() - start) * 1000, 1),
                **fields,
            }
            self.events.append(entry)
            self._notify(entry)

    def to_list(self) -> list[dict[str, Any]]:
        return list(self.events)


# A no-op tracer for paths that don't need tracing.
class NullTracer(Tracer):
    def event(self, name: str, **fields: Any) -> None:  # pragma: no cover - trivial
        pass
