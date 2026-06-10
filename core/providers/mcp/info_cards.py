"""Generic `info_card` fallback — ANY successful tool result without a
bespoke card renders a titled key-value tile, so no operation's output is
invisible in the UI (the frontend silently drops unknown attachment kinds)."""

from __future__ import annotations

from typing import Any

from core.contracts.attachment import Attachment
from core.contracts.tool_result import ToolResult

_MAX_INFO_ROWS = 10
_MAX_INFO_LINES = 8


def _prettify(name: str) -> str:
    return name.replace("_", " ").strip().title()


def info_card_payload(tool: str, envelope: dict[str, Any]) -> dict[str, Any] | None:
    """Generic titled key-value tile from any envelope (the catch-all renderer)."""
    from core.providers.mcp.digest import lean_digest

    data = envelope.get("data") if isinstance(envelope, dict) else None
    if not isinstance(data, dict) or envelope.get("is_error"):
        return None
    rows: list[dict[str, str]] = []
    for key, value in data.items():
        if isinstance(value, str | int | float | bool) and value not in ("", None):
            text = str(value)
            if text.startswith(("http://", "https://")) or len(text) > 160:
                continue
            rows.append({"label": _prettify(str(key)), "value": text})
        if len(rows) >= _MAX_INFO_ROWS:
            break
    lines: list[str] = []
    if not rows:
        digest = lean_digest(envelope)
        if digest:
            lines = digest.splitlines()[:_MAX_INFO_LINES]
    if not rows and not lines:
        return None
    payload: dict[str, Any] = {"title": _prettify(tool), "rows": rows}
    if lines:
        payload["lines"] = lines
    return payload


def build_info_cards(
    results: list[ToolResult],
    *,
    exclude: set[str],
    provider_id: str,
    brand: str,
) -> list[Attachment]:
    """`info_card` for every ok result not already covered by a bespoke card —
    the guarantee that no successful tool result is invisible in the UI."""
    out: list[Attachment] = []
    for tr in results:
        if tr.tool in exclude or not tr.ok:
            continue
        payload = info_card_payload(tr.tool, tr.data if isinstance(tr.data, dict) else {})
        if payload:
            out.append(
                Attachment(
                    kind="info_card",
                    provider=brand,
                    source=f"{provider_id}.{tr.tool}",
                    info=payload,
                )
            )
    return out
