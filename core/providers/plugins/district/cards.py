"""District card dispatcher (clean port of `_collect_district_attachments`).

A data-driven table maps each discovery tool to its envelope list-key, row
builder, attachment kind/field, dedup id, and the session key its entities feed.
Distinct attachment field names (e.g. `dining_restaurants`, not `restaurants`)
keep the chat-side `<Attachment>` shape unambiguous vs the Zomato/Swiggy lifters.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool_result import ToolResult
from core.providers.plugins.district.discovery import (
    dining_card,
    envelope_data,
    movie_card,
    show_card,
    theatre_card,
)
from core.providers.plugins.district.experiences import artist_card, event_card, venue_card

_MAX_CARDS = 8


@dataclass(frozen=True)
class _Spec:
    tool: str
    list_key: str
    builder: Callable[[Any], dict[str, Any] | None]
    kind: str
    id_key: str
    attach_field: str
    session_key: str | None  # None ⇒ not carried in session (shows, details dup)


SPECS: tuple[_Spec, ...] = (
    _Spec(
        "search_dining_restaurants",
        "restaurants",
        dining_card,
        "dining_cards",
        "res_id",
        "dining_restaurants",
        "session_restaurants",
    ),
    _Spec(
        "get_dining_restaurants_details",
        "restaurants",
        dining_card,
        "dining_cards",
        "res_id",
        "dining_restaurants",
        None,
    ),
    _Spec(
        "search_movies", "movies", movie_card, "movie_cards", "movie_id", "movies", "session_movies"
    ),
    _Spec(
        "search_theatres",
        "theatres",
        theatre_card,
        "theatre_cards",
        "theatre_id",
        "theatres",
        "session_theatres",
    ),
    _Spec("search_shows", "shows", show_card, "show_cards", "show_id", "shows", None),
    _Spec(
        "search_events", "events", event_card, "event_cards", "event_id", "events", "session_events"
    ),
    _Spec(
        "search_event_venues",
        "venues",
        venue_card,
        "venue_cards",
        "venue_id",
        "venues",
        "session_venues",
    ),
    _Spec(
        "search_artists",
        "artists",
        artist_card,
        "artist_cards",
        "artist_id",
        "artists",
        "session_artists",
    ),
)
_BY_TOOL = {s.tool: s for s in SPECS}


def cards_for(spec: _Spec, envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Build deduped, capped cards from one envelope for a given spec."""
    data = envelope_data(envelope)
    rows = data.get(spec.list_key) if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        if len(cards) >= _MAX_CARDS:
            break
        card = spec.builder(raw)
        if card is None:
            continue
        ident = str(card.get(spec.id_key) or "").strip()
        if ident and ident in seen:
            continue
        if ident:
            seen.add(ident)
        cards.append(card)
    return cards


def build_attachments(results: list[ToolResult]) -> list[Attachment]:
    out: list[Attachment] = []
    for tr in results:
        spec = _BY_TOOL.get(tr.tool)
        if spec is None or not tr.ok:
            continue
        envelope = tr.data if isinstance(tr.data, dict) else {}
        if envelope.get("is_error"):
            continue
        cards = cards_for(spec, envelope)
        if not cards:
            continue
        out.append(
            Attachment(
                kind=spec.kind,
                source=f"district.{tr.tool}",
                provider="district",
                **{spec.attach_field: cards},
            )
        )
    return out
