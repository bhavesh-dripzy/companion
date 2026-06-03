"""Interactive single-turn helper that keeps state between invocations.

Usage:

    # First turn (creates session file)
    python scripts/agent_chat.py "I'm hungry, what's available on zomato"

    # Subsequent turns (loads + updates the session)
    python scripts/agent_chat.py "show me biryani options"

State is persisted to ``--state-file`` (default ``/tmp/companion-agent-session.json``)
so a sequence of script calls behaves like one continuous chat. Use ``--reset`` to
start fresh.

Output is a clean per-turn summary: the assistant's reply, the planner's
tool_plan, the executor's IDs (session_state in/out), and any attachments.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BASE = "http://127.0.0.1:8765"
# Intentionally empty — the new flow auto-resolves the address from
# session_addresses or by prefetching from Zomato MCP. Pass --address-id
# to override (e.g. to test "user picked an address in the UI" path).
DEFAULT_ADDRESS_ID = ""
DEFAULT_USERNAME = "local_admin"
DEFAULT_PASSWORD = "local_password"
DEFAULT_STATE_FILE = "/tmp/companion-agent-session.json"


def _http(method: str, url: str, *, body: dict[str, Any] | None = None, token: str | None = None, timeout: int = 180) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return {"_status": e.code, **json.loads(raw)}
        except Exception:
            return {"_status": e.code, "_body": raw}


def _login(base: str, username: str, password: str) -> str:
    resp = _http("POST", f"{base}/api/login/", body={"username": username, "password": password})
    token = resp.get("access_token")
    if not token:
        raise SystemExit(f"login failed: {resp!r}")
    return str(token)


def _load_state(path: Path) -> dict[str, Any]:
    empty = {
        "turn": 0,
        "history": [],
        "session_state": {},
        "session_restaurants": [],
        "session_movies": [],
        "session_theatres": [],
        "session_events": [],
        "session_venues": [],
        "session_artists": [],
        "session_reservation": None,
        "session_addresses": [],
        "session_menu": [],
        "session_cart": None,
        "pending_variant_choice": [],
        "token": None,
        "chat_id": None,
    }
    if not path.is_file():
        return empty
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return empty
    loaded.setdefault("session_restaurants", [])
    loaded.setdefault("session_movies", [])
    loaded.setdefault("session_theatres", [])
    loaded.setdefault("session_events", [])
    loaded.setdefault("session_venues", [])
    loaded.setdefault("session_artists", [])
    loaded.setdefault("session_reservation", None)
    loaded.setdefault("session_addresses", [])
    loaded.setdefault("session_menu", [])
    loaded.setdefault("session_cart", None)
    loaded.setdefault("pending_variant_choice", [])
    loaded.setdefault("chat_id", None)
    return loaded


def _addr_label(entry: dict[str, Any]) -> str:
    """Best-effort short label for a saved-address row from any provider shape."""
    for key in ("label", "location_name", "addressTag", "address_tag", "address_line"):
        val = str(entry.get(key) or "").strip()
        if val:
            return val
    return str(entry.get("address_id") or "?")


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _short(s: str, n: int = 2000) -> str:
    return s if len(s) <= n else s[:n] + f"\n…[+{len(s) - n} more chars]"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("message", help="The message to send (one turn).")
    ap.add_argument("--base", default=os.getenv("COMPANION_BASE", DEFAULT_BASE))
    ap.add_argument("--address-id", default=os.getenv("COMPANION_ADDRESS_ID", DEFAULT_ADDRESS_ID))
    ap.add_argument("--username", default=os.getenv("LEGACY_ADMIN_USERNAME", DEFAULT_USERNAME))
    ap.add_argument("--password", default=os.getenv("LEGACY_ADMIN_PASSWORD", DEFAULT_PASSWORD))
    ap.add_argument("--state-file", default=os.getenv("COMPANION_STATE_FILE", DEFAULT_STATE_FILE))
    ap.add_argument("--reset", action="store_true", help="Wipe state before this turn.")
    ap.add_argument(
        "--lat",
        type=float,
        default=(float(os.getenv("COMPANION_USER_LAT")) if os.getenv("COMPANION_USER_LAT") else None),
        help="Simulate the frontend's GPS latitude (used by District / Maps).",
    )
    ap.add_argument(
        "--lng",
        type=float,
        default=(float(os.getenv("COMPANION_USER_LNG")) if os.getenv("COMPANION_USER_LNG") else None),
        help="Simulate the frontend's GPS longitude.",
    )
    args = ap.parse_args()

    state_path = Path(args.state_file)
    if args.reset and state_path.exists():
        state_path.unlink()

    state = _load_state(state_path)
    if not state.get("token"):
        state["token"] = _login(args.base, args.username, args.password)

    token: str = state["token"]
    history: list[dict[str, str]] = state.get("history") or []
    session_state: dict[str, str] = state.get("session_state") or {}
    session_restaurants: list[dict[str, Any]] = state.get("session_restaurants") or []
    session_movies: list[dict[str, Any]] = state.get("session_movies") or []
    session_theatres: list[dict[str, Any]] = state.get("session_theatres") or []
    session_events: list[dict[str, Any]] = state.get("session_events") or []
    session_venues: list[dict[str, Any]] = state.get("session_venues") or []
    session_artists: list[dict[str, Any]] = state.get("session_artists") or []
    raw_reservation = state.get("session_reservation")
    session_reservation: dict[str, Any] | None = (
        raw_reservation if isinstance(raw_reservation, dict) else None
    )
    session_addresses: list[dict[str, Any]] = state.get("session_addresses") or []
    session_menu: list[dict[str, Any]] = state.get("session_menu") or []
    session_cart: dict[str, Any] | None = state.get("session_cart") if isinstance(state.get("session_cart"), dict) else None
    pending_variant_choice: list[dict[str, Any]] = state.get("pending_variant_choice") or []
    chat_id: str | None = state.get("chat_id") if isinstance(state.get("chat_id"), str) and state.get("chat_id") else None
    turn_idx = int(state.get("turn") or 0) + 1

    body: dict[str, Any] = {
        "message": args.message,
        "address_id": args.address_id,
        "execute_tools": True,
        "history": history,
    }
    if args.lat is not None and args.lng is not None:
        body["geo"] = {"latitude": args.lat, "longitude": args.lng}
    if session_state:
        body["session_state"] = session_state
    if session_restaurants:
        body["session_restaurants"] = session_restaurants
    if session_movies:
        body["session_movies"] = session_movies
    if session_theatres:
        body["session_theatres"] = session_theatres
    if session_events:
        body["session_events"] = session_events
    if session_venues:
        body["session_venues"] = session_venues
    if session_artists:
        body["session_artists"] = session_artists
    if session_reservation and session_reservation.get("booking_id"):
        body["session_reservation"] = session_reservation
    if session_addresses:
        body["session_addresses"] = session_addresses
    if session_menu:
        body["session_menu"] = session_menu
    if session_cart and session_cart.get("cart_id"):
        body["session_cart"] = session_cart
    if pending_variant_choice:
        body["pending_variant_choice"] = pending_variant_choice
    if chat_id:
        body["chat_id"] = chat_id

    print(f"\n{'═' * 78}")
    print(f"TURN {turn_idx}  →  {args.message!r}")
    print(f"{'═' * 78}")
    addr_in = args.address_id or "(none — let executor auto-resolve)"
    print(f"  address_id IN     : {addr_in}")
    if args.lat is not None and args.lng is not None:
        print(f"  user_geo IN       : lat={args.lat} lng={args.lng}")
    print(f"  chat_id IN        : {chat_id or '(none — backend will lazy-create)'}")
    print(f"  session_state IN  : {session_state}")
    if session_cart:
        ci = ", ".join(
            f"{i.get('item_name')}x{i.get('quantity') or 1}" for i in (session_cart.get("items") or [])[:4]
        )
        print(
            f"  session_cart IN   : cart_id={session_cart.get('cart_id')} "
            f"res={session_cart.get('restaurant_name')!r}({session_cart.get('res_id')}) "
            f"items=[{ci}] total=₹{session_cart.get('final_amount')}"
        )
    if session_restaurants:
        rests = ", ".join(f"{r.get('name')}({r.get('res_id')})" for r in session_restaurants[:5])
        more = "" if len(session_restaurants) <= 5 else f" (+{len(session_restaurants) - 5} more)"
        print(f"  restaurants IN    : {rests}{more}")
    if session_addresses:
        addrs = ", ".join(f"{_addr_label(a)}({a.get('address_id')})" for a in session_addresses[:5])
        more = "" if len(session_addresses) <= 5 else f" (+{len(session_addresses) - 5} more)"
        print(f"  addresses IN      : {addrs}{more}")

    result = _http("POST", f"{args.base}/api/agent/query/", token=token, body=body)

    assistant = str(result.get("assistant_message") or "")
    routed = result.get("routed_provider")
    tool_exec = result.get("tool_execution") or {}
    attachments = result.get("attachments") or []
    next_state = result.get("session_state") or {}
    next_restaurants = result.get("session_restaurants") or []
    next_movies = result.get("session_movies") or []
    next_theatres = result.get("session_theatres") or []
    next_events = result.get("session_events") or []
    next_venues = result.get("session_venues") or []
    next_artists = result.get("session_artists") or []
    next_reservation_raw = result.get("session_reservation")
    next_reservation: dict[str, Any] | None = (
        next_reservation_raw
        if isinstance(next_reservation_raw, dict) and next_reservation_raw.get("booking_id")
        else None
    )
    next_addresses = result.get("session_addresses") or []
    next_menu = result.get("session_menu") or []
    next_cart = result.get("session_cart")
    if not (isinstance(next_cart, dict) and next_cart.get("cart_id")):
        next_cart = None
    next_pending_variant_choice = result.get("pending_variant_choice") or []
    next_chat_id_raw = result.get("chat_id")
    next_chat_id: str | None = next_chat_id_raw if isinstance(next_chat_id_raw, str) and next_chat_id_raw else chat_id
    address_context = result.get("address_context") or None
    plan = result.get("food_routing_plan") or {}
    tool_plan = plan.get("tool_plan") or []
    planned_tools = [s.get("tool") for s in tool_plan if isinstance(s, dict)]

    if next_chat_id and next_chat_id != chat_id:
        print(f"  ↑ new chat_id      : {next_chat_id}")
    elif next_chat_id:
        print(f"  chat_id OUT       : {next_chat_id}")
    print(f"  routed_provider   : {routed!r}")
    print(f"  planned tools     : {planned_tools}")
    print(f"  tool_execution.ok : {tool_exec.get('ok')!r}  error={tool_exec.get('error')!r}")
    print(f"  session_state OUT : {next_state}")
    delta_keys = sorted(set(next_state.keys()) - set(session_state.keys()))
    if delta_keys:
        print(f"  ↑ new IDs learned : {delta_keys}")
    if next_restaurants:
        rests = ", ".join(f"{r.get('name')}({r.get('res_id')})" for r in next_restaurants[:5])
        more = "" if len(next_restaurants) <= 5 else f" (+{len(next_restaurants) - 5} more)"
        print(f"  restaurants OUT   : {rests}{more}")
    if next_movies:
        ms = ", ".join(f"{m.get('name')}({m.get('movie_id')})" for m in next_movies[:5])
        print(f"  movies OUT        : {ms}")
    if next_theatres:
        ts = ", ".join(f"{t.get('name')}({t.get('theatre_id')})" for t in next_theatres[:5])
        print(f"  theatres OUT      : {ts}")
    if next_events:
        es = ", ".join(f"{e.get('name')}({e.get('event_id')})" for e in next_events[:5])
        print(f"  events OUT        : {es}")
    if next_venues:
        vs = ", ".join(f"{v.get('name')}({v.get('venue_id')})" for v in next_venues[:5])
        print(f"  venues OUT        : {vs}")
    if next_artists:
        ars = ", ".join(f"{a.get('name')}({a.get('artist_id')})" for a in next_artists[:5])
        print(f"  artists OUT       : {ars}")
    if next_reservation:
        print(
            f"  reservation OUT   : booking_id={next_reservation.get('booking_id')} "
            f"res={next_reservation.get('restaurant_name')!r}({next_reservation.get('res_id')}) "
            f"date={next_reservation.get('booking_date')} time={next_reservation.get('slot_time')} "
            f"party={next_reservation.get('party_size')} status={next_reservation.get('status')!r}"
        )
    if next_addresses:
        addrs = ", ".join(f"{_addr_label(a)}({a.get('address_id')})" for a in next_addresses[:5])
        more = "" if len(next_addresses) <= 5 else f" (+{len(next_addresses) - 5} more)"
        print(f"  addresses OUT     : {addrs}{more}")
        new_addr_ids = sorted(
            {str(a.get("address_id") or "") for a in next_addresses}
            - {str(a.get("address_id") or "") for a in session_addresses}
        )
        if new_addr_ids:
            print(f"  ↑ new addresses    : {new_addr_ids}")
    if next_menu:
        items = ", ".join(
            f"{m.get('item_name')}({m.get('variant_id')})" for m in next_menu[:5]
        )
        more = "" if len(next_menu) <= 5 else f" (+{len(next_menu) - 5} more)"
        print(f"  menu items OUT    : {items}{more}")
    if next_cart:
        ci = ", ".join(
            f"{i.get('item_name')}x{i.get('quantity') or 1}" for i in (next_cart.get("items") or [])[:4]
        )
        replaced = ""
        if session_cart and session_cart.get("res_id") and session_cart.get("res_id") != next_cart.get("res_id"):
            replaced = f"  (REPLACED prior cart at {session_cart.get('restaurant_name')!r})"
        print(
            f"  session_cart OUT  : cart_id={next_cart.get('cart_id')} "
            f"res={next_cart.get('restaurant_name')!r}({next_cart.get('res_id')}) "
            f"items=[{ci}] total=₹{next_cart.get('final_amount')}{replaced}"
        )
    elif session_cart:
        print(f"  session_cart OUT  : (cleared — no cart in this response)")
    if address_context:
        active_id = address_context.get("address_id")
        label = address_context.get("label")
        source = address_context.get("source")
        print(f"  address_context   : id={active_id!r} label={label!r} source={source!r}")
    if next_pending_variant_choice:
        for pvc in next_pending_variant_choice:
            iname = pvc.get("item_name") or pvc.get("item_id")
            groups_desc = ", ".join(
                f"{g.get('name')}({len(g.get('choices') or [])})" for g in (pvc.get("groups") or [])
            )
            print(f"  ⏸ pending variant  : {iname} → {groups_desc}")
    print(f"  attachments       : {len(attachments)} item(s)")
    for j, att in enumerate(attachments):
        kind = att.get('kind')
        if kind == 'menu_cards':
            items = att.get('items') or []
            print(
                f"    [{j}] kind='menu_cards' restaurant={att.get('restaurant_name')!r} "
                f"res_id={att.get('res_id')} cards={len(items)}"
            )
            for k, it in enumerate(items):
                price = it.get('discounted_price') or it.get('price')
                veg = it.get('veg_kind')
                print(f"        - {it.get('name')} (vid={it.get('variant_id')}, ₹{price}, {veg})")
        elif kind == 'cart_card':
            c = att.get('cart') or {}
            ci = ", ".join(
                f"{i.get('item_name')}x{i.get('quantity') or 1}"
                for i in (c.get('items') or [])[:4]
            )
            print(
                f"    [{j}] kind='cart_card' source={att.get('source')!r} "
                f"cart_id={c.get('cart_id')} res={c.get('restaurant_name')!r} "
                f"items=[{ci}] total=₹{c.get('final_amount')}"
            )
        elif kind == 'restaurant_cards':
            restos = att.get('restaurants') or []
            print(
                f"    [{j}] kind='restaurant_cards' keyword={att.get('keyword')!r} "
                f"restaurants={len(restos)}"
            )
            for k, r in enumerate(restos):
                items = r.get('items') or []
                print(
                    f"        ▸ {r.get('name')} (res_id={r.get('res_id')}, "
                    f"⭐{r.get('rating')}, {r.get('eta')}, {r.get('distance_km')}km, "
                    f"items={len(items)})"
                )
                for it in items[:3]:
                    print(
                        f"            • {it.get('name')} (vid={it.get('variant_id')}, "
                        f"₹{it.get('price')}, {it.get('veg_kind')})"
                    )
        elif kind == 'dining_cards':
            rows = att.get('dining_restaurants') or []
            print(f"    [{j}] kind='dining_cards' provider='district' cards={len(rows)}")
            for r in rows[:4]:
                print(
                    f"        ▸ {r.get('name')} (res_id={r.get('res_id')}, "
                    f"⭐{r.get('rating')}, {r.get('locality')}, bookable={r.get('bookable_via_agent')})"
                )
        elif kind == 'movie_cards':
            rows = att.get('movies') or []
            print(f"    [{j}] kind='movie_cards' provider='district' cards={len(rows)}")
            for m in rows[:4]:
                langs = ",".join(m.get('languages') or [])
                print(
                    f"        ▸ {m.get('name')} (movie_id={m.get('movie_id')}, "
                    f"{langs}, ⭐{m.get('rating')}, {m.get('duration_minutes')}min)"
                )
        elif kind == 'theatre_cards':
            rows = att.get('theatres') or []
            print(f"    [{j}] kind='theatre_cards' provider='district' cards={len(rows)}")
            for t in rows[:4]:
                print(
                    f"        ▸ {t.get('name')} (theatre_id={t.get('theatre_id')}, "
                    f"{t.get('locality')}, {t.get('distance')})"
                )
        elif kind == 'show_cards':
            rows = att.get('shows') or []
            print(f"    [{j}] kind='show_cards' provider='district' cards={len(rows)}")
            for s in rows[:4]:
                print(
                    f"        ▸ {s.get('movie_name')} @ {s.get('theatre_name')} "
                    f"({s.get('show_start_time')}, {s.get('screen_type')}, "
                    f"₹{s.get('min_price')})"
                )
        elif kind == 'event_cards':
            rows = att.get('events') or []
            print(f"    [{j}] kind='event_cards' provider='district' cards={len(rows)}")
            for e in rows[:4]:
                print(
                    f"        ▸ {e.get('name')} (event_id={e.get('event_id')}, "
                    f"{e.get('date_string')}, {e.get('venue_name')}, "
                    f"{e.get('price_string')})"
                )
        elif kind == 'venue_cards':
            rows = att.get('venues') or []
            print(f"    [{j}] kind='venue_cards' provider='district' cards={len(rows)}")
            for v in rows[:4]:
                print(
                    f"        ▸ {v.get('name')} (venue_id={v.get('venue_id')}, "
                    f"{v.get('locality')}, {v.get('city')})"
                )
        elif kind == 'artist_cards':
            rows = att.get('artists') or []
            print(f"    [{j}] kind='artist_cards' provider='district' cards={len(rows)}")
            for a in rows[:4]:
                print(
                    f"        ▸ {a.get('name')} (artist_id={a.get('artist_id')}, "
                    f"{a.get('artist_type')}, upcoming={a.get('upcoming_event_count')})"
                )
        elif kind == 'reservation_card':
            r = att.get('reservation') or {}
            print(
                f"    [{j}] kind='reservation_card' provider='district' "
                f"booking_id={r.get('booking_id')} res={r.get('restaurant_name')!r} "
                f"({r.get('booking_date')} {r.get('slot_time')}) "
                f"party={r.get('party_size')} status={r.get('status')!r}"
            )
        else:
            print(
                f"    [{j}] kind={kind!r} mime={att.get('mime_type')!r} "
                f"has_b64={bool(att.get('data_b64'))} amount=₹{att.get('amount_inr')} "
                f"upi={att.get('upi_link')!r}"
            )
    print()
    print("ASSISTANT:")
    print(_short(assistant))

    # Persist
    state["turn"] = turn_idx
    state["history"] = history + [
        {"role": "user", "content": args.message},
        {"role": "assistant", "content": assistant},
    ]
    if isinstance(next_state, dict):
        state["session_state"] = {k: str(v) for k, v in next_state.items() if isinstance(k, str)}
    if isinstance(next_restaurants, list):
        state["session_restaurants"] = next_restaurants
    if isinstance(next_movies, list):
        state["session_movies"] = next_movies
    if isinstance(next_theatres, list):
        state["session_theatres"] = next_theatres
    if isinstance(next_events, list):
        state["session_events"] = next_events
    if isinstance(next_venues, list):
        state["session_venues"] = next_venues
    if isinstance(next_artists, list):
        state["session_artists"] = next_artists
    state["session_reservation"] = next_reservation
    if isinstance(next_addresses, list):
        state["session_addresses"] = next_addresses
    if isinstance(next_menu, list):
        state["session_menu"] = next_menu
    state["session_cart"] = next_cart
    state["chat_id"] = next_chat_id
    if isinstance(next_pending_variant_choice, list):
        state["pending_variant_choice"] = next_pending_variant_choice
    _save_state(state_path, state)

    if result.get("_status"):
        print(f"\n!! HTTP error {result['_status']}: {result.get('detail') or result.get('_body')}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
