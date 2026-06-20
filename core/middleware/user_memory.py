"""Render the user's remembered profile into a compact, prompt-ready memory block.

One generic seam shared by the planner and the narrator so the agent behaves
personally. It surfaces WHATEVER the profile holds — identity, weighted interests,
known leads — never a hardcoded set of fields or interest categories, so new
profile fields / interest categories appear automatically. Empty when nothing is
known; privacy-light (no phone / email / age). Used contextually: the stages tell
the model to personalize only when relevant, not to recite these facts back.

The interest shape is read defensively — production rows carry `details: [...]`
and a stringified `weight`, while the typed schema uses `detail` / float.
"""

from __future__ import annotations

from typing import Any

_SKIP = {"", "not mentioned", "none", "n/a", "unknown", "null"}
_MAX_INTERESTS = 6
_MAX_LEADS = 5


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


def _weight(item: dict[str, Any]) -> float:
    try:
        return float(item.get("weight"))
    except (TypeError, ValueError):
        return 0.0


def _interest_detail(item: dict[str, Any]) -> str:
    detail = item.get("details") if item.get("details") is not None else item.get("detail")
    if isinstance(detail, list):
        return ", ".join(d for d in (str(x).strip() for x in detail) if d)
    return _clean(detail)


def _interests(profile: Any) -> str:
    rows = [i for i in (getattr(profile, "interests", None) or []) if isinstance(i, dict)]
    rendered: list[str] = []
    for item in sorted(rows, key=_weight, reverse=True)[:_MAX_INTERESTS]:
        category, detail = _clean(item.get("category")), _interest_detail(item)
        label = f"{category}: {detail}" if category and detail else (detail or category)
        if label:
            rendered.append(label)
    return "; ".join(rendered)


def _leads(profile: Any, attr: str) -> str:
    out: list[str] = []
    for lead in (getattr(profile, attr, None) or [])[:_MAX_LEADS]:
        if not isinstance(lead, dict) or not (product := _clean(lead.get("product"))):
            continue
        category, vendor = _clean(lead.get("category")), _clean(lead.get("vendor_name"))
        label = f'{category}: "{product}"' if category else f'"{product}"'
        out.append(f"{label} → {vendor}" if vendor else label)
    return ", ".join(out)


def render_user_memory(profile: Any) -> str:
    """Compact memory block for prompts; '' when nothing personal is known."""
    if profile is None:
        return ""
    lines: list[str] = []
    if identity := _identity(profile):
        lines.append(f"- {identity}")
    if interests := _interests(profile):
        lines.append(f"- Interests / usual focus: {interests}")
    if open_leads := _leads(profile, "open_leads"):
        lines.append(f"- Open leads (already known — update, don't re-pitch): {open_leads}")
    if matched := _leads(profile, "matched_leads"):
        lines.append(f"- Matched leads (you may offer to connect them to the vendor): {matched}")
    if not lines:
        return ""
    return (
        "WHAT YOU KNOW ABOUT THIS USER (use only when relevant to their request; "
        "personalize naturally and never recite this back as a list):\n" + "\n".join(lines)
    )
