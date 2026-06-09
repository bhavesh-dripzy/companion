"""Personalized hero greeting for the app's empty/landing state.

Generates one short, catchy, personalized line for the logged-in user by
feeding an LLM (DeepSeek, falling back to the auto-resolved provider) a small
context bundle:

  * who they are            — name / city / top interest, from the profile cache
  * what it's like outside   — current weather for their saved address (Open-Meteo,
                               free + keyless), looked up via the home/office lat-lng
  * when it is              — time-of-day in their locale

Everything is best-effort: a missing profile, no saved coordinates, a weather
timeout, or an LLM failure all degrade gracefully to a sensible static line so
the hero never blocks on this call.

Relocated from `app/services/hero_greeting.py` in the legacy teardown; the LLM
plumbing now runs on `core.llm` (config + async client) instead of the legacy
`food_llm_router` / `llm_config`.
"""

from __future__ import annotations

import json
import logging
import random
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# India-focused app: when we can't derive the user's timezone from their
# coordinates, assume IST so "good morning/evening" lands right.
_IST = timezone(timedelta(hours=5, minutes=30))

# Open-Meteo WMO weather codes → short human phrase.
_WEATHER_CODES: dict[int, str] = {
    0: "clear skies",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "freezing fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "rain showers",
    81: "rain showers",
    82: "heavy showers",
    85: "snow showers",
    86: "snow showers",
    95: "a thunderstorm",
    96: "a thunderstorm",
    99: "a hailstorm",
}


def _time_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def _coords_from_profile(profile: dict[str, Any]) -> tuple[float, float] | None:
    """Pull lat/lng from the saved home address, then office, if present."""
    addresses = profile.get("addresses")
    if not isinstance(addresses, dict):
        return None
    for label in ("home", "office"):
        addr = addresses.get(label)
        if isinstance(addr, dict):
            lat, lng = addr.get("lat"), addr.get("lng")
            try:
                if lat is not None and lng is not None:
                    return float(lat), float(lng)
            except (TypeError, ValueError):
                continue
    return None


def fetch_weather(lat: float, lng: float, *, timeout: float = 3.0) -> dict[str, Any] | None:
    """Current weather + local time for a coordinate via Open-Meteo (keyless).

    Returns {"temp_c": float, "condition": str, "hour": int} or None on any
    failure (network, timeout, malformed response).
    """
    query = urllib.parse.urlencode(
        {
            "latitude": f"{lat:.4f}",
            "longitude": f"{lng:.4f}",
            "current": "temperature_2m,weather_code",
            "timezone": "auto",
        }
    )
    url = f"https://api.open-meteo.com/v1/forecast?{query}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "companion/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # trusted host (open-meteo)
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        logger.debug("weather fetch failed", exc_info=True)
        return None

    current = data.get("current") or {}
    temp = current.get("temperature_2m")
    code = current.get("weather_code")
    # Local hour comes from the ISO time Open-Meteo returns in the resolved tz.
    hour = None
    iso = current.get("time")
    if isinstance(iso, str) and "T" in iso:
        try:
            hour = int(iso.split("T", 1)[1][:2])
        except (ValueError, IndexError):
            hour = None

    if temp is None:
        return None
    return {
        "temp_c": round(float(temp)),
        "condition": _WEATHER_CODES.get(int(code) if code is not None else -1, ""),
        "hour": hour,
    }


def _static_greeting(name: str | None, tod: str) -> str:
    """Deterministic fallback when the LLM is unavailable."""
    who = f", {name}" if name else ""
    base = {
        "morning": f"Good morning{who}. What shall we get done today?",
        "afternoon": f"Good afternoon{who}. What can I take off your plate?",
        "evening": f"Good evening{who}. How can I help you unwind?",
        "night": f"Still up{who}? Tell me what you need.",
    }
    return base.get(tod, f"Namaste{who}. How can I help?")


# A little variety nudge so back-to-back loads (the helper runs at a low fixed
# temperature) don't produce identical lines.
_VIBES = [
    "warm and welcoming",
    "playful and witty",
    "calm and reassuring",
    "energetic and upbeat",
    "poetic with a light Indian touch",
    "friendly and conversational",
]


def generate_greeting(
    *,
    name: str | None,
    city: str | None,
    interest: str | None,
    tod: str,
    weather: dict[str, Any] | None,
) -> str | None:
    """Ask the LLM for one short, catchy, personalized greeting line."""
    try:
        import asyncio

        from core.llm.client import complete_json_object
        from core.llm.config import resolve_llm_from_env
    except Exception:
        logger.debug("greeting LLM import failed", exc_info=True)
        return None

    resolved, err = resolve_llm_from_env(force="deepseek")
    if resolved is None:
        # DeepSeek not configured — fall back to whatever provider is available.
        resolved, err = resolve_llm_from_env()
    if resolved is None:
        logger.info("greeting: no LLM provider available (%s)", err)
        return None

    weather_str = "unknown"
    if weather:
        bits = []
        if weather.get("condition"):
            bits.append(weather["condition"])
        if weather.get("temp_c") is not None:
            bits.append(f"{weather['temp_c']}°C")
        weather_str = ", ".join(bits) if bits else "unknown"

    context = {
        "user_name": name or "(unknown)",
        "city": city or "(unknown)",
        "top_interest": interest or "(unknown)",
        "time_of_day": tod,
        "weather": weather_str,
        "vibe": random.choice(_VIBES),
    }

    system_prompt = (
        "You write a single, short, catchy welcome line for the home screen of "
        "'Companion', an everything-assistant app for India (order food & groceries, "
        "book cabs, find events, track investments, set reminders). "
        "Rules: ONE sentence, at most 14 words. Greet by first name if known. "
        "Weave in the time of day and, if known, the weather and/or their top "
        "interest, naturally and subtly (do not list facts mechanically). "
        "Sound human and inviting, never corporate. No emojis, no hashtags, no "
        "surrounding quotes. Do not use em dashes (—); use commas or other "
        "punctuation instead. Do NOT include the phrase 'From Bharat, For Bharat'. "
        'Return strict JSON: {"greeting": "<the line>"}.'
    )
    user_message = (
        "Write the greeting using this context:\n"
        + json.dumps(context, ensure_ascii=False)
        + f"\nMake the tone {context['vibe']}."
    )

    try:
        out = asyncio.run(
            complete_json_object(
                user_message=user_message,
                system_prompt=system_prompt,
                llm=resolved,
            )
        )
    except Exception:
        logger.debug("greeting LLM call failed", exc_info=True)
        return None

    line = (out or {}).get("greeting")
    if not isinstance(line, str):
        return None
    line = line.strip().strip('"').strip()
    # Guard against a model that ignores the length rule.
    if not line or len(line) > 160:
        return None
    return line


def build_hero_greeting(user_id: str) -> dict[str, Any]:
    """Top-level orchestration for the GET /api/hero/greeting endpoint."""
    profile: dict[str, Any] = {}
    try:
        from companion_api.profile_cache import ProfileCacheRepository

        profile = ProfileCacheRepository().get(user_id) or {}
    except Exception:
        logger.debug("greeting profile fetch failed", exc_info=True)
        profile = {}

    name = profile.get("name")
    first_name = name.split()[0] if isinstance(name, str) and name.strip() else None
    city = profile.get("city")
    interests = profile.get("interests")
    interest = None
    if isinstance(interests, list) and interests:
        top = interests[0]
        if isinstance(top, dict):
            interest = top.get("category")

    # Weather (and the user's local hour) from their saved coordinates.
    weather = None
    coords = _coords_from_profile(profile)
    if coords:
        weather = fetch_weather(*coords)

    if weather and weather.get("hour") is not None:
        tod = _time_of_day(int(weather["hour"]))
    else:
        tod = _time_of_day(datetime.now(_IST).hour)

    greeting = generate_greeting(
        name=first_name,
        city=city,
        interest=interest,
        tod=tod,
        weather=weather,
    )
    source = "llm"
    if not greeting:
        greeting = _static_greeting(first_name, tod)
        source = "fallback"

    return {
        "greeting": greeting,
        "source": source,
        "time_of_day": tod,
        "weather": weather,
    }
