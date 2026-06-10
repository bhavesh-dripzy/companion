"""District planner session-context renderer (clean port of the District blocks
of legacy ``_format_routing_message`` + ``_date_context_for_planner``).

``plan._generic_plan`` prepends the string returned by ``build_context`` to the
planner input. The transcript, current user turn and the ``(resolved address_id …)``
line are already added by ``_format_input`` — this renders ONLY the session-state
BRIEF blocks plus a District date-context line (so relative phrases like "tonight"
or "this weekend" resolve against IST, not the LLM's training-time clock).

Blocks emitted (each only when its session slice is non-empty), in order:
  * date context (always)
  * carried-forward placeholders
  * restaurants / movies / theatres / events / venues / artists already shown
  * active reservation
Header wording, caps and row shapes mirror the legacy renderer verbatim.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from core.providers.mcp.planner_context import placeholders_block

# District operates in India and the LLM has no clock of its own; resolve "today"
# / "tomorrow" / "this weekend" against IST. ZoneInfo handles DST/offset cleanly;
# fall back to a fixed +05:30 offset if the tz database is unavailable.
try:
    _IST: Any = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover - missing tzdata
    _IST = timezone(timedelta(hours=5, minutes=30))


def _date_context() -> str:
    """Compact today/tomorrow/weekend hint, mirroring the legacy District agent."""
    today = datetime.now(_IST).date()
    tomorrow = today + timedelta(days=1)
    # Next Saturday + Sunday for "this weekend".
    days_to_sat = (5 - today.weekday()) % 7
    sat = today + timedelta(days=days_to_sat or 7 if today.weekday() > 5 else days_to_sat)
    sun = sat + timedelta(days=1)
    return (
        f"[Date context — Asia/Kolkata]\n"
        f"today = {today.isoformat()} ({today.strftime('%A')})\n"
        f"tomorrow = {tomorrow.isoformat()} ({tomorrow.strftime('%A')})\n"
        f"this_weekend = {sat.isoformat()} to {sun.isoformat()}\n"
        f"Use these when resolving 'today', 'tomorrow', 'this weekend', etc."
    )


def _rows(entries: Any, id_key: str, cap: int) -> list[str]:
    """Render the shared ``  - <name> → <id_key>=<id>`` rows for a session list."""
    out: list[str] = []
    if not isinstance(entries, list):
        return out
    for entry in entries[:cap]:
        if not isinstance(entry, dict):
            continue
        ident = str(entry.get(id_key) or "").strip()
        name = str(entry.get("name") or "").strip()
        if ident and name:
            out.append(f"  - {name} → {id_key}={ident}")
    return out


def _restaurants_block(entries: Any) -> str | None:
    rows: list[str] = []
    if not isinstance(entries, list):
        return None
    for entry in entries[:10]:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        rid = str(entry.get("res_id") or "").strip()
        if not name or not rid:
            continue
        rating = entry.get("rating")
        tail = f" (rating {rating})" if rating not in (None, "") else ""
        rows.append(f"  - {name} → res_id={rid}{tail}")
    if not rows:
        return None
    return (
        "Restaurants already shown to the user this session — if the "
        "user picks one of these by name, use the literal res_id below "
        "instead of running get_restaurants_for_keyword again "
        "(Zomato's keyword search is fuzzy and often returns the wrong "
        "restaurant for short brand names):\n" + "\n".join(rows)
    )


def _movies_block(entries: Any) -> str | None:
    rows = _rows(entries, "movie_id", 10)
    if not rows:
        return None
    return (
        "Movies already shown this session — use literal movie_id "
        "below for showtimes/reviews instead of re-searching:\n" + "\n".join(rows)
    )


def _theatres_block(entries: Any) -> str | None:
    rows = _rows(entries, "theatre_id", 10)
    if not rows:
        return None
    return (
        "Theatres already shown this session — use literal theatre_id "
        "below for showtimes instead of re-searching:\n" + "\n".join(rows)
    )


def _events_block(entries: Any) -> str | None:
    rows: list[str] = []
    if not isinstance(entries, list):
        return None
    for entry in entries[:10]:
        if not isinstance(entry, dict):
            continue
        eid = str(entry.get("event_id") or "").strip()
        name = str(entry.get("name") or "").strip()
        if eid and name:
            date_hint = str(entry.get("date_string") or "").strip()
            tail = f" [{date_hint}]" if date_hint else ""
            rows.append(f"  - {name} → event_id={eid}{tail}")
    if not rows:
        return None
    return (
        "Events already shown this session — use literal event_id "
        "below for venue/show follow-ups instead of re-searching:\n" + "\n".join(rows)
    )


def _venues_block(entries: Any) -> str | None:
    rows = _rows(entries, "venue_id", 10)
    if not rows:
        return None
    return (
        "Event venues already shown this session — use literal venue_id "
        "below instead of re-searching:\n" + "\n".join(rows)
    )


def _artists_block(entries: Any) -> str | None:
    rows = _rows(entries, "artist_id", 10)
    if not rows:
        return None
    return (
        "Artists already shown this session — use literal artist_id "
        "below instead of re-searching:\n" + "\n".join(rows)
    )


def _reservation_block(reservation: Any) -> str | None:
    if not isinstance(reservation, dict):
        return None
    bid = str(reservation.get("booking_id") or "").strip()
    if not bid:
        return None
    who = str(reservation.get("restaurant_name") or "").strip() or "the restaurant"
    when_bits = [
        str(reservation.get("booking_date") or "").strip(),
        str(reservation.get("slot_time") or "").strip(),
    ]
    when = " ".join(b for b in when_bits if b).strip() or "the chosen slot"
    party = reservation.get("party_size")
    party_hint = f" for {party}" if isinstance(party, int) and party > 0 else ""
    return (
        "Active District reservation in session — use booking_id "
        f"{bid} (__BOOKING_ID__) when the user asks for status / "
        f"cancellation; details: {who} at {when}{party_hint}."
    )


def build_context(ctx: Any) -> str:
    """Render the District session brief prepended to the planner input.

    Returns the present blocks joined by blank lines. The date-context block is
    always present, so the return is non-empty in practice; ``""`` is returned
    only if every block (including date context) renders empty.
    """
    session = getattr(ctx, "session", None)
    legacy: dict[str, Any] = getattr(session, "legacy", {}) or {}
    placeholders: dict[str, Any] = getattr(session, "placeholders", {}) or {}

    parts: list[str | None] = [
        _date_context(),
        placeholders_block(placeholders, "district"),
        _restaurants_block(legacy.get("session_restaurants")),
        _movies_block(legacy.get("session_movies")),
        _theatres_block(legacy.get("session_theatres")),
        _events_block(legacy.get("session_events")),
        _venues_block(legacy.get("session_venues")),
        _artists_block(legacy.get("session_artists")),
        _reservation_block(legacy.get("session_reservation")),
    ]
    return "\n\n".join(p for p in parts if p)
