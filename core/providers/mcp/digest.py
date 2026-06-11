"""Generic lean digest — the default narrator rendering for ANY tool envelope.

Every provider exposes read tools (order history, tracking, coupons, slots,
filters, quotes…) whose envelopes are dominated by URLs, images and deeplink
metadata. Under the narrator's raw-JSON truncation only a fragment survives, so
real data reads as "not found". This walk keeps the informative fields, drops
the noise, and bounds the output — so every tool of every provider (current
and future) yields usable facts without bespoke digest code per tool.
"""

from __future__ import annotations

import re
from typing import Any

_MAX_CHARS = 2000
_MAX_LIST_ROWS = 12
_MAX_DEPTH = 4
_MAX_VALUE_LEN = 120
_MAX_SERIES_POINTS = 8  # longer all-numeric lists are chart data, not facts

_NOISE_KEY = re.compile(
    r"(image|img|icon|logo|photo|thumbnail|banner|media|url|link|deeplink|href|"
    r"html|css|style|color|token|signature|hash|tracking_id|trackingid|"
    r"metadata|meta_data|analytics|impression)",
    re.IGNORECASE,
)
# Name-ish keys shown first so list rows lead with what identifies them.
_LEAD_KEYS = (
    "name",
    "title",
    "item_name",
    "label",
    "status",
    "state",
    "price",
    "amount",
    "total",
    "quantity",
    "rating",
    "eta",
    "date",
    "time",
    "slot",
)


def _is_noise(key: str, value: Any) -> bool:
    if _NOISE_KEY.search(key):
        return True
    return isinstance(value, str) and (
        value.startswith(("http://", "https://", "data:")) or len(value) > 400
    )


def _scalar(value: Any) -> str:
    text = str(value)
    return text if len(text) <= _MAX_VALUE_LEN else text[:_MAX_VALUE_LEN] + "…"


def _render(obj: Any, lines: list[str], indent: int, depth: int) -> None:
    pad = "  " * indent
    if isinstance(obj, dict):
        ordered = [k for k in _LEAD_KEYS if k in obj] + [k for k in obj if k not in _LEAD_KEYS]
        for key in ordered:
            value = obj[key]
            if value in (None, "", [], {}) or _is_noise(str(key), value):
                continue
            if isinstance(value, dict | list):
                if depth >= _MAX_DEPTH:
                    continue
                lines.append(f"{pad}{key}:")
                _render(value, lines, indent + 1, depth + 1)
                if lines and lines[-1] == f"{pad}{key}:":
                    lines.pop()
            else:
                lines.append(f"{pad}{key}: {_scalar(value)}")
    elif isinstance(obj, list):
        if len(obj) > _MAX_SERIES_POINTS and all(
            isinstance(x, int | float) and not isinstance(x, bool) for x in obj
        ):
            lines.append(f"{pad}{len(obj)} values: {_scalar(obj[0])} → {_scalar(obj[-1])}")
            return
        for row in obj[:_MAX_LIST_ROWS]:
            if isinstance(row, dict):
                lead = [
                    f"{k}={_scalar(row[k])}"
                    for k in _LEAD_KEYS
                    if row.get(k) not in (None, "", [], {})
                ]
                lines.append(f"{pad}- {', '.join(lead) if lead else '(row)'}")
                if depth < _MAX_DEPTH:
                    rest = {
                        k: v
                        for k, v in row.items()
                        if k not in _LEAD_KEYS and not _is_noise(str(k), v)
                    }
                    _render(rest, lines, indent + 1, depth + 1)
            elif row not in (None, ""):
                lines.append(f"{pad}- {_scalar(row)}")
        if len(obj) > _MAX_LIST_ROWS:
            lines.append(f"{pad}(+{len(obj) - _MAX_LIST_ROWS} more)")


def lean_digest(envelope: Any) -> str | None:
    """Compact, noise-free rendering of a tool envelope; None when nothing useful."""
    data = envelope.get("data") if isinstance(envelope, dict) else None
    if data in (None, "", [], {}):
        return None
    lines: list[str] = []
    _render(data, lines, 0, 0)
    if not lines:
        return None
    out = "\n".join(lines)
    return out[:_MAX_CHARS]
