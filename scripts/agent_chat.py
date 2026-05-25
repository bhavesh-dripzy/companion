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
        "session_addresses": [],
        "pending_variant_choice": [],
        "token": None,
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
    loaded.setdefault("session_addresses", [])
    loaded.setdefault("pending_variant_choice", [])
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
    session_addresses: list[dict[str, Any]] = state.get("session_addresses") or []
    pending_variant_choice: list[dict[str, Any]] = state.get("pending_variant_choice") or []
    turn_idx = int(state.get("turn") or 0) + 1

    body: dict[str, Any] = {
        "message": args.message,
        "address_id": args.address_id,
        "execute_tools": True,
        "history": history,
    }
    if session_state:
        body["session_state"] = session_state
    if session_restaurants:
        body["session_restaurants"] = session_restaurants
    if session_movies:
        body["session_movies"] = session_movies
    if session_theatres:
        body["session_theatres"] = session_theatres
    if session_addresses:
        body["session_addresses"] = session_addresses
    if pending_variant_choice:
        body["pending_variant_choice"] = pending_variant_choice

    print(f"\n{'═' * 78}")
    print(f"TURN {turn_idx}  →  {args.message!r}")
    print(f"{'═' * 78}")
    addr_in = args.address_id or "(none — let executor auto-resolve)"
    print(f"  address_id IN     : {addr_in}")
    print(f"  session_state IN  : {session_state}")
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
    next_addresses = result.get("session_addresses") or []
    next_pending_variant_choice = result.get("pending_variant_choice") or []
    address_context = result.get("address_context") or None
    plan = result.get("food_routing_plan") or {}
    tool_plan = plan.get("tool_plan") or []
    planned_tools = [s.get("tool") for s in tool_plan if isinstance(s, dict)]

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
        print(
            f"    [{j}] kind={att.get('kind')!r} mime={att.get('mime_type')!r} "
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
    if isinstance(next_addresses, list):
        state["session_addresses"] = next_addresses
    if isinstance(next_pending_variant_choice, list):
        state["pending_variant_choice"] = next_pending_variant_choice
    _save_state(state_path, state)

    if result.get("_status"):
        print(f"\n!! HTTP error {result['_status']}: {result.get('detail') or result.get('_body')}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
