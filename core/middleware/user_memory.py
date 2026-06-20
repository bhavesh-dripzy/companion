"""Render the user's remembered profile into a compact, prompt-ready memory block.

One generic seam shared by the planner and the narrator so the agent behaves
personally. It surfaces WHO the user is and WHAT THEY PREFER — identity fields +
durable preferences — and deliberately NOT products / interests / leads, which are
leadgen-commercial (vendor matching) rather than personalization. Generic over
fields (no hardcoded preference taxonomy); empty when nothing is known;
privacy-light (no phone / email / age). Used contextually: the stages tell the
model to personalize only when relevant, not to recite these facts back.
"""

from __future__ import annotations

from typing import Any

_SKIP = {"", "not mentioned", "none", "n/a", "unknown", "null"}
_MAX_PREFERENCES = 10


def _clean(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() in _SKIP else text


def _identity(profile: Any) -> str:
    fields = (
        ("Name", "name"),
        ("City", "city"),
        ("Occupation", "occupation"),
        ("Family", "family_composition"),
        ("Speaks", "language"),
    )
    parts = [f"{label}: {v}" for label, attr in fields if (v := _clean(getattr(profile, attr, "")))]
    return "; ".join(parts)


def _preferences(profile: Any) -> str:
    rows = getattr(profile, "preferences", None) or []
    seen: set[str] = set()
    out: list[str] = []
    for item in rows:
        pref = _clean(item)
        key = pref.lower()
        if pref and key not in seen:
            seen.add(key)
            out.append(pref)
    return "; ".join(out[:_MAX_PREFERENCES])


def render_user_memory(profile: Any) -> str:
    """Compact memory block for prompts; '' when nothing personal is known."""
    if profile is None:
        return ""
    lines: list[str] = []
    if identity := _identity(profile):
        lines.append(f"- {identity}")
    if preferences := _preferences(profile):
        lines.append(f"- Preferences: {preferences}")
    if not lines:
        return ""
    return (
        "WHAT YOU KNOW ABOUT THIS USER (use only when relevant to their request; "
        "personalize naturally and never recite this back as a list):\n" + "\n".join(lines)
    )
