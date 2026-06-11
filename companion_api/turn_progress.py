"""Map pipeline trace events to human-friendly rolling status lines.

`ProgressNarrator` is a Tracer listener (see core.middleware.tracing): it
receives stage-start progress markers and stage-completion events, turns the
interesting ones into short status lines ("Searching Zomato and Swiggy Food…"),
and emits each NEW line through the supplied callback — the async turn worker
points that callback at the job store so the frontend can poll rolling updates.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any


def provider_label(provider_id: str) -> str:
    """Human name for a provider id: ``swiggy_food`` → ``Swiggy Food``."""
    return str(provider_id).replace("_", " ").strip().title()


def _join_labels(providers: list[str]) -> str:
    labels = [provider_label(p) for p in providers if str(p).strip()]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + " and " + labels[-1]


class ProgressNarrator:
    """Stateful trace listener producing deduplicated human status lines."""

    def __init__(self, emit: Callable[[str], None]) -> None:
        self._emit = emit
        self._providers: list[str] = []
        self._last: str | None = None

    def _say(self, line: str) -> None:
        if not line or line == self._last:
            return
        self._last = line
        with contextlib.suppress(Exception):
            self._emit(line)

    def __call__(self, event: dict[str, Any]) -> None:
        name = event.get("event")
        if name == "stage" and event.get("progress"):
            self._on_stage_start(str(event.get("name") or ""))
        elif name == "route":
            providers = event.get("providers")
            self._providers = [str(p) for p in providers] if isinstance(providers, list) else []
        elif name == "collect":
            count = event.get("attachments")
            if isinstance(count, int) and count > 0:
                self._say(f"Found {count} result{'s' if count != 1 else ''} — comparing them…")

    def _on_stage_start(self, stage: str) -> None:
        if stage == "route":
            self._say("Figuring out the best way to help…")
        elif stage == "plan" and self._providers:
            self._say(f"Checking {_join_labels(self._providers)}…")
        elif stage == "execute" and self._providers:
            self._say(
                f"Searching {_join_labels(self._providers)} — this can take a few seconds…"
            )
        elif stage == "narrate":
            self._say("Putting your answer together…")
