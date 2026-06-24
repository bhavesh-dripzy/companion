"""Turn event stream — the contract between the backend (director) and the frontend (player).

A turn emits an ordered stream of small, transport-agnostic events. The backend owns the narrative
and may run tools CONCURRENTLY; it emits events in COMPLETION order (whichever tool finishes first
narrates first — there is no hard-coded provider order). The frontend is a generic, order-agnostic
player: it types out `say` text and builds `cards` blocks as they arrive, paced for a "thinking live"
feel. Neither side knows the other's internals — they talk only through these events.

The vocabulary is intentionally tiny so new domains / tools / providers never extend it — they just
emit `say` + `cards`. `say` payloads are clean, speakable phrases so a future text-to-speech layer
can read them verbatim. The transport (SSE today; WebSocket / Lambda response-stream later) lives
below `Emit` and is swappable without touching any caller.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

# A sink for turn events. The streaming transport passes a real one; the plain request/response
# path passes `noop`, so the exact same pipeline code serves both with zero branching.
Emit = Callable[[dict[str, Any]], Awaitable[None]]


async def noop(_ev: dict[str, Any]) -> None:
    pass


def say(text: str) -> dict[str, Any]:
    """A line of narration to type out (and, later, speak via TTS). Keep it a clean phrase."""
    return {"type": "say", "text": text}


def cards(attachments: list[dict[str, Any]], provider: str | None = None) -> dict[str, Any]:
    """A block of UI cards to build in when the player reaches this point in the stream."""
    return {"type": "cards", "attachments": attachments, "provider": provider}


def status(action: str, provider: str | None = None) -> dict[str, Any]:
    """A transient 'what I'm doing right now' hint for the live working indicator — the action verb
    (search/add/set_qty/…) plus which provider it targets. NOT narration: the player shows it only
    while waiting for the next content, then drops it once real `say`/`cards` arrive. Domain-agnostic
    (the frontend maps action+provider to friendly copy), so the vocabulary stays tiny."""
    return {"type": "status", "action": action, "provider": provider}
