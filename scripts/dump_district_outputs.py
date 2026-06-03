"""Dump raw District MCP responses for representative tool calls.

Mirrors the Zomato / Swiggy `outputs/` dump pattern but for the District provider
(dining reservations, movies, events). Drives the live District MCP via
`mcp-remote` (same OAuth tokens the executor uses) and writes each call as an
``{tool, arguments, step_index, result}`` envelope into ``outputs/district/``.

Tools that mutate state (`create_table_reservation_booking`) or that require
pre-existing user state (`get_booking_details`) are skipped — they would either
charge the user or fail without a real booking id.

Usage:
    ./.venv/bin/python scripts/dump_district_outputs.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "outputs" / "district"

# Koramangala — same anchor used in the executor's offline locality table.
LAT, LNG = 12.9352, 77.6245
LOCATION = {"latitude": LAT, "longitude": LNG}


def _payload(call_result: Any) -> dict[str, Any]:
    """Reduce a CallToolResult to a JSON-serializable envelope."""

    is_error = bool(getattr(call_result, "isError", False))
    text_chunks: list[str] = []
    structured = getattr(call_result, "structuredContent", None)

    for block in getattr(call_result, "content", []) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            text_chunks.append(text)

    data: Any = None
    if text_chunks:
        joined = "\n".join(text_chunks).strip()
        try:
            data = json.loads(joined)
        except Exception:
            data = joined
    elif structured is not None:
        data = structured

    return {
        "is_error": is_error,
        "data": data,
        "structured": structured,
    }


def _write(name: str, envelope: dict[str, Any]) -> Path:
    out = OUTPUT_DIR / f"{name}.json"
    out.write_text(json.dumps(envelope, indent=2, ensure_ascii=False))
    body = envelope.get("result", {}).get("data")
    summary: str
    if isinstance(body, dict):
        summary = f"keys={sorted(body.keys())[:6]}"
    elif isinstance(body, list):
        summary = f"list len={len(body)}"
    elif isinstance(body, str):
        summary = f"text len={len(body)}"
    else:
        summary = f"type={type(body).__name__}"
    print(f"  wrote {out.relative_to(REPO_ROOT)}  ({summary})")
    return out


async def _call(session, step_idx: int, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    print(f"-> {tool}  args={json.dumps(arguments, default=str)[:160]}")
    raw = await asyncio.wait_for(session.call_tool(tool, arguments=arguments), timeout=45)
    return {
        "tool": tool,
        "arguments": arguments,
        "step_index": step_idx,
        "result": _payload(raw),
    }


def _harvest_first_id(envelope: dict[str, Any], *keys: str) -> str | None:
    body = envelope.get("result", {}).get("data")

    def walk(obj: Any) -> str | None:
        if isinstance(obj, dict):
            for k in keys:
                val = obj.get(k)
                if val is not None and str(val).strip():
                    return str(val).strip()
            for v in obj.values():
                found = walk(v)
                if found is not None:
                    return found
        elif isinstance(obj, list):
            for v in obj:
                found = walk(v)
                if found is not None:
                    return found
        return None

    return walk(body)


def _harvest_first_filter_value(envelope: dict[str, Any], filter_name: str) -> str | None:
    """Locate a returned dynamic-filter row and return one of its values."""

    body = envelope.get("result", {}).get("data")

    def walk(obj: Any) -> str | None:
        if isinstance(obj, dict):
            name = obj.get("name") or obj.get("filter_name")
            values = obj.get("values") or obj.get("filter_values")
            if name == filter_name and isinstance(values, list) and values:
                first = str(values[0]).strip()
                if first:
                    return first
            for v in obj.values():
                found = walk(v)
                if found is not None:
                    return found
        elif isinstance(obj, list):
            for v in obj:
                found = walk(v)
                if found is not None:
                    return found
        return None

    return walk(body)


async def main() -> None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    proxy_script = REPO_ROOT / "node_modules/mcp-remote/dist/proxy.js"
    if not proxy_script.is_file():
        print("mcp-remote missing — run `npm install` at the repo root.", file=sys.stderr)
        sys.exit(1)

    mcp_url = os.environ.get("DISTRICT_MCP_URL", "https://mcp-server.district.in/mcp")
    env = {**os.environ}
    env.setdefault(
        "MCP_REMOTE_CONFIG_DIR",
        str(REPO_ROOT / ".mcp-auth/users/local_admin"),
    )

    params = StdioServerParameters(
        command="node",
        args=[str(proxy_script), mcp_url],
        cwd=str(REPO_ROOT),
        env=env,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")
    today_iso = datetime.now().strftime("%Y-%m-%d")

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            # --- 1. Tools catalog ----------------------------------------
            tools = await asyncio.wait_for(session.list_tools(), timeout=30)
            catalog = {
                "ok": True,
                "tools": [
                    {
                        "name": t.name,
                        "description": t.description,
                        "input_schema": t.inputSchema,
                    }
                    for t in tools.tools
                ],
            }
            (OUTPUT_DIR / "_district_tools_catalog.json").write_text(
                json.dumps(catalog, indent=2, ensure_ascii=False)
            )
            print(f"wrote outputs/district/_district_tools_catalog.json ({len(catalog['tools'])} tools)")

            step = 0

            # --- 2. Dining flow ------------------------------------------
            env_filters_dining = await _call(
                session,
                step,
                "get_dynamic_filters",
                {"user_location": LOCATION},
            )
            _write("get_dynamic_filters_dining", env_filters_dining)
            step += 1

            env_search_dining = await _call(
                session,
                step,
                "search_dining_restaurants",
                {"user_location": LOCATION, "count": 10},
            )
            _write("search_dining_restaurants_koramangala", env_search_dining)
            step += 1

            res_id = _harvest_first_id(env_search_dining, "res_id", "restaurant_id", "id")
            if res_id:
                env_details = await _call(
                    session,
                    step,
                    "get_dining_restaurants_details",
                    {"user_location": LOCATION, "res_ids": [res_id]},
                )
                _write(f"get_dining_restaurants_details_{res_id}", env_details)
                step += 1

                env_slots = await _call(
                    session,
                    step,
                    "get_table_reservation_slots",
                    {"res_id": res_id, "booking_date": tomorrow, "party_size": 2},
                )
                _write(f"get_table_reservation_slots_{res_id}", env_slots)
                step += 1
            else:
                print("  (no res_id harvested — skipping details/slots)")

            # --- 3. Movie flow -------------------------------------------
            env_filters_movie = await _call(
                session,
                step,
                "get_dynamic_filters_movie",
                {"user_location": LOCATION},
            )
            _write("get_dynamic_filters_movie", env_filters_movie)
            step += 1

            env_movies = await _call(
                session,
                step,
                "search_movies",
                {"location": LOCATION, "filters": []},
            )
            _write("search_movies_koramangala", env_movies)
            step += 1

            env_theatres = await _call(
                session,
                step,
                "search_theatres",
                {"location": LOCATION, "filters": []},
            )
            _write("search_theatres_koramangala", env_theatres)
            step += 1

            movie_id = _harvest_first_id(env_movies, "movie_id", "id")
            theatre_id = _harvest_first_id(env_theatres, "theatre_id", "theater_id", "id")
            if movie_id and theatre_id:
                env_shows = await _call(
                    session,
                    step,
                    "search_shows",
                    {
                        "location": LOCATION,
                        "filters": [
                            {"filter_name": "movie_id", "filter_values": [str(movie_id)]},
                            {"filter_name": "theatre_id", "filter_values": [str(theatre_id)]},
                            {"filter_name": "show_date", "filter_values": [today_iso]},
                        ],
                    },
                )
                _write(f"search_shows_{movie_id}_{theatre_id}", env_shows)
                step += 1
            else:
                print(f"  (no movie/theatre ids harvested — skipping shows; movie={movie_id} theatre={theatre_id})")

            if movie_id:
                try:
                    mid_int = int(str(movie_id))
                except ValueError:
                    mid_int = None
                if mid_int is not None:
                    env_reviews = await _call(
                        session,
                        step,
                        "get_movie_critique_reviews",
                        {"movie_id": mid_int},
                    )
                    _write(f"get_movie_critique_reviews_{mid_int}", env_reviews)
                    step += 1

            # --- 4. Events flow -------------------------------------------
            env_filters_event = await _call(
                session,
                step,
                "get_dynamic_filters_event",
                {"user_location": LOCATION},
            )
            _write("get_dynamic_filters_event", env_filters_event)
            step += 1

            env_events = await _call(
                session,
                step,
                "search_events",
                {"location": LOCATION},
            )
            _write("search_events_koramangala", env_events)
            step += 1

            env_event_venues = await _call(
                session,
                step,
                "search_event_venues",
                {"location": LOCATION},
            )
            _write("search_event_venues_koramangala", env_event_venues)
            step += 1

            env_artists = await _call(
                session,
                step,
                "search_artists",
                {"location": LOCATION},
            )
            _write("search_artists_koramangala", env_artists)
            step += 1

            print("\nDONE. Skipped (would mutate / need prior booking):")
            print("  - create_table_reservation_booking")
            print("  - get_booking_details")


if __name__ == "__main__":
    asyncio.run(main())
